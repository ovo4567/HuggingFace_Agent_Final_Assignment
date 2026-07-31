"""Unit tests for the pure helpers in the local GAIA solver harness (text.py).

Ticket 01 (`harden-local-solver`) requires unit coverage for:
- Answer normalization (`_normalize_answer_for_submission`)
- Prompt building (`_build_cleaning_prompt`)
plus the hardening helpers introduced for the ticket: safe archive extraction
(`_safe_extract_all`) and retry-with-backoff (`_call_with_retry`).
"""

import zipfile

import pytest
import requests

import text


# ─── Answer normalization ─────────────────────────────────────────────
class TestNormalizeAnswerForSubmission:
    def test_none_becomes_empty_string(self):
        assert text._normalize_answer_for_submission(None) == ""

    def test_whitespace_is_stripped(self):
        assert text._normalize_answer_for_submission("  x  ") == "x"

    def test_reasoning_tags_are_stripped(self):
        answer = "<think>The largest city is Los Angeles.</think>Los Angeles"
        assert text._normalize_answer_for_submission(answer) == "Los Angeles"

    def test_orphaned_reasoning_tags_are_stripped(self):
        assert text._normalize_answer_for_submission("</think> 42") == "42"
        assert text._normalize_answer_for_submission("42 <think>") == "42"

    def test_final_answer_prefix_is_removed(self):
        assert text._normalize_answer_for_submission("Final Answer: 3") == "3"

    def test_uppercase_final_answer_prefix_is_removed(self):
        assert text._normalize_answer_for_submission("FINAL ANSWER: Paris") == "Paris"

    def test_answer_prefix_is_removed(self):
        assert text._normalize_answer_for_submission("Answer: 7") == "7"

    def test_spanish_respuesta_final_prefix_is_removed(self):
        assert text._normalize_answer_for_submission("Respuesta Final: Madrid") == "Madrid"

    def test_leading_prefix_only_stripped_once(self):
        assert text._normalize_answer_for_submission("Final Answer: Final Answer: 3") == "Final Answer: 3"

    def test_formatting_marks_are_stripped(self):
        assert text._normalize_answer_for_submission("**42**") == "42"
        assert text._normalize_answer_for_submission("`3, 4, 5`") == "3, 4, 5"

    def test_comma_separated_list_is_preserved(self):
        assert text._normalize_answer_for_submission("3, 4, 5") == "3, 4, 5"


# ─── Prompt building ──────────────────────────────────────────────────
class TestBuildCleaningPrompt:
    def test_interpolates_question_and_answer(self):
        prompt = text._build_cleaning_prompt("What is 2+2?", "4")
        assert "Question: What is 2+2?" in prompt
        assert "Initial Answer: 4" in prompt
        assert "{question}" not in prompt
        assert "{answer}" not in prompt

    def test_braces_in_answer_do_not_crash(self):
        prompt = text._build_cleaning_prompt("What is the set?", "{1, 2, 3}")
        assert "Initial Answer: {1, 2, 3}" in prompt

    def test_braces_in_question_do_not_crash(self):
        prompt = text._build_cleaning_prompt("Solve f(x) = {x} for x", "5")
        assert "Solve f(x) = {x} for x" in prompt
        assert "Initial Answer: 5" in prompt

    def test_both_braces_and_answer_placeholder_safe(self):
        prompt = text._build_cleaning_prompt("f({x})", "{answer}")
        assert "Initial Answer: {answer}" in prompt

    def test_none_values_do_not_crash(self):
        prompt = text._build_cleaning_prompt(None, None)
        assert "Question:" in prompt
        assert "Initial Answer:" in prompt


# ─── Safe archive extraction ──────────────────────────────────────────
class TestSafeExtractAll:
    def test_extracts_normal_members(self, tmp_path):
        zip_path = tmp_path / "ok.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("hello.txt", "hello")
            zf.writestr("sub/nested.txt", "nested")
        out = tmp_path / "out"
        with zipfile.ZipFile(zip_path) as zf:
            extracted, skipped = text._safe_extract_all(zf, str(out))
        assert extracted == ["hello.txt", "sub/nested.txt"]
        assert skipped == []
        assert (out / "hello.txt").read_text() == "hello"
        assert (out / "sub" / "nested.txt").read_text() == "nested"

    def test_rejects_parent_directory_traversal(self, tmp_path):
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../evil.txt", "evil")
        out = tmp_path / "out"
        with zipfile.ZipFile(zip_path) as zf:
            extracted, skipped = text._safe_extract_all(zf, str(out))
        assert extracted == []
        assert skipped == ["../evil.txt"]
        assert not (tmp_path / "evil.txt").exists()

    def test_rejects_absolute_path_member(self, tmp_path):
        zip_path = tmp_path / "abs.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("/tmp/abs_evil.txt", "bad")
        out = tmp_path / "out"
        with zipfile.ZipFile(zip_path) as zf:
            extracted, skipped = text._safe_extract_all(zf, str(out))
        assert extracted == []
        assert "/tmp/abs_evil.txt" in skipped
        assert not (out / "tmp" / "abs_evil.txt").exists()

    def test_deep_traversal_never_escapes_extract_dir(self, tmp_path):
        zip_path = tmp_path / "deep.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("a/b/../../outside.txt", "nope")
        out = tmp_path / "out"
        with zipfile.ZipFile(zip_path) as zf:
            extracted, skipped = text._safe_extract_all(zf, str(out))
        assert extracted == []
        assert "a/b/../../outside.txt" in skipped
        assert not (tmp_path / "outside.txt").exists()


# ─── Retry with backoff ───────────────────────────────────────────────
class TestCallWithRetry:
    def test_returns_result_when_no_error(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            return "ok"

        assert text._call_with_retry(fn, max_attempts=3, base_delay=0.0) == "ok"
        assert calls["n"] == 1

    def test_retries_transient_error_then_succeeds(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("429 rate limit")
            return "recovered"

        assert text._call_with_retry(fn, max_attempts=5, base_delay=0.0) == "recovered"
        assert calls["n"] == 3

    def test_gives_up_after_max_attempts(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise RuntimeError("connection error")

        with pytest.raises(RuntimeError):
            text._call_with_retry(fn, max_attempts=3, base_delay=0.0)
        assert calls["n"] == 3

    def test_non_transient_error_is_not_retried(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise ValueError("this is not a transient failure")

        with pytest.raises(ValueError):
            text._call_with_retry(fn, max_attempts=3, base_delay=0.0)
        assert calls["n"] == 1

    def test_retryable_exception_subtypes_are_retried(self):
        # Simulates an OpenAI-style transient error class.
        class FakeRateLimitError(Exception):
            pass

        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 2:
                raise FakeRateLimitError("rate limit exceeded")
            return "ok"

        assert text._call_with_retry(fn, max_attempts=3, base_delay=0.0) == "ok"
        assert calls["n"] == 2

    def test_permanent_http_client_error_is_not_retried(self):
        # 404 from raise_for_status() must not burn retry attempts.
        response = requests.Response()
        response.status_code = 404
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise requests.exceptions.HTTPError("404 Client Error", response=response)

        with pytest.raises(requests.exceptions.HTTPError):
            text._call_with_retry(fn, max_attempts=4, base_delay=0.0)
        assert calls["n"] == 1

    def test_transient_http_server_error_is_retried(self):
        response = requests.Response()
        response.status_code = 503
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 2:
                raise requests.exceptions.HTTPError("503 Service Unavailable", response=response)
            return "ok"

        assert text._call_with_retry(fn, max_attempts=4, base_delay=0.0) == "ok"
        assert calls["n"] == 2


# ─── Worker per-Task timeout ──────────────────────────────────────────
class TestRunWithTimeout:
    def test_returns_value_when_fn_completes(self):
        assert text._run_with_timeout(lambda: 42, timeout=5.0) == 42

    def test_raises_timeout_when_fn_is_stuck(self):
        import time

        t0 = time.time()
        with pytest.raises(TimeoutError):
            text._run_with_timeout(lambda: time.sleep(30), timeout=0.1)
        # Returns promptly rather than waiting out the stuck call
        assert time.time() - t0 < 5.0

    def test_propagates_exception_from_fn(self):
        def boom():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            text._run_with_timeout(boom, timeout=5.0)


# ─── Local-file resolution ────────────────────────────────────────────
class TestGetLocalTaskFile:
    def test_exact_file_name_match(self, tmp_path, monkeypatch):
        (tmp_path / "report.pdf").write_text("x")
        monkeypatch.setattr(text, "LOCAL_FILES_DIR", str(tmp_path))
        assert text._get_local_task_file("t1", "report.pdf") == str(tmp_path / "report.pdf")

    def test_task_id_with_extension_match(self, tmp_path, monkeypatch):
        (tmp_path / "abc123.mp3").write_text("x")
        monkeypatch.setattr(text, "LOCAL_FILES_DIR", str(tmp_path))
        assert text._get_local_task_file("abc123", "some.mp3") == str(tmp_path / "abc123.mp3")

    def test_task_id_alone_match(self, tmp_path, monkeypatch):
        (tmp_path / "xyz").write_text("x")
        monkeypatch.setattr(text, "LOCAL_FILES_DIR", str(tmp_path))
        assert text._get_local_task_file("xyz", "") == str(tmp_path / "xyz")

    def test_task_id_prefix_search(self, tmp_path, monkeypatch):
        (tmp_path / "abc123-extra.png").write_text("x")
        monkeypatch.setattr(text, "LOCAL_FILES_DIR", str(tmp_path))
        result = text._get_local_task_file("abc123", "something.png")
        assert result == str(tmp_path / "abc123-extra.png")

    def test_missing_directory_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(text, "LOCAL_FILES_DIR", str(tmp_path / "does-not-exist"))
        assert text._get_local_task_file("t1", "f.pdf") == ""

    def test_no_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(text, "LOCAL_FILES_DIR", str(tmp_path))
        assert text._get_local_task_file("t1", "missing.pdf") == ""
