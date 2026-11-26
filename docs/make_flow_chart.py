import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

out = sys.argv[1]
fig, ax = plt.subplots(figsize=(16, 10), dpi=150)
ax.set_xlim(0, 160); ax.set_ylim(0, 100); ax.axis("off")
fig.patch.set_facecolor("white")

C = {"in": "#dbeafe", "run": "#fef3c7", "core": "#ede9fe", "ui": "#dcfce7", "out": "#fee2e2"}
EDGE = "#334155"

def box(x, y, w, h, title, body, color):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.5",
                                fc=color, ec=EDGE, lw=1.4))
    ax.text(x + w / 2, y + h - 2.6, title, ha="center", va="top", fontsize=10.5, weight="bold", color="#0f172a")
    ax.text(x + w / 2, y + h - 6.4, body, ha="center", va="top", fontsize=8.8, color="#1e293b", linespacing=1.4)
    return (x, y, w, h)

def arrow(a, b, label=None, start="r", end="l"):
    def pt(bx, side):
        x, y, w, h = bx
        return {"r": (x + w + 0.4, y + h / 2), "l": (x - 0.4, y + h / 2),
                "t": (x + w / 2, y + h + 0.4), "b": (x + w / 2, y - 0.4)}[side]
    p, q = pt(a, start), pt(b, end)
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=16, lw=1.6, color=EDGE))
    if label:
        vert = abs(p[0] - q[0]) < 1
        ax.text((p[0] + q[0]) / 2 + (1.2 if vert else 0), (p[1] + q[1]) / 2 + (0 if vert else 1.4), label, ha="left" if vert else "center", va="center", fontsize=8, style="italic", color="#475569")

ax.text(80, 97, "Advisor Lens: how it works", ha="center", va="top", fontsize=20, weight="bold", color="#0f172a")
ax.text(80, 92.2, "Local Streamlit dashboard that shows what Claude Code's /advisor did, what it cost, and which model each task needed",
        ha="center", va="top", fontsize=10.5, color="#475569")

launch = box(2, 64, 30, 20, "1. Launch",
             "launch_advisor_lens.bat\nreuse running server, else\nvenv + deps (once), hidden start\nopens http://localhost:8502", C["run"])
src = box(2, 34, 30, 20, "2. Input (read-only)",
          "Claude Code transcripts\n~/.claude/projects/**/*.jsonl\nnever written to", C["in"])
watch = box(2, 6, 30, 18, "Auto-refresh watcher",
            "checks file fingerprint every\n2 / 5 / 10 / 30 s; reruns\nonly when something changed", C["run"])

parse = box(42, 34, 32, 26, "3. Parse  (cc_parser.py)",
            "incremental: reads only new bytes\nSession + AdvisorCall objects\ntimeline: tool calls, messages,\nedits, subagents, errors\ndedup usage, skip corrupt lines", C["core"])
rec = box(42, 64, 32, 20, "4a. Score (recommender.py)",
          "difficulty score per session\nover / right / under-powered\nkeyword + similar-session\nrecommender for new tasks", C["core"])
sug = box(42, 6, 32, 20, "4b. Suggest  (suggest.py)",
          "task mix -> main model class\nadvisor one class above\nversion picked from your\nown track record", C["core"])

ui = box(84, 6, 36, 78, "5. Dashboard tabs",
         "", C["ui"])
tabs = [("Session view", "live terminal-style view of one session,\nfollows latest session, advisor panel"),
        ("Overview", "advisor calls, token share, result\ntypes, trigger reasons, calls per day"),
        ("Advisor calls", "every call: context, advice, errors,\nnext message; CSV export"),
        ("Model audit", "per-session difficulty and verdict;\nused-vs-enough matrix"),
        ("Recommend", "type a task, get a model tier")]
for i, (t, d) in enumerate(tabs):
    y = 65.5 - i * 14.2
    ax.add_patch(FancyBboxPatch((86.5, y), 31, 11, boxstyle="round,pad=0.3,rounding_size=1", fc="white", ec="#16a34a", lw=1))
    ax.text(102, y + 9.4, t, ha="center", va="top", fontsize=10, weight="bold", color="#14532d")
    ax.text(102, y + 6.2, d, ha="center", va="top", fontsize=7.8, color="#1e293b", linespacing=1.3)

out_box = box(128, 34, 30, 26, "6. You get",
              "what advisor did and cost\nwhy each call fired\nwhether each task used\ntoo strong / too weak a model\nwhich model to pick next", C["out"])

arrow(launch, src, "starts server", start="b", end="t")
arrow(src, parse, "JSONL lines")
arrow(watch, src, "re-scan", start="t", end="b")
arrow(parse, rec, "sessions", start="t", end="b")
arrow(parse, sug, "sessions", start="b", end="t")
arrow(parse, ui, "timeline + calls")
arrow(rec, ui, "scores")
arrow(sug, ui, "suggested models")
arrow(ui, out_box)

ax.text(80, 1.2, "Limit: Opus/Sonnet/Haiku 5.x and Fable advisors return encrypted advice (advisor_redacted_result); "
        "only older plaintext advisors show advice text.", ha="center", fontsize=8.5, color="#64748b")

fig.savefig(out, format="jpeg", pil_kwargs={"quality": 92}, bbox_inches="tight", facecolor="white")
print("saved", out)
