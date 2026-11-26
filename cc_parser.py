"""
parser.py — reads Claude Code session transcripts (JSONL) and extracts
sessions, advisor calls and token usage.

CONCEPTS
--------
1. Where transcripts live: ~/.claude/projects/<encoded-project-path>/<session-id>.jsonl
   One JSON object per line. Each line is an "event": a user turn, an
   assistant message, a tool result, a system note, etc.

2. Claude Code SPLITS one API response into several lines — typically one line
   per content block — and every line repeats the same message.id and usage.
   If you naively sum usage per line you double/triple count. So we keep the
   LAST usage seen per message.id and sum once at the end (deduplication).

3. The advisor is a *server tool*. In the assistant content it appears as:
       {"type": "server_tool_use", "name": "advisor", "id": "srvtoolu_..."}
       {"type": "advisor_tool_result", "tool_use_id": "srvtoolu_...",
        "content": {"type": "advisor_result" | "advisor_redacted_result"
                           | "advisor_tool_result_error", ...}}
   Opus 5.x / Fable advisors return advisor_redacted_result: encrypted. We can
   see THAT advice happened, its cost, and what the executor did next — never
   the advice text itself.

4. Advisor tokens are NOT in top-level usage. They sit in
   usage.iterations[] entries with type == "advisor_message".

The parser is defensive: unknown line shapes are skipped, never fatal.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DEFAULT_ROOT = Path.home() / ".claude" / "projects"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
AGENT_TOOLS = {"Agent", "Task"}   # subagent launcher (renamed Task -> Agent in newer versions)
CONTEXT_WINDOW = 6  # how many prior events to keep as "what led to the call"


@dataclass
class AdvisorCall:
    session_id: str
    project: str
    tool_use_id: str
    message_id: str | None
    timestamp: str | None
    executor_model: str | None
    is_subagent: bool
    result_type: str = "missing"          # advisor_result | advisor_redacted_result | advisor_tool_result_error | missing
    advice_text: str | None = None        # only for plaintext advisors
    error_code: str | None = None
    stop_reason: str | None = None
    advisor_model: str | None = None
    advisor_input_tokens: int = 0
    advisor_output_tokens: int = 0
    advisor_cache_read_tokens: int = 0
    context_before: list[str] = field(default_factory=list)
    executor_next: str | None = None      # first assistant text after the call
    trigger_guess: str = ""               # heuristic label

    @property
    def readable(self) -> bool:
        return self.result_type == "advisor_result"


@dataclass
class Session:
    session_id: str
    project: str
    path: str
    start: str | None = None
    end: str | None = None
    models: Counter = field(default_factory=Counter)
    first_prompt: str = ""
    user_turns: int = 0
    assistant_messages: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    files_edited: set = field(default_factory=set)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    advisor_input_tokens: int = 0
    advisor_output_tokens: int = 0
    advisor_calls: list[AdvisorCall] = field(default_factory=list)
    # --- for the terminal view
    subagents: list[dict] = field(default_factory=list)      # {id, ts, type, description, model, background, status, end_ts}
    timeline: list[tuple] = field(default_factory=list)      # (timestamp, actor, text | AdvisorCall, kind)
    tool_counts: Counter = field(default_factory=Counter)    # tool name -> calls
    tool_error_counts: Counter = field(default_factory=Counter)
    last_text: str = ""                                      # final assistant message (main thread)
    file_mtime: float = 0.0                                  # wall-clock time of the last transcript write

    @property
    def main_model(self) -> str:
        return self.models.most_common(1)[0][0] if self.models else "unknown"

    @property
    def error_rate(self) -> float:
        return self.tool_errors / self.tool_calls if self.tool_calls else 0.0


# ---------------------------------------------------------------- helpers
def _short(x: Any, n: int = 160) -> str:
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _iter_lines(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue  # partially written line from a live session
            if isinstance(obj, dict):
                yield obj


def _tool_input_summary(block: dict) -> str:
    name = block.get("name", "?")
    inp = block.get("input") or {}
    if isinstance(inp, dict):
        for key in ("command", "file_path", "pattern", "path", "url", "query", "description"):
            if key in inp:
                return f"{name}: {_short(inp[key], 120)}"
    return f"{name}: {_short(inp, 120)}"


def _user_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return None


def _guess_trigger(context: list[str], edits_so_far: int, next_text: str | None) -> str:
    """Heuristic only. The API doesn't say WHY the executor called the advisor."""
    errors = sum(1 for c in context if c.startswith("✖"))
    if errors >= 2:
        return "stuck / recurring errors"
    if edits_so_far == 0:
        return "before committing to an approach"
    if next_text and any(w in next_text.lower() for w in ("done", "complete", "all tests pass", "finished", "summary")):
        return "pre-completion check"
    return "mid-task checkpoint"


# ---------------------------------------------------------------- core
class SessionParser:
    """
    INCREMENTAL parser for one transcript.

    CONCEPT: a live session appends lines every second. Re-reading a multi-MB
    file from byte 0 every refresh is O(file) work per refresh — that lag is why
    the dashboard trailed reality. Instead we remember the byte OFFSET we've
    consumed and, on each refresh, read only the new bytes (O(new lines)).

    Two subtleties:
    - A line being written right now has no trailing newline yet. We stop at the
      last complete newline and leave the partial line for the next refresh
      (otherwise it would be parsed broken and lost forever).
    - If the file shrank (rewritten/rotated), the offset is invalid: start over.
    """
    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.size_seen = 0
        self.reset()

    def reset(self):
        p = self.path
        self.sess = Session(session_id=p.stem, project=p.parent.name, path=str(p))
        self.offset = 0
        self.usage_by_msg: dict[str, dict] = {}
        self.model_by_msg: dict[str, str] = {}
        self.events: list[str] = []
        self.pending: dict[str, AdvisorCall] = {}
        self.awaiting_next: list[AdvisorCall] = []
        self.calls_by_msg: dict[str, list[AdvisorCall]] = {}
        self.seen_text_blocks: set = set()
        self.tool_names: dict[str, str] = {}
        self.agents: dict[str, dict] = {}

    def update(self) -> Session:
        try:
            st_ = self.path.stat()
        except FileNotFoundError:
            raise
        if st_.st_size < self.offset:          # file was rewritten: start again
            self.reset()
        if st_.st_size > self.offset:
            with self.path.open("rb") as f:
                f.seek(self.offset)
                chunk = f.read()
            end = chunk.rfind(b"\n")
            if end >= 0:
                complete, self.offset = chunk[: end + 1], self.offset + end + 1
                for raw in complete.split(b"\n"):
                    raw = raw.strip()
                    if not raw or b'"type":"api-request' in raw[:400]:   # huge request blobs: never needed
                        continue
                    try:
                        obj = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if isinstance(obj, dict):
                        self.feed(obj)
                if len(self.events) > 200:
                    del self.events[:-50]
        self.sess.file_mtime = st_.st_mtime
        return self.finalize()

    def feed(self, obj: dict) -> None:
        ts = obj.get("timestamp")
        if ts:
            self.sess.start = self.sess.start or ts
            self.sess.end = ts
        if obj.get("sessionId"):
            self.sess.session_id = obj["sessionId"]
        if obj.get("cwd"):
            self.sess.project = obj["cwd"]
        is_sub = bool(obj.get("isSidechain"))
        kind = obj.get("type")
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}

        # ---------------- user lines (prompts + tool results)
        if kind == "user":
            if obj.get("isMeta"):
                return
            content = msg.get("content")
            if isinstance(content, list):
                for b in content:
                    if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                        continue
                    tid = b.get("tool_use_id")
                    if tid in self.agents and not is_sub:
                        a = self.agents[tid]
                        bg = a["background"] or "background" in _short(b.get("content"), 300).lower()
                        a["status"] = "failed" if b.get("is_error") else ("launched" if bg else "done")
                        a["end_ts"] = ts
                        label = "running in background" if a["status"] == "launched" else a["status"]
                        self.sess.timeline.append((ts, a["type"], f"{label} · {a['description']}", "agent_end"))
                    if b.get("is_error"):
                        self.sess.tool_errors += 1
                        name = self.tool_names.get(tid, "?")
                        self.sess.tool_error_counts[name] += 1
                        self.events.append("✖ tool error: " + _short(b.get("content"), 120))
                        self.sess.timeline.append((ts, name, "error · " + _short(b.get("content"), 90), "error"))
            text = _user_text(content)
            if text and not text.lstrip().startswith(("<command-", "<local-command", "<system-reminder")) and not is_sub:
                self.sess.user_turns += 1
                if not self.sess.first_prompt:
                    self.sess.first_prompt = text.strip()
                self.events.append("👤 " + _short(text, 120))
                self.sess.timeline.append((ts, "you", _short(text, 90), "user"))
            return

        # ---------------- assistant lines
        if kind != "assistant":
            return
        mid = msg.get("id") or obj.get("uuid")
        model = msg.get("model")
        if model and model != "<synthetic>":
            self.model_by_msg[mid] = model
        if isinstance(msg.get("usage"), dict):
            self.usage_by_msg[mid] = msg["usage"]          # keep latest copy

        for b in msg.get("content") or []:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text" and b.get("text", "").strip():
                key = (mid, b["text"][:80])
                if key in self.seen_text_blocks:
                    continue
                self.seen_text_blocks.add(key)
                for c in self.awaiting_next:
                    c.executor_next = b["text"].strip()
                    c.trigger_guess = _guess_trigger(c.context_before, c._edits, c.executor_next)  # type: ignore[attr-defined]
                self.awaiting_next.clear()
                self.events.append("🤖 " + _short(b["text"], 120))
                if not is_sub:
                    self.sess.timeline.append((ts, "claude", _short(b["text"], 110), "say"))
                    self.sess.last_text = b["text"].strip()
            elif t == "tool_use":
                if b.get("id") and b["id"] in self.tool_names:
                    continue  # duplicate line of the same block
                self.tool_names[b.get("id")] = b.get("name", "?")
                self.sess.tool_calls += 1
                self.sess.tool_counts[b.get("name", "?")] += 1
                inp = b.get("input") or {}
                if b.get("name") in AGENT_TOOLS and not is_sub:
                    rec = {"id": b.get("id"), "ts": ts, "type": inp.get("subagent_type") or "general-purpose",
                           "description": _short(inp.get("description") or inp.get("prompt") or "", 60),
                           "model": inp.get("model"), "background": bool(inp.get("run_in_background")),
                           "status": "running", "end_ts": None}
                    self.agents[b.get("id")] = rec
                    self.sess.subagents.append(rec)
                    self.sess.timeline.append((ts, rec["type"], f"launched · {rec['description']}", "agent_start"))
                elif b.get("name") in EDIT_TOOLS:
                    self.sess.timeline.append((ts, b["name"].lower(), _short(inp.get("file_path") or inp.get("notebook_path") or "", 70), "edit"))
                if b.get("name") in EDIT_TOOLS:
                    fp = (b.get("input") or {}).get("file_path") or (b.get("input") or {}).get("notebook_path")
                    if fp:
                        self.sess.files_edited.add(fp)
                self.events.append("🔧 " + _tool_input_summary(b))
                if b.get("name") not in EDIT_TOOLS and b.get("name") not in AGENT_TOOLS:
                    s_ = _tool_input_summary(b)
                    self.sess.timeline.append((ts, ("sub·" if is_sub else "") + b.get("name", "?"), s_.split(": ", 1)[-1], "tool"))
            elif t == "server_tool_use" and b.get("name") == "advisor":
                tid = b.get("id", f"anon-{len(self.pending)}")
                if tid in self.pending:
                    continue  # duplicate line
                call = AdvisorCall(
                    session_id=self.sess.session_id, project=self.sess.project, tool_use_id=tid,
                    message_id=mid, timestamp=ts, executor_model=model, is_subagent=is_sub,
                    context_before=self.events[-CONTEXT_WINDOW:],
                )
                call._edits = len(self.sess.files_edited)  # type: ignore[attr-defined]
                call.trigger_guess = _guess_trigger(call.context_before, call._edits, None)  # type: ignore[attr-defined]
                self.pending[tid] = call
                self.awaiting_next.append(call)
                self.calls_by_msg.setdefault(mid, []).append(call)
                self.sess.advisor_calls.append(call)
                self.events.append("🧭 advisor consulted")
                self.sess.timeline.append((ts, "advisor", call, "advisor"))  # call object: trigger resolves later
            elif t == "advisor_tool_result":
                call = self.pending.get(b.get("tool_use_id"))
                c = b.get("content") or {}
                if call is None or not isinstance(c, dict):
                    continue
                call.result_type = c.get("type", "unknown")
                call.advice_text = c.get("text")
                call.error_code = c.get("error_code")
                call.stop_reason = c.get("stop_reason")

    def finalize(self) -> Session:
        """Recompute usage-derived totals from scratch (idempotent: safe after every update)."""
        s = self.sess
        s.assistant_messages = 0
        s.models = Counter()
        s.input_tokens = s.output_tokens = s.cache_read_tokens = s.cache_creation_tokens = 0
        s.advisor_input_tokens = s.advisor_output_tokens = 0
        for mid, u in self.usage_by_msg.items():
            s.assistant_messages += 1
            if mid in self.model_by_msg:
                s.models[self.model_by_msg[mid]] += 1
            s.input_tokens += int(u.get("input_tokens") or 0)
            s.output_tokens += int(u.get("output_tokens") or 0)
            s.cache_read_tokens += int(u.get("cache_read_input_tokens") or 0)
            s.cache_creation_tokens += int(u.get("cache_creation_input_tokens") or 0)
            adv_iters = [i for i in (u.get("iterations") or []) if isinstance(i, dict) and i.get("type") == "advisor_message"]
            calls = self.calls_by_msg.get(mid, [])
            for idx, it in enumerate(adv_iters):
                ai, ao = int(it.get("input_tokens") or 0), int(it.get("output_tokens") or 0)
                s.advisor_input_tokens += ai
                s.advisor_output_tokens += ao
                if idx < len(calls):              # iterations and calls appear in the same order
                    calls[idx].advisor_model = it.get("model")
                    calls[idx].advisor_input_tokens = ai
                    calls[idx].advisor_output_tokens = ao
                    calls[idx].advisor_cache_read_tokens = int(it.get("cache_read_input_tokens") or 0)

        for c in s.advisor_calls:
            c.project = s.project
            c.session_id = s.session_id
        return s


_PARSERS: dict[str, SessionParser] = {}     # lives for the whole server process


def parse_session(path: Path) -> Session:
    """Incremental: first call reads the whole file, later calls read only what was appended."""
    key = str(path)
    if key not in _PARSERS:
        _PARSERS[key] = SessionParser(path)
    return _PARSERS[key].update()


def find_transcripts(root: Path = DEFAULT_ROOT) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)


def parse_all(root: Path = DEFAULT_ROOT, limit: int | None = None) -> list[Session]:
    out = []
    for p in find_transcripts(root)[:limit]:
        try:
            s = parse_session(p)
        except Exception as e:  # never let one bad file kill the dashboard
            print(f"[advisor-lens] skipped {p}: {e}")
            continue
        if s.assistant_messages or s.user_turns:
            out.append(s)
    return out
