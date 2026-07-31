# 03 — Worklog capture and Answer bundle writer

**What to build:** Every local Run captures each Task's full ReAct Worklog and writes a committed Answer bundle (keyed by Task, holding the Answer and its Worklog) alongside the results CSV — so the Space can submit without re-solving and still show how each Answer was reached, and re-Runs only solve Tasks missing from the bundle.

**Blocked by:** 01 (Harden the local solver)

**Status:** resolved

- [x] After a Run, a committed Answer bundle exists with one entry per solved Task, each carrying the final Answer and a structured Worklog.
- [x] Each Worklog contains the full trace (thought / tool call / observation) plus a tool-call summary with timing.
- [x] Re-running the local solver only solves Tasks missing from the bundle, with an option to force a full re-solve.
- [x] Bundle entries are keyed by Task and consistent with the results CSV.

## Answer

Implemented in `text.py` (Worklog capture + Answer bundle writer) with unit tests in `tests/test_text.py`.

**Worklog capture**
- `GAIASolverAgent.solve()` returns `(final_answer, worklog)` (the old `__call__` delegates to it for backward compatibility). After each Task solve it serializes `agent.memory.steps` into a structured Worklog via `_serialize_worklog`.
- Each Worklog holds the **full trace** — one entry per step with `thought` (model output), `tool_calls` (name + JSON arguments), `observations`, `code_action`, `duration_sec`, and `type` (`action`/`final`) — plus a **tool-summary with timing** (`_serialize_tool_summary`: per-tool `calls`/`total_sec`/`avg_sec`, splitting step durations across the tools called in that step) and a `total_duration_sec`.
- Verified against the installed smolagents 1.26.0 memory model: the final answer is an `ActionStep` with `is_final_answer=True` + `action_output` in `memory.steps` (the `FinalAnswerStep` is only yielded, never stored), so the concluding step is captured.
- On failure `solve()` keeps the **partial trace** gathered before the error (`status: "error"` + `error` message); memory is reset at the start of each solve so a reused worker's Worklog never shows a stale trace.

**Answer bundle**
- New `_load_answer_bundle` / `_save_answer_bundle` (atomic write) / `_make_bundle_entry` helpers. The bundle is JSON keyed by `task_id`; each entry holds `{task_id, question, level, file_name, answer, worklog, timestamp}` — the spec's Answer bundle contract. Written to `ANSWER_BUNDLE_PATH` (default `answer_bundle.json`, not gitignored).

**Re-run behavior + `--force`**
- `run_pipeline_and_save_csv(force=False)` loads the bundle and only solves Tasks **missing from the bundle** (Agent never called for bundled Tasks); solved Tasks are folded into the bundle so the next Run solves strictly fewer. `--force` (new CLI flag, also `force=True` param) re-solves every Task.
- Failed Tasks (error/timeout) stay out of the bundle and are retried next Run. A `--force` re-solve that now fails a previously-bundled Task drops its stale entry. The bundle is pruned to the served Question set so it stays consistent with the results CSV.
- The results CSV gains `source` (`bundle`/`live`), `status`, and `timestamp` columns so bundle entries and CSV rows are mutually consistent; the lazy agent pool means a fully-bundled Run needs no `DEEPSEEK_API_KEY`.

**Tests (74 total, all green)** — `_serialize_worklog` (trace fields, final-step marking, tool-summary counts/timing, Task-step skipping, error field, missing-attr safety, JSON-string tool arguments, list observations), `_make_bundle_entry`/load/save round-trip (missing/corrupt → `{}`, atomic, no temp file left), and end-to-end pipeline bundling with fake Agent + fake server: first Run solves + bundles, re-Run submits from the bundle without solving, `--force` re-solves everything, failed Tasks are not bundled and are retried, `--force` failure removes stale entries, bundle pruned to served set, and `solve()` captures a partial trace on failure.
