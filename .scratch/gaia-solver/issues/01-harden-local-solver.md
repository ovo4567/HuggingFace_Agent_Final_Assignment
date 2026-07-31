# 01 — Harden the local solver

**What to build:** The local solver Run completes reliably over the full served Question set — it survives transient model/network errors via retry-with-backoff, doesn't crash when a single Task fails, never loses a worker or deadlocks, times out Tasks that get stuck, cleans Answers without crashing on braces or truncating long list Answers, and extracts archives safely. Includes unit tests for the answer-normalization and prompt-building helpers.

**Blocked by:** None — can start immediately.

**Status:** resolved

- [x] A full local Run over the served Questions completes and records an Answer for every Task.
- [x] Transient model/network failures are retried with backoff instead of failing the Task.
- [x] A stuck Task times out rather than blocking a worker indefinitely.
- [x] The worker pool never loses a worker or deadlocks, even when a Task throws.
- [x] The answer-cleaning pass handles Answers containing `{`/`}` and does not truncate long comma-separated list Answers.
- [x] Archive extraction is protected against path traversal.
- [x] Unit tests cover Answer normalization (reasoning-tag stripping, prefix removal, formatting) and prompt building.

## Answer

Implemented in `text.py` (hardened local solver) with unit tests in `tests/test_text.py`.

**What changed**
- **Retry-with-backoff** — added `_call_with_retry` + `_is_retryable_error` (transient: OpenAI connection/timeout/rate-limit/5xx, requests connection/timeout and HTTP 5xx/429; not permanent 4xx). Applied to the answer-cleaning completion call, the `/questions` fetch, and, per model call inside the Agent, via a new `_RetryingOpenAIServerModel` subclass (retries at the step level so the ReAct memory is preserved and tool side-effects are not repeated).
- **Per-Task timeout** — new `_run_with_timeout` runs the Agent call on a short-lived daemon helper thread joined with `TASK_TIMEOUT_SEC` (`GAIA_TASK_TIMEOUT`, default 600). A wedged call raises `TimeoutError` in the worker without blocking it or the process at exit (verified empirically: a hung Task is recorded as a timeout row and the process still exits promptly).
- **No lost worker / deadlock** — `_resolve` returns the Agent to the pool in `try/finally` on success and thrown errors; on timeout the possibly-wedged Agent is discarded and replaced with a fresh one so the pool stays at full strength. `executor.shutdown(wait=True)` now completes promptly because workers enforce their own timeout.
- **Answer cleaning** — prompt built via `_build_cleaning_prompt` using string replacement (sentinels) instead of `.format()`, so Answers/Questions containing `{`/`}` no longer crash; `max_tokens` raised from 256 to `CLEANING_MAX_TOKENS` (default 1024, env-configurable) so long comma-separated list Answers are not truncated.
- **Safe archive extraction** — `extract_zip` now uses `_safe_extract_all` (never `extractall()`): rejects absolute paths and `..` traversal, realpath-checks every destination against the extraction folder, and streams members as plain files so symlinks cannot escape. Skipped unsafe entries are surfaced in the tool output.
- **Every Task recorded** — success, thrown-error, and timeout paths all produce an Answer row; progress count uses the actual submitted Task count.

**Tests (36 total, all green)** — `_normalize_answer_for_submission` (reasoning-tag stripping, prefix removal, formatting marks, None/whitespace), `_build_cleaning_prompt` (interpolation + braces safety), `_safe_extract_all` (normal + traversal/absolute/deep-traversal rejection), `_call_with_retry` (transient vs permanent classification incl. 404-not-retried / 503-retried), `_run_with_timeout` (completion, timeout, exception propagation), and `_get_local_task_file` (spec's local-file resolution).

**smolagents version verification (handoff flag)** — verified smolagents 1.26.0 `CodeAgent`: the default `code_block_tags` is `("<code>", "</code>")`, so the existing `<code>`-tag prompt instructions in `__call__` are still aligned. No prompt change needed. smolagents also retries rate-limit errors internally (up to 3 attempts, 60s base); our model wrapper adds connection/timeout/5xx coverage.

**Hygiene** — added `.gitignore` (excludes `.env`, `files/`, `gaia_results.csv`, caches, `.DS_Store`, `.vscode`); `conftest.py` at repo root enables `import text` in tests. `.env` and `files/` remain untracked; no secrets touched.
