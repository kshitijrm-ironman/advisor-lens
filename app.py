"""
Advisor Lens — see what Claude Code's advisor did, what it cost, and which
model each task actually needed.

Run:  streamlit run app.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from cc_parser import DEFAULT_ROOT, find_transcripts, parse_all, parse_session
from terminal_ui import _hms, pretty_model, render_session
from suggest import suggest_models
from tabs_ui import audit_html, call_detail_html, calls_table_html, overview_html, recommend_html
from recommender import TIERS, WEIGHTS, recommend_for_task, score_session

st.set_page_config(page_title="Advisor Lens", page_icon="🧭", layout="wide")


# ---------------------------------------------------------------- data loading
# No st.cache_data any more: parse_session() is incremental (remembers its byte
# offset per file), so a full refresh only reads bytes appended since the last one.
# The old cache only refreshed on a click, which is why the app trailed reality.
def fingerprint(root: Path) -> tuple:
    files = find_transcripts(root)
    return (len(files), max((f.stat().st_mtime for f in files), default=0))


# ---------------------------------------------------------------- sidebar
st.sidebar.title("🧭 Advisor Lens")
root = Path(st.sidebar.text_input("Transcript folder", str(DEFAULT_ROOT)))
refresh_s = st.sidebar.select_slider("Auto-refresh", options=["off", 2, 5, 10, 30], value=5,
                                     format_func=lambda v: v if v == "off" else f"{v} s",
                                     help="Checks transcripts on this interval and refreshes only when something changed")
if st.sidebar.button("↻ Rescan"):
    st.rerun()
if st.sidebar.button("⏻ Close app", help="Stop the Advisor Lens server"):
    import os, threading
    st.success("Advisor Lens stopped. You can close this browser tab.")
    # Delay exit briefly so the message reaches the browser before the server dies.
    threading.Timer(0.5, lambda: os._exit(0)).start()
    st.stop()


# Change detector: a tiny fragment that wakes every N seconds, compares the
# transcript fingerprint, and triggers a full refresh ONLY when something changed.
# Idle Claude Code → no reruns; active session → page follows it.
@st.fragment(run_every=None if refresh_s == "off" else refresh_s)
def _watch(root_: str):
    fp = fingerprint(Path(root_))
    if st.session_state.get("_fp") is None:
        st.session_state["_fp"] = fp
    elif fp != st.session_state["_fp"]:
        st.session_state["_fp"] = fp
        st.rerun()


with st.sidebar:
    _watch(str(root))

st.session_state["_fp"] = fingerprint(root)
sessions = parse_all(root)
if not sessions:
    st.warning(f"No transcripts found under `{root}`. Claude Code writes them to `~/.claude/projects/`.")
    st.stop()

projects = sorted({s.project for s in sessions})
pick = st.sidebar.multiselect("Projects", projects, default=projects)
include_sub = st.sidebar.checkbox("Include subagent advisor calls", True)
sessions = [s for s in sessions if s.project in pick]
if not sessions:
    st.info("No projects selected. Pick at least one project in the sidebar.")
    st.stop()

st.sidebar.markdown("---")
st.sidebar.caption("Optional cost estimate — enter your own $/M-token rates (left at 0 = tokens only).")
rate_in = st.sidebar.number_input("Advisor input $/MTok", 0.0, step=0.5)
rate_out = st.sidebar.number_input("Advisor output $/MTok", 0.0, step=0.5)

calls = [c for s in sessions for c in s.advisor_calls if include_sub or not c.is_subagent]

tab_live, tab_over, tab_adv, tab_audit, tab_rec = st.tabs(
    ["Session view", "Overview", "Advisor calls", "Model audit (past)", "Recommend (new task)"]
)

# ---------------------------------------------------------------- terminal-style session view
with tab_live:
    # Most recently WRITTEN transcript first (file mtime), not last parsed timestamp.
    ordered = sorted(sessions, key=lambda s: s.file_mtime, reverse=True)
    FOLLOW = -1
    opts = [FOLLOW] + list(range(len(ordered)))
    pick_i = st.selectbox(
        "Session", opts,
        format_func=lambda i: ("● Follow latest session (auto-switches when a new one starts)" if i == FOLLOW else
                               f"{(ordered[i].end or '')[:10]} {_hms(ordered[i].end)[:5]} · {Path(ordered[i].project).name} · "
                               f"{pretty_model(ordered[i].main_model)} · {(ordered[i].first_prompt or '—')[:70]}"))
    chosen = ordered[0] if pick_i in (None, FOLLOW) or pick_i >= len(ordered) else ordered[pick_i]
    st.html(render_session(chosen))

# ---------------------------------------------------------------- overview
with tab_over:
    st.html(overview_html(sessions, calls, rate_in, rate_out, suggest_models(sessions)))

# ---------------------------------------------------------------- advisor calls
with tab_adv:
    st.html(calls_table_html(calls))
    if calls:
        order = sorted(calls, key=lambda c: c.timestamp or "", reverse=True)
        c1, c2 = st.columns([5, 1])
        idx = c1.selectbox("Inspect a call", range(len(order)), format_func=lambda i: (
            f"{(order[i].timestamp or '?')[:10]} {_hms(order[i].timestamp)} · {Path(order[i].project).name} · "
            f"{order[i].trigger_guess} · "
            + {"advisor_result": "plaintext", "advisor_redacted_result": "encrypted",
               "advisor_tool_result_error": "error"}.get(order[i].result_type, order[i].result_type)))
        df = pd.DataFrame([{
            "time": c.timestamp, "project": c.project, "session": c.session_id, "executor": c.executor_model,
            "advisor": c.advisor_model, "result": c.result_type, "trigger_guess": c.trigger_guess,
            "advisor_in": c.advisor_input_tokens, "advisor_out": c.advisor_output_tokens,
            "subagent": c.is_subagent, "error": c.error_code} for c in order])
        c2.download_button("Export CSV", df.to_csv(index=False), "advisor_calls.csv", width="stretch")
        st.html(call_detail_html(order[idx if idx is not None and idx < len(order) else 0]))

# ---------------------------------------------------------------- model audit
with tab_audit:
    scored = [(s, score_session(s)) for s in sessions]
    verdicts = sorted({sc.verdict for _, sc in scored})
    c1, c2 = st.columns([5, 1])
    only = c1.multiselect("Filter verdict", verdicts, placeholder="all verdicts")
    shown = [(s, sc) for s, sc in scored if not only or sc.verdict in only]
    adf = pd.DataFrame([{
        "start": s.start, "project": s.project, "session": s.session_id, "first_prompt": s.first_prompt[:200],
        "model_used": s.main_model, "tool_calls": s.tool_calls, "error_rate": round(s.error_rate, 3),
        "files_edited": len(s.files_edited), "user_turns": s.user_turns, "advisor_calls": len(s.advisor_calls),
        "score": sc.score, "enough": TIERS[sc.recommended_tier], "verdict": sc.verdict} for s, sc in shown])
    c2.download_button("Export CSV", adf.to_csv(index=False), "model_audit.csv", width="stretch")
    st.html(audit_html([s for s, _ in shown], [sc for _, sc in shown], WEIGHTS))

# ---------------------------------------------------------------- recommender
with tab_rec:
    task = st.text_area("Describe the task you're about to give Claude Code", height=110,
                        placeholder="e.g. Add AES-GCM encryption to the financials tracker sync layer and migrate existing rows")
    if task.strip():
        st.html(recommend_html(recommend_for_task(task, sessions)))
    else:
        st.caption("Type a task above. The suggestion combines risk keywords with your most similar past sessions.")
