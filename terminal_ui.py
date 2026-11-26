"""
terminal_ui.py — renders one session as a terminal-style dashboard (HTML string).

CONCEPTS
--------
- Streamlit's widgets can't produce this layout, so we emit plain HTML + CSS and
  hand it to st.html(). It's a "view layer": pure function Session -> HTML,
  no state, which makes it easy to test (render to a file, screenshot it).
- EVERY piece of transcript text goes through html.escape(). Transcripts contain
  code, prompts and tool output; an unescaped "<script>" or "<div" in a prompt
  would break or hijack the page. This is the same class of bug as SQL injection.
- Panels map to real transcript data only. The reference design had a "JEV fork
  layer" (a third-party routing model you don't run); its slot here is the
  TOOL LAYER: which tools the session leaned on and how often each one failed.
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from cc_parser import AdvisorCall, Session

IST = timezone(timedelta(hours=5, minutes=30))
E = html.escape

CSS = """
<style>
.al{--bg:#0e1116;--fg:#d7dae0;--dim:#7d8590;--line:#2a2f37;--adv:#b9a6ff;--lead:#f4a27c;--tool:#7ee2a0;--sub:#8db7ff;--bad:#ff7b72;
 background:var(--bg);color:var(--fg);font-family:'JetBrains Mono','Cascadia Code',Consolas,monospace;font-size:13px;
 border-radius:12px;padding:18px 22px 14px;line-height:1.55;border:1px solid #1c2129}
.al *{box-sizing:border-box}
.al .bar{display:flex;gap:7px;margin-bottom:6px}.al .bar i{width:11px;height:11px;border-radius:50%;display:block}
.al .ttl{text-align:center;font-weight:700;letter-spacing:.06em;font-size:15px;padding:4px 0 10px;border-bottom:2px solid var(--line)}
.al .ttl .o{color:var(--lead)}.al .ttl .b{color:var(--sub)}.al .ttl .d{color:var(--dim);font-weight:400}
.al .legend{display:flex;justify-content:center;flex-wrap:wrap;gap:26px;color:var(--dim);margin:10px 0 16px}
.al .legend span:before{content:'';display:inline-block;width:10px;height:10px;margin-right:8px;background:var(--c)}
.al .grid{display:grid;grid-template-columns:minmax(230px,300px) 1fr;gap:22px}
.al .box{border:1.5px solid var(--c);border-radius:3px;padding:12px 14px;background:#0f1319}
.al .adv{--c:var(--adv)}.al .adv h3{color:var(--adv);text-align:center;margin:0;font-size:15px}
.al .adv .sub{color:var(--adv);text-align:center;opacity:.85}
.al .trig{margin:10px 0 0;border-left:1px dashed #4a4560;padding-left:12px}
.al .trig div{margin:3px 0}.al .trig .t{color:var(--dim);font-size:11px;margin-right:6px}
.al .trig .on{background:#1d1a2b;color:#fff;font-weight:700;margin-left:-12px;padding-left:10px}
.al .k{display:flex;justify-content:space-between}.al .k b{color:var(--adv)}
.al .note{color:#c9cdd4;margin:12px 0}.al .muted{color:var(--dim)}
.al .advice{color:var(--adv);font-weight:700;word-break:break-word}
.al .right{display:flex;flex-direction:column;gap:18px}
.al .lead{--c:var(--lead);text-align:center;max-width:380px;margin:0 auto;width:100%}
.al .lead .h{color:var(--lead);font-weight:700}
.al .tl{--c:var(--tool)}.al .tl .h{display:flex;justify-content:space-between}.al .tl .h b{color:var(--tool)}
.al .row{display:grid;grid-template-columns:110px 1fr 120px;gap:10px;align-items:center;margin:4px 0}
.al .track{height:16px;background:#1b2a20;border-radius:2px;overflow:hidden}.al .fill{height:100%;background:var(--tool)}
.al .fill.w{background:var(--lead)}.al .ok{color:var(--tool)}.al .warn{color:var(--lead)}
.al .stem{width:2px;height:16px;background:var(--line);margin:-18px auto -18px}
.al .center{text-align:center}.al .center b{color:var(--sub)}
.al .subs{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px}
.al .card{--c:var(--sub);text-align:center}.al .card .n{font-weight:700;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.al .card .m{color:var(--sub)}.al .st{margin-top:4px}.al .st.done{color:var(--tool)}.al .st.fail{color:var(--bad)}.al .st.run{color:var(--sub)}
.al .ret{--c:var(--lead);text-align:center;max-width:460px;margin:0 auto;width:100%}.al .ret .h{color:var(--lead);font-weight:700}
.al .log{border:1px solid #3a3f48;border-radius:3px;margin-top:22px;padding:6px 14px 10px;position:relative}
.al .log .lt{position:absolute;top:-11px;left:30px;background:var(--bg);padding:0 10px;color:#c9cdd4}
.al .log table{width:100%;border-collapse:collapse;margin-top:6px;font-size:inherit;color:#c9cdd4}.al .log td{padding:2px 10px 2px 0;vertical-align:top}
.al .log td:first-child{color:#5a616b;white-space:nowrap;width:80px}.al .log td:nth-child(2){font-weight:700;white-space:nowrap;width:130px}
.al .status{margin-top:12px;border-top:1px solid var(--line);padding-top:8px}
.al .status .p{color:#c9cdd4}.al .status .chips{color:var(--dim);margin-top:2px}.al .status .chips b{color:#c9cdd4}
.al .empty{color:var(--dim);font-style:italic}
</style>
"""

ACTOR_COLOURS = {"advisor": "var(--adv)", "you": "#e6edf3", "error": "var(--bad)"}


def pretty_model(m: str | None) -> str:
    """'claude-sonnet-5-5' -> 'sonnet 5.5'; 'fable' -> 'fable'."""
    if not m:
        return "—"
    m = m.lower().replace("claude-", "")
    m = re.sub(r"-\d{8}$", "", m)                 # drop date suffixes
    parts = m.split("-")
    name, nums = parts[0], [p for p in parts[1:] if p.isdigit()]
    return f"{name} {'.'.join(nums)}".strip()


def _hms(ts: str | None) -> str:
    if not ts:
        return "--:--:--"
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(IST).strftime("%H:%M:%S")
    except ValueError:
        return ts[-8:]


def _dur(a: str | None, b: str | None) -> str:
    try:
        d = datetime.fromisoformat(b.replace("Z", "+00:00")) - datetime.fromisoformat(a.replace("Z", "+00:00"))
        s = int(d.total_seconds())
        return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"
    except Exception:
        return ""


def _advisor_panel(s: Session) -> str:
    calls = s.advisor_calls
    adv_model = next((c.advisor_model for c in calls if c.advisor_model), None)
    triggers = "".join(
        f'<div class="{"on" if i == len(calls) - 1 else ""}"><span class="t">{_hms(c.timestamp)}</span>'
        f'{"◆" if i == len(calls) - 1 else "◇"} {E(c.trigger_guess)}'
        f'{" · " + E(c.error_code) if c.error_code else ""}</div>'
        for i, c in enumerate(calls)
    ) or '<div class="empty">no advisor calls this session</div>'

    # Prefer the latest call that actually returned advice; a later failure shouldn't hide it.
    ok = [c for c in calls if c.result_type in ("advisor_result", "advisor_redacted_result")]
    last: AdvisorCall | None = ok[-1] if ok else (calls[-1] if calls else None)
    if last is None:
        advice = '<span class="muted">—</span>'
    elif last.readable:
        advice = f'<span class="advice">» {E(last.advice_text or "")[:220]}</span>'
    elif last.result_type == "advisor_redacted_result":
        advice = ('<span class="advice">» 🔒 encrypted</span><br><span class="muted">executor then:</span><br>'
                  f'<span class="advice">» {E((last.executor_next or "—")[:200])}</span>')
    else:
        advice = f'<span class="warn">» failed: {E(last.error_code or last.result_type)}</span>'

    tin = sum(c.advisor_input_tokens for c in calls)
    tout = sum(c.advisor_output_tokens for c in calls)
    silent = max(s.assistant_messages - len({c.message_id for c in calls}), 0)
    return f"""
    <div class="box adv">
      <h3>{E(pretty_model(adv_model).upper()) if adv_model else "ADVISOR"}</h3>
      <div class="sub">advisor · {"on call" if calls else "idle"}</div>
      <div class="trig">{triggers}</div>
      <div class="note">reads the whole session, every tool call and every result</div>
      <div class="muted">last advice:</div><div>{advice}</div>
      <div class="note"></div>
      <div class="k"><span>calls</span><b>{len(calls)}</b></div>
      <div class="k"><span>tokens read</span><b>{tin/1000:,.0f}k</b></div>
      <div class="k"><span>tokens written</span><b>{tout/1000:,.1f}k</b></div>
      <div class="note">silent on {silent} routine turn{"s" if silent != 1 else ""}</div>
      <div class="muted">never writes code itself. {E(pretty_model(s.main_model))} applies the advice.</div>
    </div>"""


def _tool_layer(s: Session) -> str:
    top = s.tool_counts.most_common(5)
    if not top:
        return '<div class="box tl"><div class="h"><b>TOOL LAYER</b></div><div class="empty">no tool calls</div></div>'
    mx = top[0][1]
    rows = ""
    for name, n in top:
        err = s.tool_error_counts.get(name, 0)
        rate = err / n if n else 0
        cls, label = ("ok", "clean") if err == 0 else ("warn", f"{rate:.0%} err")
        rows += (f'<div class="row"><span>{E(name)}</span><div class="track"><div class="fill {"w" if err else ""}" '
                 f'style="width:{max(n / mx * 100, 4):.0f}%"></div></div><span class="{cls}">{n} · {label}</span></div>')
    return f"""<div class="box tl"><div class="h"><b>TOOL LAYER · {E(pretty_model(s.main_model))}</b>
      <span class="muted">calls <b>{s.tool_calls:,}</b></span></div>{rows}
      <div class="muted" style="margin-top:6px">clean → keep on this model &nbsp; <span class="warn">err → watch</span></div></div>"""


def _subagents(s: Session) -> str:
    if not s.subagents:
        return '<div class="center muted">no subagents delegated</div>'
    cards = ""
    for a in s.subagents[-6:]:
        st = {"done": ("done", "✓ done"), "failed": ("fail", "✖ failed"), "launched": ("run", "◐ background"),
              "running": ("run", "◐ running")}.get(a["status"], ("run", a["status"]))
        dur = _dur(a["ts"], a["end_ts"]) if a["status"] in ("done", "failed") else ""
        cards += (f'<div class="box card"><div class="n" title="{E(a["description"])}">{E(a["description"] or a["type"])}</div>'
                  f'<div class="m">{E(a["model"] or "inherits model")}</div><div class="muted">{E(a["type"])}</div>'
                  f'<div class="st {st[0]}">{st[1]}{" · " + dur if dur else ""}</div></div>')
    models = sorted({a["model"] or "inherit" for a in s.subagents})
    return (f'<div class="center">delegate to subagents · <b>{E(", ".join(models))}</b></div>'
            f'<div class="subs">{cards}</div>')


def _log(s: Session, n: int = 16) -> str:
    rows = ""
    adv_default = next((c.advisor_model for c in s.advisor_calls if c.advisor_model), None)
    for ts, actor, text, kind in sorted(s.timeline, key=lambda x: x[0] or "")[-n:]:
        if kind == "advisor":
            c: AdvisorCall = text
            text = f"{c.trigger_guess} · " + ("failed: " + c.error_code if c.error_code else "reading full session")
            actor = pretty_model(c.advisor_model or adv_default) if (c.advisor_model or adv_default) else "advisor"
        colour = ACTOR_COLOURS.get("error" if kind == "error" else actor if actor in ACTOR_COLOURS else "",
                                   "var(--adv)" if kind == "advisor" else
                                   "var(--sub)" if kind.startswith("agent") else
                                   "var(--lead)" if kind == "edit" else
                                   "var(--tool)" if kind == "tool" else
                                   "#8b949e" if kind == "say" else "#c9cdd4")
        rows += f'<tr><td>{_hms(ts)}</td><td style="color:{colour}">{E(str(actor))}</td><td>{E(str(text))}</td></tr>'
    return f'<div class="log"><span class="lt">session log</span><table>{rows or "<tr><td></td><td></td><td class=empty>empty</td></tr>"}</table></div>'


def _freshness(s: Session) -> str:
    """How old is the newest transcript write? This is the honest 'is it live?' signal."""
    if not s.file_mtime:
        return ""
    age = max(time.time() - s.file_mtime, 0)
    txt = f"{age:.0f}s ago" if age < 90 else f"{age / 60:.0f} min ago" if age < 5400 else f"{age / 3600:.1f} h ago"
    col, dot = ("var(--tool)", "● LIVE") if age < 60 else ("var(--lead)", "● idle") if age < 600 else ("var(--dim)", "○ ended")
    wrote = datetime.fromtimestamp(s.file_mtime, IST).strftime("%H:%M:%S")
    return (f'<span style="color:{col};font-weight:700">{dot}</span> &nbsp; last write <b>{wrote}</b> '
            f'<span style="color:{col}">({txt})</span> &nbsp; rendered <b>{datetime.now(IST):%H:%M:%S}</b>')


def render_session(s: Session) -> str:
    main = pretty_model(s.main_model)
    adv = next((c.advisor_model for c in s.advisor_calls if c.advisor_model), None)
    sub_models = sorted({a["model"] for a in s.subagents if a["model"]})
    done = sum(a["status"] == "done" for a in s.subagents)
    proj = Path(s.project).name or s.project
    cmd = f"claude --model {main.split()[0]}" + (f" --advisor {pretty_model(adv).split()[0]}" if adv else "")
    last = (s.last_text or "—")[:160]
    return CSS + f"""
<div class="al">
  <div class="bar"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i><i style="background:#28c840"></i></div>
  <div class="ttl">CLAUDE CODE SESSION <span class="d">·</span> <span class="o">{E(main.upper())} BUILDS</span>
     {f'<span class="d">·</span> <span class="b">{E(" / ".join(m.upper() for m in sub_models))} SWARMS</span>' if sub_models else ""}</div>
  <div class="legend">
    <span style="--c:var(--lead)">{E(main)} · lead</span>
    {f'<span style="--c:var(--sub)">{E(", ".join(sub_models))} · swarm</span>' if sub_models else '<span style="--c:var(--sub)">subagents</span>'}
    <span style="--c:var(--tool)">tools · layer</span>
    <span style="--c:var(--adv)">{E(pretty_model(adv)) if adv else "no"} · advisor</span>
  </div>
  <div class="grid">
    {_advisor_panel(s)}
    <div class="right">
      <div class="box lead"><div class="h">{E(main.title())} · main session</div>
        <div>edits {len(s.files_edited)} file{"s" if len(s.files_edited) != 1 else ""} + runs {s.tool_calls:,} tool calls</div>
        <div class="muted">errors <span class="{"warn" if s.error_rate > .15 else "ok"}">{s.error_rate:.0%}</span> · {s.user_turns} prompt{"s" if s.user_turns != 1 else ""} from you</div>
        <div class="muted">{E(proj)}</div></div>
      {_tool_layer(s)}
      {_subagents(s)}
      <div class="box ret"><div class="h">back to {E(main.title())} · lead</div><div class="muted">last message:</div><div>{E(last)}</div></div>
    </div>
  </div>
  {_log(s)}
  <div class="status"><div class="p">~/{E(proj)} $ {E(cmd)}</div>
    <div class="chips" style="margin-bottom:2px">{_freshness(s)}</div>
    <div class="chips">subagents <b>[{done}/{len(s.subagents)}]</b> &nbsp; advisor <b>[{len(s.advisor_calls)} calls]</b>
      &nbsp; errors <b>[{s.tool_errors}]</b> &nbsp; started <b>{_hms(s.start)}</b> &nbsp; last <b>{_hms(s.end)}</b></div></div>
</div>"""
