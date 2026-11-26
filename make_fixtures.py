"""Generate fake Claude Code transcripts for testing:  python make_fixtures.py fixtures/"""
import json
import sys
from pathlib import Path


def line(**kw):
    return json.dumps(kw)


def assistant(mid, ts, model, blocks, usage, sid, cwd, side=False):
    # Claude Code writes one line per content block, repeating id + usage
    return [line(type="assistant", timestamp=ts, sessionId=sid, cwd=cwd, isSidechain=side,
                 message={"id": mid, "model": model, "content": [b], "usage": usage}) for b in blocks]


def user(ts, content, sid, cwd):
    return line(type="user", timestamp=ts, sessionId=sid, cwd=cwd, message={"role": "user", "content": content})


def hard_session(sid, cwd):
    L = [user("2026-11-20T10:00:00Z", "Add AES-GCM encryption to the financials tracker sync and migrate rows", sid, cwd)]
    L += assistant("m1", "2026-11-20T10:00:05Z", "claude-sonnet-5", [
        {"type": "text", "text": "I'll inspect the sync layer."},
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q"}},
    ], {"input_tokens": 1000, "output_tokens": 100}, sid, cwd)
    L.append(user("2026-11-20T10:00:10Z", [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "ImportError: cryptography"}], sid, cwd))
    # subagents: one finishes, one runs in background, one fails
    L += assistant("ma", "2026-11-20T10:00:11Z", "claude-sonnet-5", [
        {"type": "tool_use", "id": "ag1", "name": "Agent", "input": {"description": "map the sync module", "subagent_type": "Explore", "model": "haiku", "prompt": "..."}},
        {"type": "tool_use", "id": "ag2", "name": "Agent", "input": {"description": "find call sites of encrypt()", "subagent_type": "Explore", "model": "haiku", "prompt": "...", "run_in_background": True}},
        {"type": "tool_use", "id": "ag3", "name": "Task", "input": {"description": "audit migration in worktree", "subagent_type": "general-purpose", "prompt": "..."}},
    ], {"input_tokens": 900, "output_tokens": 120}, sid, cwd)
    L.append(user("2026-11-20T10:00:12Z", [{"type": "tool_result", "tool_use_id": "ag2", "content": "Agent started in background"}], sid, cwd))
    L.append(line(type="assistant", timestamp="2026-11-20T10:00:13Z", sessionId=sid, cwd=cwd, isSidechain=True,
                  message={"id": "sub1", "model": "claude-haiku-5-5", "content": [{"type": "tool_use", "id": "s1", "name": "Grep", "input": {"pattern": "encrypt("}}],
                           "usage": {"input_tokens": 300, "output_tokens": 20}}))
    L.append(user("2026-11-20T10:00:30Z", [{"type": "tool_result", "tool_use_id": "ag1", "content": "sync/ has 4 modules ..."}], sid, cwd))
    L.append(user("2026-11-20T10:00:40Z", [{"type": "tool_result", "tool_use_id": "ag3", "is_error": True, "content": "worktree creation failed"}], sid, cwd))
    L += assistant("m2", "2026-11-20T10:00:15Z", "claude-sonnet-5", [
        {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "pytest -q tests/sync"}},
    ], {"input_tokens": 1200, "output_tokens": 50}, sid, cwd)
    L.append(user("2026-11-20T10:00:20Z", [{"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": "KeyError nonce"}], sid, cwd))
    # advisor call — redacted (Opus 5.5) — while stuck
    L += assistant("m3", "2026-11-20T10:01:00Z", "claude-sonnet-5", [
        {"type": "server_tool_use", "id": "srv1", "name": "advisor", "input": {}},
        {"type": "advisor_tool_result", "tool_use_id": "srv1", "content": {"type": "advisor_redacted_result", "encrypted_content": "EqQB..."}},
        {"type": "text", "text": "Switching approach: derive a per-row nonce and store it alongside ciphertext."},
        {"type": "tool_use", "id": "t3", "name": "Edit", "input": {"file_path": "sync/crypto.py"}},
    ], {"input_tokens": 1500, "output_tokens": 400, "iterations": [
        {"type": "message", "input_tokens": 700, "output_tokens": 50},
        {"type": "advisor_message", "model": "claude-opus-5-5", "input_tokens": 9000, "output_tokens": 1200},
        {"type": "message", "input_tokens": 800, "output_tokens": 350}]}, sid, cwd)
    L.append(user("2026-11-20T10:02:00Z", "no, keep backward compat with old rows", sid, cwd))
    L += assistant("m4", "2026-11-20T10:03:00Z", "claude-sonnet-5", [
        {"type": "tool_use", "id": "t4", "name": "Write", "input": {"file_path": "migrations/0007.py"}},
        {"type": "server_tool_use", "id": "srv2", "name": "advisor", "input": {}},
        {"type": "advisor_tool_result", "tool_use_id": "srv2", "content": {"type": "advisor_tool_result_error", "error_code": "overloaded"}},
        {"type": "text", "text": "Done — all tests pass. Summary below."},
    ], {"input_tokens": 2000, "output_tokens": 300}, sid, cwd)
    return L


def easy_session(sid, cwd):
    L = [user("2026-11-21T09:00:00Z", "fix typo in README", sid, cwd)]
    L += assistant("e1", "2026-11-21T09:00:03Z", "claude-opus-5-5", [
        {"type": "tool_use", "id": "x1", "name": "Edit", "input": {"file_path": "README.md"}},
        {"type": "text", "text": "Fixed."},
    ], {"input_tokens": 500, "output_tokens": 40}, sid, cwd)
    return L


def plaintext_session(sid, cwd):
    L = [user("2026-11-22T12:00:00Z", "design the architecture for the steel invoice parser refactor", sid, cwd)]
    L += assistant("p1", "2026-11-22T12:00:05Z", "claude-sonnet-5", [
        {"type": "server_tool_use", "id": "srvp", "name": "advisor", "input": {}},
        {"type": "advisor_tool_result", "tool_use_id": "srvp", "content": {"type": "advisor_result", "text": "Split parsing from validation; use a strategy per vendor format."}},
        {"type": "text", "text": "Plan: vendor-specific parser strategies, shared validator."},
    ], {"input_tokens": 800, "output_tokens": 200, "iterations": [
        {"type": "advisor_message", "model": "claude-opus-4-8", "input_tokens": 3000, "output_tokens": 600}]}, sid, cwd)
    L.append("{not json")  # corrupt line must be skipped
    return L


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures")
    for name, fn, cwd in [("aaaa1111", hard_session, "/home/k/financials-tracker"),
                          ("bbbb2222", easy_session, "/home/k/financials-tracker"),
                          ("cccc3333", plaintext_session, "/home/k/steel-invoice-app")]:
        d = out / cwd.strip("/").replace("/", "-")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.jsonl").write_text("\n".join(fn(name, cwd)) + "\n")
    print("fixtures written to", out)
