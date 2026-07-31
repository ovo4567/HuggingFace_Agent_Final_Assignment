# 04 — Space orchestration and submission

**What to build:** Clicking "Run Evaluation & Submit All Answers" on the Space fetches the Questions, submits the bundled Answers for every Task it recognizes without calling the Agent, runs the Agent live (best-effort, text/web-only) for Tasks missing from the bundle — downloading their Attachments from the server first — and posts the submission payload to get the Benchmark results back.

**Blocked by:** 03 (Worklog capture and Answer bundle writer)

**Status:** resolved

- [x] Bundle-matched Tasks are submitted from the Answer bundle without invoking the Agent.
- [x] Unbundled Tasks invoke the live Agent; file-based Tasks have their Attachment downloaded from the server first.
- [x] The submission payload matches the server contract and the returned Benchmark results (score, correct/total, timestamp, message) are captured.
- [x] The orchestration behavior is covered by tests using a fake Agent and a fake scoring server (bundle matching, live fallback, payload and result shape).

## Answer

Implemented a new **run orchestration module** (`orchestration.py`) — the spec's single test seam — and wired it into the Space (`app.py`).

**Orchestration seam** — `orchestrate_run(questions, bundle, agent, server, *, username, agent_code) -> (payload, view)`:
- **Bundle match** → submits the bundled Answer + Worklog, the Agent is never called (ADR-0001). The bundle is authoritative for Answer, Worklog, timestamp, and Question/Level/file_name.
- **Live fallback** (best-effort, text/web-only) → for unbundled Tasks, `server.download_file(task_id, file_name)` fetches `/files/{task_id}` into a local dir first; the Agent is then called with the downloaded `file_path` and its live Worklog is recorded.
- **Payload** matches the `/submit` contract: `{username, agent_code, answers: [{task_id, submitted_answer}]}`.
- **Results view** = `{score_card, rows}`: the score card holds every `ScoreResponse` field (filled after `parse_score_response`); each row holds `{task_id, question, level, file_name, answer, source, status, timestamp, worklog}`.
- **Failed live Tasks are recorded as error rows but NOT submitted** — an `"Error: ..."` string would be scored as a guaranteed-wrong attempt (best-effort = don't crash, not submit garbage; surfaced via code review).

**`ServerAPI`** — thin client encapsulating `GET /questions`, `GET /files/{task_id}`, `POST /submit`; reuses `text._call_with_retry` for transient-network backoff on all three; `session` is injectable for tests; downloads land as `{task_id}.{ext}` so the Agent's local file lookup finds them.

**`text.py`** — `GAIASolverAgent(multimodal=False)` registers only text/web + document tools (no Qwen2-VL / faster-whisper / video frames) so the Space fallback never loads heavy local models (ADR-0002); `solve()` gained an optional `file_path` param (explicit download path wins over local lookup).

**`app.py`** — replaced the placeholder `BasicAgent` with the real flow: login check → `ServerAPI` → fetch `/questions` → load the committed Answer bundle → lazily build `GAIASolverAgent(multimodal=False)` only if Tasks are unbundled (a fully-bundled Run needs no `DEEPSEEK_API_KEY`) → `orchestrate_run` → `submit` → merge `parse_score_response` into the score card → status (score, correct/total, timestamp, message) + per-Task DataFrame. The rich results *view* (expandable Worklog) is ticket 05.

**Tests (97 total, all green)** — new `tests/test_orchestration.py` (18 tests) with a fake Agent + fake scoring server: bundle matching (Agent never called when fully/partially bundled), live fallback (download-before-solve, no download without Attachment, `"None"`-file_name not an Attachment), payload contract, results-view shape (score-card fields, per-Task rows, Worklog, source/status), ScoreResponse parsing, mixed runs (only bundled + successful live Tasks submitted; failed live Tasks surfaced as error rows), and the real `ServerAPI` HTTP parsing with a fake transport. Plus `tests/test_text.py` additions for the `multimodal` toolset flag and explicit `file_path` in `solve()`.
