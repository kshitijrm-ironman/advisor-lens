"""
tabs_ui.py — terminal-style HTML renderers for Overview, Advisor calls,
Model audit and Recommend. Same visual language as terminal_ui.py.

CONCEPTS
--------
- Separation of concerns: app.py owns *interaction* (widgets, filters, state);
  these functions own *presentation* (data -> HTML). Widgets stay native
  Streamlit because HTML injected via st.html cannot send input back to Python.
- One design system: colours are CSS variables defined once (--lead, --adv,
  --tool, --sub, --bad). Every panel reuses them, so meaning stays consistent:
  orange = main model, purple = advisor, green = healthy, blue = delegated /
  could-save, red = failure.
- The used-vs-recommended MATRIX is a confusion matrix: rows = what you ran,
  columns = what the heuristic says was enough. Diagonal = right-sized,
  above it = overspend, below it = underpowered. One glance shows the bias.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from cc_parser import AdvisorCall, Session
from recommender import THRESHOLDS, TIERS, TaskRecommendation
from terminal_ui import CSS, E, IST, _hms, pretty_model

EXTRA_CSS = """
<style>
.al .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:18px}
.al .kpi{border:1.5px solid var(--c);border-radius:3px;padding:10px 12px;background:#0f1319}
.al .kpi .v{font-size:22px;font-weight:700;color:var(--c)}.al .kpi .l{color:var(--dim);font-size:12px}
.al .two{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px}
.al .three{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-bottom:18px}
@media (max-width:900px){.al .two,.al .three{grid-template-columns:1fr}}
.al .bt{color:var(--c);font-weight:700;margin-bottom:8px;display:flex;justify-content:space-between}
.al .bt span{color:var(--dim);font-weight:400}
.al .hb{display:grid;grid-template-columns:minmax(90px,170px) 1fr 70px;gap:10px;align-items:center;margin:4px 0}
.al .hb .lbl{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.al .hb .tr{height:14px;background:#161b22;border-radius:2px;overflow:hidden}.al .hb .fl{height:100%}
.al .hb .n{text-align:right;color:#c9cdd4}
.al .spark{display:flex;align-items:flex-end;gap:4px;height:110px;padding-top:6px}
.al .spark .col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%}
.al .spark .pair{display:flex;align-items:flex-end;gap:2px;width:100%;height:100%}.al .spark .pair div{flex:1}
.al .spark .s{width:100%;background:var(--lead);opacity:.85;border-radius:2px 2px 0 0}
.al .spark .a{width:100%;background:var(--adv);border-radius:2px 2px 0 0}
.al .spark .d{font-size:10px;color:#5a616b;margin-top:3px}
.al table.t{width:100%;border-collapse:collapse;font-size:inherit;color:#c9cdd4}
.al table.t th{color:var(--dim);font-weight:400;text-align:left;border-bottom:1px solid var(--line);padding:4px 10px 4px 0}
.al table.t td{padding:4px 10px 4px 0;border-bottom:1px solid #161b22;vertical-align:top}
.al .chip{display:inline-block;border:1px solid var(--c);color:var(--c);border-radius:10px;padding:0 8px;font-size:11px;white-space:nowrap}
.al .evt{margin:3px 0;white-space:pre-wrap;word-break:break-word}
.al .mx{display:grid;grid-template-columns:150px repeat(4,1fr);gap:4px}
.al .mx div{padding:8px 6px;text-align:center;border-radius:2px}
.al .mx .h{color:var(--dim);font-size:11px;background:none}
.al .ladder{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:10px 0 16px}
.al .rung{border:1.5px solid #2a2f37;border-radius:3px;padding:10px;text-align:center;color:var(--dim)}
.al .rung.on{border-color:var(--adv);color:#fff;background:#1d1a2b;box-shadow:0 0 0 1px var(--adv) inset}
.al .rung .x{font-size:11px}
.al .meter{position:relative;height:18px;background:#161b22;border-radius:3px;margin:8px 0 22px}
.al .meter .f{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--tool),var(--lead),var(--bad))}
.al .meter .tick{position:absolute;top:-4px;width:2px;height:26px;background:#5a616b}
.al .meter .tl{position:absolute;top:26px;font-size:10px;color:#5a616b;transform:translateX(-50%)}
.al .split{display:grid;grid-template-columns:3fr 2fr;gap:18px}
@media (max-width:1000px){.al .split{grid-template-columns:1fr}}
.al .mrow{display:grid;grid-template-columns:62px 1fr 18px 1fr;gap:8px;align-items:center;margin:6px 0}
.al .mrow .role{color:var(--dim);font-size:12px}
.al .mrow .cur{color:var(--lead);font-weight:700}.al .mrow .arr{color:var(--dim);text-align:center}
.al .mrow .new{font-weight:700}
.al .mv{font-size:11px;margin:-2px 0 6px 70px}
.al .cmd{background:#07090c;border:1px solid var(--line);padding:10px 14px;border-radius:3px;color:var(--tool)}
.al .cmd:before{content:'$ ';color:var(--dim)}
</style>
"""

VERDICT_COLOUR = {
    "right-sized": "var(--tool)", "over-powered (could save)": "var(--sub)",
    "under-powered (struggled)": "var(--bad)", "lean but survived": "var(--lead)", "unknown model": "var(--dim)",
}
RESULT_LABEL = {"advisor_result": ("plaintext", "var(--tool)"), "advisor_redacted_result": ("🔒 encrypted", "var(--adv)"),
                "advisor_tool_result_error": ("error", "var(--bad)"), "missing": ("no result", "var(--dim)")}
TIER_CMD = ["claude --model haiku", "claude --model sonnet", "claude --model sonnet --advisor opus",
            "claude --model opus --advisor fable"]


# ---------------------------------------------------------------- shared pieces
def shell(title: str, accent: str, body: str, status: str = "") -> str:
    return CSS + EXTRA_CSS + f"""<div class="al">
<div class="bar"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i><i style="background:#28c840"></i></div>
<div class="ttl">ADVISOR LENS <span class="d">·</span> <span style="color:{accent}">{E(title)}</span></div>
<div style="height:16px"></div>{body}
{f'<div class="status"><div class="chips">{status}</div></div>' if status else ""}</div>"""


def kpi(label: str, value: str, colour: str) -> str:
    return f'<div class="kpi" style="--c:{colour}"><div class="v">{E(value)}</div><div class="l">{E(label)}</div></div>'


def hbars(items: list[tuple[str, float]], colour: str, fmt=lambda v: f"{v:,.0f}", colours: dict | None = None) -> str:
    if not items:
        return '<div class="empty">no data</div>'
    mx = max(v for _, v in items) or 1
    return "".join(
        f'<div class="hb"><span class="lbl" title="{E(k)}">{E(k)}</span><div class="tr"><div class="fl" '
        f'style="width:{max(v / mx * 100, 2):.0f}%;background:{(colours or {}).get(k, colour)}"></div></div>'
        f'<span class="n">{fmt(v)}</span></div>' for k, v in items)


def box(title: str, colour: str, inner: str, right: str = "") -> str:
    return (f'<div class="box" style="--c:{colour}"><div class="bt" style="--c:{colour}">{E(title)}'
            f'<span>{right}</span></div>{inner}</div>')


def _day(ts: str | None):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(IST).date()
    except Exception:
        return None


# ---------------------------------------------------------------- 1. overview
def overview_html(sessions: list[Session], calls: list[AdvisorCall], rate_in: float, rate_out: float,
                  sugg: dict | None = None) -> str:
    adv_in = sum(c.advisor_input_tokens for c in calls)
    adv_out = sum(c.advisor_output_tokens for c in calls)
    exec_out = sum(s.output_tokens for s in sessions)
    with_adv = sum(1 for s in sessions if s.advisor_calls)
    tiles = "".join([
        kpi("sessions", f"{len(sessions)}", "var(--lead)"),
        kpi("sessions with advisor", f"{with_adv} ({with_adv / max(len(sessions), 1):.0%})", "var(--adv)"),
        kpi("advisor calls", f"{len(calls)}", "var(--adv)"),
        kpi("advisor tokens in / out", f"{adv_in / 1e3:,.0f}k / {adv_out / 1e3:,.1f}k", "var(--adv)"),
        kpi("advisor share of output", f"{adv_out / max(exec_out + adv_out, 1):.0%}", "var(--sub)"),
        kpi("tool error rate", f"{sum(s.tool_errors for s in sessions) / max(sum(s.tool_calls for s in sessions), 1):.1%}", "var(--bad)"),
    ])
    if rate_in or rate_out:
        tiles += kpi("est. advisor spend (API-equiv.)", f"${adv_in / 1e6 * rate_in + adv_out / 1e6 * rate_out:,.2f}", "var(--tool)")

    # activity: last 14 days, sessions vs advisor calls
    today = datetime.now(IST).date()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    s_per = Counter(_day(s.start) for s in sessions)
    a_per = Counter(_day(c.timestamp) for c in calls)
    mx = max([s_per[d] for d in days] + [a_per[d] for d in days] + [1])
    cols = "".join(
        f'<div class="col" title="{d:%d %b}: {s_per[d]} sessions, {a_per[d]} advisor calls">'
        f'<div class="pair"><div class="s" style="height:{s_per[d] / mx * 100:.0f}%"></div>'
        f'<div class="a" style="height:{a_per[d] / mx * 100:.0f}%"></div></div>'
        f'<div class="d">{d:%d}</div></div>' for d in days)
    activity = box("ACTIVITY · last 14 days", "var(--lead)", f'<div class="spark">{cols}</div>',
                   '<span style="color:var(--lead)">■</span> sessions <span style="color:var(--adv)">■</span> advisor calls')

    results = Counter(c.result_type for c in calls)
    res_items = [(RESULT_LABEL.get(k, (k, ""))[0], v) for k, v in results.most_common()]
    res_cols = {RESULT_LABEL[k][0]: RESULT_LABEL[k][1] for k in RESULT_LABEL}
    triggers = Counter(c.trigger_guess for c in calls).most_common()
    models = Counter(pretty_model(s.main_model) for s in sessions).most_common(6)
    projects = Counter(Path(s.project).name or s.project for s in sessions).most_common(6)
    adv_proj = Counter(Path(c.project).name or c.project for c in calls)

    note = ""
    if results.get("advisor_redacted_result"):
        note = (f'<div class="log" style="margin:0 0 18px"><span class="lt">note</span><table><tr><td>🔒</td>'
                f'<td style="color:var(--adv)">encrypted</td><td>{results["advisor_redacted_result"]} of {len(calls)} '
                f'advisor results are encrypted (Opus 5.x / Fable). Shown as context → executor’s next move.</td></tr></table></div>')

    head = (models_panel(sugg) + '<div style="height:18px"></div>') if sugg else ""
    body = (f'{head}<div class="kpis">{tiles}</div>{note}{activity}<div style="height:18px"></div>'
            f'<div class="two">{box("RESULT TYPES", "var(--adv)", hbars(res_items, "var(--adv)", colours=res_cols))}'
            f'{box("WHY IT WAS CALLED · heuristic", "var(--adv)", hbars(triggers, "var(--adv)"))}</div>'
            f'<div class="two">{box("MAIN MODEL MIX · sessions", "var(--lead)", hbars(models, "var(--lead)"))}'
            f'{box("PROJECTS · sessions", "var(--tool)", hbars(projects, "var(--tool)", fmt=lambda v: f"{v:,.0f}"), "advisor calls in purple")}</div>')
    # annotate projects with advisor count
    for name, _ in projects:
        if adv_proj.get(name):
            body = body.replace(f'title="{E(name)}">{E(name)}</span>',
                                f'title="{E(name)}">{E(name)} <span style="color:var(--adv)">·{adv_proj[name]}</span></span>', 1)
    first = min((s.start for s in sessions if s.start), default=None)
    status = (f'window <b>{E(str(_day(first)))}</b> → <b>{E(str(today))}</b> &nbsp; transcripts <b>[{len(sessions)}]</b> '
              f'&nbsp; retention limits history (cleanupPeriodDays)')
    return shell("OVERVIEW", "var(--lead)", body, status)


# ---------------------------------------------------------------- 2. advisor calls
def calls_table_html(calls: list[AdvisorCall]) -> str:
    if not calls:
        return shell("ADVISOR CALLS", "var(--adv)",
                     '<div class="empty">No advisor calls yet. Enable with <code>/advisor</code> in Claude Code; '
                     'the executor decides when to consult it.</div>')
    rows = ""
    for c in sorted(calls, key=lambda c: c.timestamp or "", reverse=True)[:200]:
        lbl, col = RESULT_LABEL.get(c.result_type, (c.result_type, "var(--dim)"))
        rows += (f'<tr><td style="color:#5a616b;white-space:nowrap">{E((c.timestamp or "")[:10])} {_hms(c.timestamp)}</td>'
                 f'<td>{E(Path(c.project).name or c.project)}</td>'
                 f'<td><span style="color:var(--lead)">{E(pretty_model(c.executor_model))}</span> → '
                 f'<span style="color:var(--adv)">{E(pretty_model(c.advisor_model))}</span></td>'
                 f'<td><span class="chip" style="--c:{col}">{E(lbl)}{" · " + E(c.error_code) if c.error_code else ""}</span></td>'
                 f'<td>{E(c.trigger_guess)}{" <span class=muted>(subagent)</span>" if c.is_subagent else ""}</td>'
                 f'<td style="text-align:right;color:var(--adv)">{c.advisor_input_tokens / 1e3:,.1f}k / {c.advisor_output_tokens / 1e3:,.1f}k</td></tr>')
    tbl = ('<table class="t"><tr><th>time (IST)</th><th>project</th><th>executor → advisor</th><th>result</th>'
           f'<th>trigger (guess)</th><th style="text-align:right">tokens in / out</th></tr>{rows}</table>')
    readable = sum(c.readable for c in calls)
    status = (f'calls <b>[{len(calls)}]</b> &nbsp; readable <b>[{readable}]</b> &nbsp; encrypted '
              f'<b>[{sum(c.result_type == "advisor_redacted_result" for c in calls)}]</b> &nbsp; errors '
              f'<b>[{sum(bool(c.error_code) for c in calls)}]</b>')
    return shell("ADVISOR CALLS", "var(--adv)", box("CALL LEDGER", "var(--adv)", tbl, "newest first"), status)


def call_detail_html(c: AdvisorCall) -> str:
    ctx = "".join(f'<div class="evt">{E(e)}</div>' for e in c.context_before) or '<div class="empty">no prior events</div>'
    if c.readable:
        adv = f'<div class="advice">» {E(c.advice_text or "")}</div>'
    elif c.result_type == "advisor_redacted_result":
        adv = ('<div class="advice">» 🔒 encrypted</div><div class="muted" style="margin-top:6px">Opus 5.x / Fable advice is '
               'readable only by the executor, server-side.</div>')
    else:
        adv = f'<div class="warn">» failed: {E(c.error_code or c.result_type)}</div><div class="muted">executor continued without advice</div>'
    if c.stop_reason == "max_tokens":
        adv += '<div class="warn" style="margin-top:6px">advice truncated (max_tokens)</div>'
    meta = (f'<div class="k"><span>when</span><b>{E((c.timestamp or "")[:10])} {_hms(c.timestamp)}</b></div>'
            f'<div class="k"><span>advisor</span><b>{E(pretty_model(c.advisor_model))}</b></div>'
            f'<div class="k"><span>tokens read</span><b>{c.advisor_input_tokens:,}</b></div>'
            f'<div class="k"><span>tokens written</span><b>{c.advisor_output_tokens:,}</b></div>'
            f'<div class="k"><span>trigger (guess)</span><b>{E(c.trigger_guess)}</b></div>')
    body = (f'<div class="grid" style="grid-template-columns:minmax(230px,300px) 1fr">'
            f'<div class="box adv"><h3>{E(pretty_model(c.advisor_model).upper())}</h3><div class="sub">advisor · this call</div>'
            f'<div class="note"></div>{meta}<div class="note"></div><div class="muted">advice:</div>{adv}</div>'
            f'<div class="right">{box("WHAT LED TO THE CALL · last events", "var(--sub)", ctx)}'
            f'{box("EXECUTOR THEN · best visible trace of the advice", "var(--lead)", f"<div class=evt>{E(c.executor_next or chr(8212))}</div>")}'
            f'</div></div>')
    return CSS + EXTRA_CSS + f'<div class="al">{body}</div>'


# ---------------------------------------------------------------- 3. model audit
def audit_html(sessions: list[Session], scores: list, weights: dict) -> str:
    v = Counter(sc.verdict for sc in scores)
    tiles = "".join(kpi(k, str(v.get(k, 0)), col) for k, col in VERDICT_COLOUR.items() if k != "unknown model")

    # confusion matrix: used bucket (rows) x recommended tier (cols)
    m = Counter()
    for s, sc in zip(sessions, scores):
        if sc.used_tier < 0:
            continue
        used = 2 if (sc.used_tier == 1 and s.advisor_calls) else sc.used_tier
        m[(used, sc.recommended_tier)] += 1
    mx = max(m.values(), default=1)
    short = ["Haiku", "Sonnet", "Sonnet+adv", "Opus/Fable"]
    grid = '<div class="h">used ↓ / enough →</div>' + "".join(f'<div class="h">{t}</div>' for t in short)
    for r in range(4):
        grid += f'<div class="h" style="text-align:right">{short[r]}</div>'
        for c in range(4):
            n = m[(r, c)]
            col = "126,226,160" if r == c else ("141,183,255" if r > c else "255,123,114")
            alpha = 0.08 + 0.75 * (n / mx) if n else 0.04
            grid += f'<div style="background:rgba({col},{alpha:.2f});color:{"#fff" if n else "#3a3f48"}">{n}</div>'
    legend = ('<div class="muted" style="margin-top:8px"><span style="color:var(--tool)">■</span> right-sized '
              '<span style="color:var(--sub)">■</span> lower-left = overspend '
              '<span style="color:var(--bad)">■</span> upper-right = underpowered</div>')
    over = sum(n for (r, c), n in m.items() if r > c)
    under = sum(n for (r, c), n in m.items() if r < c)
    bias = ("you tend to <b style='color:var(--sub)'>over-provision</b>" if over > under else
            "you tend to <b style='color:var(--bad)'>under-provision</b>" if under > over else "no clear bias")
    matrix = box("USED vs ENOUGH · confusion matrix", "var(--adv)", f'<div class="mx">{grid}</div>{legend}',
                 f"over {over} · under {under}")

    wsum = sum(weights.values())
    wbars = hbars([(k, w) for k, w in weights.items()], "var(--tool)", fmt=lambda x: f"×{x:g}")
    scoring = box("SCORE INGREDIENTS · weights", "var(--tool)",
                  wbars + f'<div class="muted" style="margin-top:8px">thresholds: &lt;{THRESHOLDS[0]} Haiku · &lt;{THRESHOLDS[1]} '
                          f'Sonnet · &lt;{THRESHOLDS[2]} Sonnet+advisor · above → Opus. Heuristic: calibrate, then trust.</div>')

    rows = ""
    for s, sc in sorted(zip(sessions, scores), key=lambda x: x[0].start or "", reverse=True)[:200]:
        col = VERDICT_COLOUR.get(sc.verdict, "var(--dim)")
        rows += (f'<tr><td style="color:#5a616b;white-space:nowrap">{E((s.start or "")[:10])}</td>'
                 f'<td>{E(Path(s.project).name or s.project)}</td>'
                 f'<td title="{E(s.first_prompt)}">{E((s.first_prompt or "—")[:60])}</td>'
                 f'<td style="color:var(--lead)">{E(pretty_model(s.main_model))}{" <span style=color:var(--adv)>+adv</span>" if s.advisor_calls else ""}</td>'
                 f'<td style="min-width:110px"><div class="tr" style="height:10px;background:#161b22;border-radius:2px">'
                 f'<div style="height:100%;width:{min(sc.score / 10 * 100, 100):.0f}%;background:{col}"></div></div>'
                 f'<span class="muted">{sc.score}</span></td>'
                 f'<td style="white-space:nowrap">{E(TIERS[sc.recommended_tier].split(" (")[0])}</td><td><span class="chip" style="--c:{col}">{E(sc.verdict)}</span></td></tr>')
    tbl = ('<table class="t"><tr><th>date</th><th>project</th><th>first prompt</th><th>used</th><th>difficulty</th>'
           f'<th>enough</th><th>verdict</th></tr>{rows}</table>')
    body = (f'<div class="kpis">{tiles}</div><div class="two">{matrix}{scoring}</div>'
            f'{box("SESSIONS", "var(--lead)", tbl, f"{len(sessions)} shown")}')
    return shell("MODEL AUDIT", "var(--sub)", body, f"verdict: {bias} &nbsp; sessions <b>[{len(sessions)}]</b>")


# ---------------------------------------------------------------- 4. recommend
def recommend_html(r: TaskRecommendation) -> str:
    rungs = "".join(
        f'<div class="rung {"on" if i == r.tier else ""}"><div>{E(t.split(" (")[0])}</div>'
        f'<div class="x">{["cheap · fast", "default", "default + escalation", "max reasoning"][i]}</div></div>'
        for i, t in enumerate(TIERS))
    ticks = "".join(f'<div class="tick" style="left:{t * 10}%"></div><div class="tl" style="left:{t * 10}%">{t}</div>'
                    for t in THRESHOLDS)
    meter = f'<div class="meter"><div class="f" style="width:{min(r.score * 10, 100):.0f}%"></div>{ticks}</div>'
    chips = " ".join(f'<span class="chip" style="--c:{"var(--bad)" if w > 0 else "var(--tool)"}">{E(t)} {w:+}</span>'
                     for t, w in r.matched_terms) or '<span class="muted">no risk keywords matched</span>'
    why = "".join(f'<div class="evt">› {E(x)}</div>' for x in r.rationale)
    nb = "".join(
        f'<tr><td style="color:var(--sub)">{sim:.2f}</td><td title="{E(s.first_prompt)}">{E(s.first_prompt[:80])}</td>'
        f'<td style="color:var(--lead)">{E(pretty_model(s.main_model))}</td><td>{sc.score}</td>'
        f'<td><span class="chip" style="--c:{VERDICT_COLOUR.get(sc.verdict, "var(--dim)")}">{E(sc.verdict)}</span></td></tr>'
        for sim, s, sc in r.neighbours)
    nbt = (f'<table class="t"><tr><th>similarity</th><th>past prompt</th><th>model</th><th>score</th><th>verdict</th></tr>{nb}</table>'
           if nb else '<div class="empty">no similar past sessions — rules only</div>')
    body = (f'<div class="center" style="font-size:12px;color:var(--dim)">SUGGESTED</div>'
            f'<div class="center" style="font-size:20px;font-weight:700;color:var(--adv);margin:2px 0 6px">{E(TIERS[r.tier])}</div>'
            f'<div class="ladder">{rungs}</div>'
            f'<div class="two">{box("DIFFICULTY", "var(--lead)", meter + f"<div class=k><span>final</span><b style=color:var(--lead)>{r.score} / 10</b></div><div class=k><span>keyword score</span><b>{r.rule_score}</b></div><div class=k><span>history score</span><b>{r.knn_score if r.knn_score is not None else chr(8212)}</b></div>")}'
            f'{box("SIGNALS", "var(--bad)", chips + "<div style=height:10px></div>" + why)}</div>'
            f'{box("LAUNCH WITH", "var(--tool)", f"<div class=cmd>{E(TIER_CMD[r.tier])}</div>")}<div style="height:18px"></div>'
            f'{box("NEAREST PAST SESSIONS", "var(--sub)", nbt)}')
    return shell("RECOMMEND", "var(--adv)", body, "heuristic · tune keywords and weights in recommender.py")


# ---------------------------------------------------------------- models: active → suggested
MOVE_COLOUR = {"step down": "var(--tool)", "keep": "var(--sub)", "step up": "var(--bad)", "set": "var(--adv)"}
MOVE_ICON = {"step down": "▼ step down · saves", "keep": "● keep", "step up": "▲ step up", "set": "● set"}


def models_panel(sg: dict) -> str:
    """Full-width strip at the top of Overview: task mix, then main | advisor side by side."""
    band_col = ["var(--tool)", "var(--sub)", "var(--adv)", "var(--bad)"]
    strip = "".join(f'<div title="{E(b)}: {n}" style="flex:{n};background:{band_col[i]};height:10px"></div>'
                    for i, (b, n) in enumerate(sg["mix"]) if n)
    legend = " &nbsp; ".join(f'<span style="color:{band_col[i]}">■</span> {E(b)} <b>{n}</b>' for i, (b, n) in enumerate(sg["mix"]))
    mix = (f'<div class="muted" style="font-size:12px">your last {sg["n"]} tasks</div>'
           f'<div style="display:flex;gap:2px;border-radius:2px;overflow:hidden;margin:4px 0">{strip}</div>'
           f'<div class="muted" style="font-size:12px;margin-bottom:14px">{legend}</div>')
    cols = ""
    for role, key, colour in (("MAIN MODEL", "main", "var(--lead)"), ("ADVISOR", "advisor", "var(--adv)")):
        d = sg[key]
        mc = MOVE_COLOUR.get(d["move"], "var(--dim)")
        cols += (f'<div style="border-left:2px solid {colour};padding-left:14px">'
                 f'<div class="muted" style="font-size:11px;letter-spacing:.08em">{role}</div>'
                 f'<div style="font-size:20px;font-weight:700;margin:4px 0">'
                 f'<span style="color:{colour}">{E(d["active"])}</span> <span class="muted">→</span> '
                 f'<span style="color:{mc}">{E(d["suggested"])}</span></div>'
                 f'<div style="color:{mc};font-weight:700;margin-bottom:6px">{MOVE_ICON.get(d["move"], d["move"])}</div>'
                 f'<div class="muted" style="font-size:12px">{E(d["reason"])}</div>'
                 f'<div style="font-size:12px;color:#c9cdd4;margin-top:2px">evidence · {E(d["evidence"])}</div></div>')
    conf_c = {"low": "var(--bad)", "medium": "var(--lead)", "high": "var(--tool)"}[sg["confidence"]]
    cmd = f'/model {sg["main"]["cli"]}   ·   /advisor {sg["advisor"]["cli"]}'
    inner = (mix + f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:22px">{cols}</div>'
             f'<div class="cmd" style="font-size:12px;margin-top:14px">{E(cmd)}</div>'
             f'<div class="muted" style="font-size:11px;margin-top:6px">active from {E(sg["source"])} · '
             f'versions come only from models you have run · advisor ≥ main</div>')
    return box("SUGGESTED MODELS · active → suggested", "var(--adv)", inner,
               f'<span class="chip" style="--c:{conf_c}">{sg["confidence"]} confidence</span>')
