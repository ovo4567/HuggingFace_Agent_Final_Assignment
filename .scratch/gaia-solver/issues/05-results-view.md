# 05 — Benchmark results view

**What to build:** After submission the Space shows the Benchmark results — a score card (score %, correct/total, timestamp, server message), a per-Question table of submitted Answers with Level and source (bundle/live), and an expandable per-Task Worklog (tool summary + full trace). The layout is settled with a throwaway prototype first.

**Blocked by:** 04 (Space orchestration and submission)

**Status:** resolved

- [x] A throwaway prototype settles the results-view layout before the final UI is built.
- [x] The score card shows every field returned by the server.
- [x] The table lists each Question with its Level, submitted Answer, and source (bundle/live).
- [x] Each Task's Worklog expands to show the tool summary and the full trace.

## Answer

Implemented the **Benchmark results view** with a new pure-HTML renderer (`results_view.py`) wired into the Space (`app.py`), plus a throwaway prototype that settled the layout first.

**Prototype (throwaway, settled the layout)** — `prototype/results-view.html` renders the results view in **three structurally-different variants** (A: stacked cards; B: stat grid + data table; C: master–detail), switchable via `?variant=` and a floating bottom bar (← / → keys). The winner folded into `results_view.py` is **B — Stat grid + table**: a 4-up stat grid for the score card, then a per-Question table whose Worklog cell expands in place. The full variant set is captured as the primary source on the throwaway branch **`prototype/results-view`** (out of main); `git checkout prototype/results-view` to view it. The final UI was rewritten properly (escaping, tests) rather than promoting prototype code.

**Renderer (`results_view.py`, the ticket-05 test seam)** — `render_results_view(view) -> str` builds a self-contained HTML fragment with no Gradio dependency:
- **Score card** shows every `ScoreResponse` field — score % (formatted), correct/total, timestamp, server message, and username;
- **Per-Question table** lists each Task's Level badge, Question (with `task_id`), submitted Answer + timestamp, source badge (bundle/live), and status badge;
- **Expandable per-Task Worklog** — a plain `<details>/<summary>` (no JS, so it survives Gradio's iframe CSP) showing the tool summary table (name / calls / total / avg time) and the full trace (per-step thought, tool call + JSON args, observation, duration, error).
- Every value from the server/Agent is HTML-escaped, and badge class tokens are sanitized to a CSS-safe alphabet (a crafted level/source/status cannot break out of an attribute); a failed live solve's `worklog["error"]` is surfaced.

**Wiring (`app.py`)** — replaced the plain DataFrame with a `gr.HTML` results view; `run_and_submit_all` now returns `(status_message, rendered_results_html)` (the table is still shown even when the submission itself fails).

**Tests (112 total, all green)** — new `tests/test_results_view.py` (15 tests): score card shows every server field + formatting (N/A/em-dash placeholders), table lists each Question with Level/answer/source, Worklog expands with tool summary + full trace, top-level error rendering, HTML-escaping of answers/questions/worklog fields, badge class-token injection safety, and empty-view robustness. The layout was verified live in a Gradio `gr.HTML` smoke app (CSS + `<details>` expand both render in Gradio 6).
