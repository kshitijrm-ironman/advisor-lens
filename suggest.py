"""
suggest.py — "active model → suggested model", derived ONLY from your tasks.

No model versions are hard-coded. Everything comes from two kinds of evidence:

1. TASK MIX — what your work demands.
   Every recent session already has a difficulty score and a needed tier
   (recommender.py). Counting them gives a demand profile, e.g.
   "20 routine · 8 planning · 1 hard". The main model must cover ~90 % of that
   demand (p90): enough for 9 tasks in 10; the advisor covers the 10th.
   p90, not the mean: a mean lets many easy tasks hide the few hard ones.

2. TRACK RECORD — how each model you have ACTUALLY RUN performed.
   For every model id seen in your transcripts (as main model or as advisor):
   sessions run, hardest tier handled, and how often it struggled
   (verdict "under-powered" or tool-error rate > 25 %).
   The specific version suggested is the one in the needed class with the best
   record on tasks at least that hard. If you've never run any model of that
   class, the panel names only the class and says there's no evidence —
   it won't invent a version.

Classes are ordered by capability: haiku < sonnet < opus < fable/mythos.
Claude Code's pairing rule is kept: advisor must rank at or above the main model.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cc_parser import Session
from recommender import score_session

CLASS_RANK = {"haiku": 0, "sonnet": 1, "opus": 2, "fable": 3}
CLASS_NAME = {v: k for k, v in CLASS_RANK.items()}
TIER_TO_MAIN_RANK = [0, 1, 1, 2]      # recommender tier → minimum main-model class
TASK_BANDS = ["routine", "standard", "needs escalation", "hard"]
WINDOW_DAYS = 30


def model_class(model: str | None) -> str | None:
    m = (model or "").lower()
    for c in ("haiku", "sonnet", "opus", "fable", "mythos"):
        if c in m:
            return "fable" if c == "mythos" else c
    return None


def read_settings(home: Path | None = None) -> dict:
    """Merged ~/.claude/settings.json + settings.local.json (local wins). Read-only."""
    base = (home or Path.home()) / ".claude"
    out: dict = {}
    for name in ("settings.json", "settings.local.json"):
        try:
            out.update(json.loads((base / name).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return out


def _struggled(s: Session, sc) -> bool:
    return sc.verdict.startswith("under") or s.error_rate > 0.25


def track_records(sessions: list[Session], scores: list) -> dict[str, dict]:
    """model id -> {role, n, struggled, max_tier} for every model you've actually run."""
    # struggle counts are kept PER ROLE: a model's record as advisor must not leak into its record as main
    rec: dict[str, dict] = defaultdict(lambda: {"main": 0, "advised": 0, "struggled_main": 0, "struggled_advised": 0,
                                                "max_tier_main": 0, "max_tier_advised": 0})
    for s, sc in zip(sessions, scores):
        bad = _struggled(s, sc)
        if model_class(s.main_model):
            r = rec[s.main_model]
            r["main"] += 1
            r["struggled_main"] += bad
            r["max_tier_main"] = max(r["max_tier_main"], sc.recommended_tier)
        for adv in {c.advisor_model for c in s.advisor_calls if c.advisor_model}:
            r = rec[adv]
            r["advised"] += 1
            r["struggled_advised"] += bad
            r["max_tier_advised"] = max(r["max_tier_advised"], sc.recommended_tier)
    return dict(rec)


def _best_version(cls: str, role: str, need_tier: int, records: dict) -> tuple[str | None, str]:
    """Best-performing model id of class `cls` in `role` ('main'|'advised'); evidence text."""
    from terminal_ui import pretty_model
    cands = [(m, r) for m, r in records.items() if model_class(m) == cls and r[role] > 0]
    if not cands:
        return None, f"no {cls} runs in your history yet"
    # prefer models proven on tasks this hard, then lowest struggle rate, then most runs
    cands.sort(key=lambda x: (x[1][f"max_tier_{role}"] < need_tier, x[1][f"struggled_{role}"] / x[1][role], -x[1][role]))
    m, r = cands[0]
    rate = r[f"struggled_{role}"] / r[role]
    what = "as main" if role == "main" else "as advisor"
    return m, f"{pretty_model(m)} {what}: {r[role]} session{'s' if r[role] != 1 else ''}, struggled {rate:.0%}"


def suggest_models(sessions: list[Session], settings: dict | None = None) -> dict:
    from terminal_ui import pretty_model
    settings = settings if settings is not None else read_settings()
    newest = sorted(sessions, key=lambda s: s.end or "", reverse=True)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()[:19]
    recent = [s for s in sessions if (s.start or "") >= cutoff] or sessions
    scored = [score_session(s) for s in recent]
    n = len(scored)
    records = track_records(sessions, [score_session(s) for s in sessions])  # all history for track record

    # ---- task mix (demand)
    mix = Counter(sc.recommended_tier for sc in scored)
    needed = sorted(TIER_TO_MAIN_RANK[sc.recommended_tier] for sc in scored)
    p90 = needed[min(n - 1, max(0, math.ceil(0.9 * n) - 1))] if n else 1
    hard_share = mix.get(3, 0) / n if n else 0.0
    under = sum(1 for s, sc in zip(recent, scored) if sc.verdict.startswith("under")) / n if n else 0.0
    conf = "low" if n < 10 else "medium" if n < 25 else "high"

    # ---- active (what Claude Code will use next)
    act_main_cls = model_class(settings.get("model")) or (model_class(newest[0].main_model) if newest else None)
    act_adv_src = settings.get("advisorModel") or next(
        (c.advisor_model for s in newest for c in s.advisor_calls if c.advisor_model), None)
    act_adv_cls = model_class(act_adv_src)

    def active_label(cls, role):
        if not cls:
            return "none"
        for s in newest:
            ids = [c.advisor_model for c in s.advisor_calls] if role == "advised" else [s.main_model]
            for mid in ids:
                if model_class(mid) == cls:
                    return pretty_model(mid)
        return cls

    # ---- main suggestion
    main_rank = p90
    if under >= 0.25 and act_main_cls in CLASS_RANK:
        main_rank = max(main_rank, CLASS_RANK[act_main_cls])     # you struggled: never step down
    main_rank = min(main_rank, 2)                                # fable isn't a daily driver
    main_cls = CLASS_NAME[main_rank]
    need_tier = max((t for t in mix if TIER_TO_MAIN_RANK[t] <= main_rank), default=0)
    main_ver, main_ev = _best_version(main_cls, "main", need_tier, records)

    # ---- advisor suggestion (pairing rule: advisor ≥ main)
    adv_rank = 3 if hard_share >= 0.10 else min(main_rank + 1, 3)
    adv_cls = CLASS_NAME[adv_rank]
    adv_ver, adv_ev = _best_version(adv_cls, "advised", 3, records)
    if adv_ver is None:  # a model you've run as MAIN in that class is still evidence
        adv_ver, adv_ev = _best_version(adv_cls, "main", 3, records)

    adv_calls = sum(len(s.advisor_calls) for s in recent)

    def move(cur, new_rank):
        if cur not in CLASS_RANK:
            return "set"
        d = new_rank - CLASS_RANK[cur]
        return "keep" if d == 0 else "step down" if d < 0 else "step up"

    def label(ver, cls):
        return pretty_model(ver) if ver else f"{cls} class"

    covered = sum(1 for r in needed if r <= main_rank)
    return {
        "n": n, "confidence": conf,
        "mix": [(TASK_BANDS[t], mix.get(t, 0)) for t in range(4)],
        "main": {"active": active_label(act_main_cls, "main"), "suggested": label(main_ver, main_cls),
                 "cli": main_cls, "move": move(act_main_cls, main_rank), "evidence": main_ev,
                 "reason": f"{covered} of {n} recent tasks fit {main_cls}-class; {under:.0%} were under-powered."},
        "advisor": {"active": active_label(act_adv_cls, "advised"), "suggested": label(adv_ver, adv_cls),
                    "cli": adv_cls, "move": move(act_adv_cls, adv_rank), "evidence": adv_ev,
                    "reason": (f"{hard_share:.0%} of tasks were hard"
                               + (" → keep the strongest advisor." if hard_share >= 0.10 else " → one class above the main model is enough.")
                               + f" Called {adv_calls}× (costs only when called).")},
        "source": "settings.json" if settings.get("model") or settings.get("advisorModel") else "latest session",
    }
