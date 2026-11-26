"""
recommender.py — model suggestions, two modes.

CONCEPTS
--------
A. RETROSPECTIVE: for each past session, compute a transparent "difficulty
   score" from observable signals, map it to the cheapest tier that should have
   been adequate, and compare with the tier actually used.

   Signals (all visible in transcripts):
     - tool calls          → amount of work           (log-scaled: 10→100 calls is not 10x harder)
     - files edited        → blast radius
     - tool error rate     → how often the model was wrong / environment fought back
     - extra user turns    → corrections / re-steering ("no, do it this way")
     - advisor calls       → the executor itself judged it needed help

   This is a HEURISTIC, not ground truth. A long session can be easy (bulk
   renames); a 3-turn session can be very hard (subtle concurrency bug).
   Treat the output as a prompt for your judgement, then tune WEIGHTS below.

B. PRE-TASK: for a new task description, blend
     1. keyword rules (domain risk: security, money, migrations … vs trivia), and
     2. k-nearest-neighbours over your own past first-prompts (bag-of-words
        cosine similarity) — "tasks that looked like this, how hard were they?"
   The kNN part improves as your history grows; with < 10 sessions it's noise.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from cc_parser import Session

# ---------------------------------------------------------------- tiers
TIERS = ["Haiku", "Sonnet", "Sonnet + Opus advisor", "Opus (+ Fable advisor if available)"]
THRESHOLDS = [3.0, 6.0, 8.5]
BASELINE = 3.5           # score < 3 → Haiku, < 6 → Sonnet, …

WEIGHTS = {
    "tool_calls": 1.0,      # × log2(1 + calls)
    "files": 0.3,           # × min(files, 10)
    "error_rate": 4.0,      # × error ratio (0‒1)
    "corrections": 0.4,     # × min(extra user turns, 8)
    "advisor": 0.5,         # × min(advisor calls, 5)
    "prompt_risk": 0.6,     # × keyword risk of the first prompt above baseline
}                           #   (catches short-but-hard sessions: design, security)


def model_tier(model: str) -> int:
    m = (model or "").lower()
    if "haiku" in m:
        return 0
    if "sonnet" in m:
        return 1
    if "opus" in m:
        return 3
    if "fable" in m or "mythos" in m:
        return 3
    return -1


def tier_for_score(score: float) -> int:
    for i, t in enumerate(THRESHOLDS):
        if score < t:
            return i
    return len(THRESHOLDS)


# ---------------------------------------------------------------- A. retrospective
@dataclass
class SessionScore:
    score: float
    breakdown: dict
    recommended_tier: int
    used_tier: int
    verdict: str


def score_session(s: Session) -> SessionScore:
    b = {
        "tool_calls": WEIGHTS["tool_calls"] * math.log2(1 + s.tool_calls),
        "files": WEIGHTS["files"] * min(len(s.files_edited), 10),
        "error_rate": WEIGHTS["error_rate"] * s.error_rate,
        "corrections": WEIGHTS["corrections"] * min(max(s.user_turns - 1, 0), 8),
        "advisor": WEIGHTS["advisor"] * min(len(s.advisor_calls), 5),
        "prompt_risk": WEIGHTS["prompt_risk"] * max(0.0, rule_score(s.first_prompt)[0] - BASELINE),
    }
    score = round(sum(b.values()), 2)
    rec = tier_for_score(score)
    used = model_tier(s.main_model)
    if used < 0:
        verdict = "unknown model"
    else:
        # Sonnet that actually consulted an advisor ≈ tier 2 ("Sonnet + Opus advisor")
        used_bucket = 2 if (used == 1 and s.advisor_calls) else used
        if used_bucket > rec:
            verdict = "over-powered (could save)"
        elif used_bucket < rec and s.error_rate > 0.25:
            verdict = "under-powered (struggled)"
        elif used_bucket < rec:
            verdict = "lean but survived"
        else:
            verdict = "right-sized"
    return SessionScore(score, {k: round(v, 2) for k, v in b.items()}, rec, used, verdict)


# ---------------------------------------------------------------- B. pre-task
HARD_TERMS = {
    "security": 2.0, "encrypt": 2.0, "encryption": 2.0, "auth": 1.5, "authentication": 1.5,
    "migration": 1.5, "migrate": 1.5, "schema": 1.0, "concurrency": 2.0, "race": 1.5,
    "architecture": 1.5, "architect": 1.5, "design": 0.8, "refactor": 1.2, "debug": 1.0,
    "forensic": 1.5, "tamper": 1.5, "financial": 1.0, "ledger": 1.0, "reconcile": 1.2,
    "sync": 1.2, "performance": 1.0, "optimize": 0.8, "algorithm": 1.2, "parser": 0.8,
    "multi-file": 1.0, "across": 0.5, "entire": 0.8, "codebase": 1.0, "legacy": 1.0,
    "ocr": 0.8, "pdf": 0.5, "trading": 1.2, "backtest": 1.2,
}
EASY_TERMS = {
    "rename": -1.5, "typo": -2.0, "format": -1.0, "lint": -1.0, "comment": -1.0,
    "readme": -1.2, "docstring": -1.2, "bump": -1.0, "version": -0.5, "color": -0.8,
    "css": -0.5, "label": -0.8, "text": -0.3, "simple": -1.0, "small": -0.8, "quick": -0.8,
}
STOP = set("the a an and or to of in on for with is are be this that it my me i we you can please make add use from by as at".split())
TOKEN = re.compile(r"[a-z][a-z0-9\-]+")


def _tokens(text: str) -> Counter:
    return Counter(t for t in TOKEN.findall((text or "").lower()) if t not in STOP and len(t) > 2)


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a[k] * b[k] for k in a.keys() & b.keys())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class TaskRecommendation:
    tier: int
    score: float
    rule_score: float
    knn_score: float | None
    matched_terms: list
    neighbours: list          # (similarity, session, SessionScore)
    rationale: list


def rule_score(text: str) -> tuple[float, list]:
    """Keyword risk score. Baseline 3.5 = an ordinary Sonnet task."""
    matched, rule = [], BASELINE
    for t in _tokens(text):
        w = HARD_TERMS.get(t) or EASY_TERMS.get(t)
        if w:
            rule += w
            matched.append((t, w))
    if len((text or "").split()) > 80:
        rule += 1.0                              # long specs usually = multi-part work
    return max(0.0, rule), matched


def recommend_for_task(task: str, sessions: list[Session], k: int = 5) -> TaskRecommendation:
    toks = _tokens(task)
    rule, matched = rule_score(task)

    sims = []
    for s in sessions:
        if not s.first_prompt:
            continue
        sim = _cosine(toks, _tokens(s.first_prompt))
        if sim > 0.08:
            sims.append((sim, s))
    sims.sort(key=lambda x: x[0], reverse=True)
    top = [(sim, s, score_session(s)) for sim, s in sims[:k]]

    knn = None
    if top:
        wsum = sum(sim for sim, _, _ in top)
        knn = sum(sim * sc.score for sim, _, sc in top) / wsum

    # Trust history more as evidence accumulates (max 60 % weight).
    alpha = min(0.6, 0.12 * len(top)) if knn is not None else 0.0
    final = round((1 - alpha) * rule + alpha * (knn or 0), 2)
    tier = tier_for_score(final)

    why = [f"Keyword/risk score {rule:.1f} (baseline 3.5)."]
    if matched:
        why.append("Signals: " + ", ".join(f"{t} ({w:+})" for t, w in matched))
    if knn is not None:
        why.append(f"{len(top)} similar past sessions averaged {knn:.1f}; weighted at {alpha:.0%}.")
    else:
        why.append("No similar past sessions — recommendation is rules-only.")
    if tier >= 2:
        why.append("High-stakes or multi-step: an advisor pays for itself on wrong-approach avoidance.")
    return TaskRecommendation(tier, final, round(rule, 2), None if knn is None else round(knn, 2), matched, top, why)
