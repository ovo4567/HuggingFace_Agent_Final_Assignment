"""Unit tests for the pure helpers in the local GAIA solver harness (text.py).

Ticket 01 (`harden-local-solver`) requires unit coverage for:
- Answer normalization (`_normalize_answer_for_submission`)
- Prompt building (`_build_cleaning_prompt`)
plus the hardening helpers introduced for the ticket: safe archive extraction
(`_safe_extract_all`) and retry-with-backoff (`_call_with_retry`).
"""

import os
import shutil
import subprocess
import zipfile
from types import SimpleNamespace

import pandas as pd
import pytest
import requests
from PIL import Image

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


# ─── Retrying model (ticket 01) ───────────────────────────────────────
class TestRetryingOpenAIServerModel:
    def test_generate_reaches_base_generate(self, monkeypatch):
        """Regression: generate() must actually reach the base implementation.

        Zero-arg ``super()`` inside the retry lambda raises
        ``RuntimeError: super(): no arguments`` — the ``__class__`` cell the
        zero-arg form needs is created only for the method's own code object
        and is NOT propagated into nested lambdas. The base ``generate`` is
        stubbed so the real subclass path (generate -> _call_with_retry ->
        super().generate) is exercised with no network.
        """
        sentinel = {"role": "assistant", "content": "stubbed"}

        def fake_base_generate(self, messages, **kwargs):
            return sentinel

        # Patch the BASE class method only; the subclass's own generate still
        # shadows it, so model.generate() drives the real retry path.
        monkeypatch.setattr(text.OpenAIServerModel, "generate", fake_base_generate)

        model = text._RetryingOpenAIServerModel(
            model_id="test-model",
            api_base="https://api.deepseek.com/v1",
            api_key="sk-test",
        )
        result = model.generate([{"role": "user", "content": "hi"}])
        assert result is sentinel


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


# ─── Video frame timestamps (ticket 02) ──────────────────────────────
class TestVideoFrameTimestamps:
    def test_evenly_spaced_center_timestamps(self):
        # 4 frames over 10s → centers of 4 equal slices
        assert text._video_frame_timestamps(10.0, 4) == [1.25, 3.75, 6.25, 8.75]

    def test_single_frame_is_middle_of_video(self):
        assert text._video_frame_timestamps(10.0, 1) == [5.0]

    def test_num_frames_below_one_clamps_to_one(self):
        assert text._video_frame_timestamps(10.0, 0) == [5.0]

    def test_negative_duration_returns_zero(self):
        assert text._video_frame_timestamps(-3.0, 4) == [0.0]

    def test_zero_duration_returns_zero(self):
        assert text._video_frame_timestamps(0.0, 4) == [0.0]

    def test_all_timestamps_stay_within_duration(self):
        ts = text._video_frame_timestamps(2.0, 8)
        assert len(ts) == 8
        assert all(0.0 <= t <= 2.0 for t in ts)
        # Ascending and distinct
        assert ts == sorted(ts)
        assert len(set(ts)) == 8

    def test_short_video_many_frames_never_exceeds_duration(self):
        ts = text._video_frame_timestamps(1.0, 100)
        assert all(0.0 <= t <= 1.0 for t in ts)
        assert len(ts) == 100


# ─── Audio transcription routing (ticket 02) ─────────────────────────
class TestTranscribeAudioRouting:
    def test_uses_local_model_by_default(self, monkeypatch):
        monkeypatch.setattr(text, "USE_WHISPER_API", False)
        monkeypatch.setattr(text, "_transcribe_audio_local", lambda p: "local transcript")
        monkeypatch.setattr(text, "_transcribe_audio_api", lambda p: "api transcript")
        assert text.transcribe_audio("file.mp3") == "local transcript"

    def test_uses_api_when_opt_in(self, monkeypatch):
        monkeypatch.setattr(text, "USE_WHISPER_API", True)
        monkeypatch.setattr(text, "_transcribe_audio_local", lambda p: "local transcript")
        monkeypatch.setattr(text, "_transcribe_audio_api", lambda p: "api transcript")
        assert text.transcribe_audio("file.mp3") == "api transcript"

    def test_falls_back_to_api_when_local_fails_and_key_set(self, monkeypatch):
        monkeypatch.setattr(text, "USE_WHISPER_API", False)
        monkeypatch.setattr(text, "_transcribe_audio_local", lambda p: "Error: local failed")
        monkeypatch.setattr(text, "_transcribe_audio_api", lambda p: "api transcript")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert text.transcribe_audio("file.mp3") == "api transcript"

    def test_returns_local_error_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr(text, "USE_WHISPER_API", False)
        monkeypatch.setattr(text, "_transcribe_audio_local", lambda p: "Error: local failed")
        monkeypatch.setattr(text, "_transcribe_audio_api", lambda p: "api transcript")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert text.transcribe_audio("file.mp3") == "Error: local failed"


# ─── Local device resolution (Part A: device auto-selection) ──────────
class TestIsAccelerator:
    def test_mps_is_accelerator(self):
        assert text._is_accelerator("mps") is True

    def test_cuda_is_accelerator(self):
        assert text._is_accelerator("cuda") is True

    def test_cpu_is_not_accelerator(self):
        assert text._is_accelerator("cpu") is False


class TestResolveLocalDevice:
    """The LOCAL_DEVICE resolver: explicit override vs auto probe (mps→cuda→cpu)."""

    def _fake_torch(self, mps=False, cuda=False):
        return SimpleNamespace(
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
            cuda=SimpleNamespace(is_available=lambda: cuda),
        )

    def test_explicit_cpu_is_honored(self, monkeypatch):
        monkeypatch.setattr(text, "LOCAL_DEVICE", "cpu")
        assert text._resolve_local_device() == "cpu"

    def test_explicit_mps_is_honored(self, monkeypatch):
        monkeypatch.setattr(text, "LOCAL_DEVICE", "mps")
        assert text._resolve_local_device() == "mps"

    def test_explicit_cuda_is_honored(self, monkeypatch):
        monkeypatch.setattr(text, "LOCAL_DEVICE", "cuda")
        assert text._resolve_local_device() == "cuda"

    def test_explicit_device_does_not_probe_torch(self, monkeypatch):
        # An explicit value must short-circuit before importing/probing torch
        monkeypatch.setattr(text, "LOCAL_DEVICE", "cuda")
        def boom():
            raise AssertionError("should not probe torch")
        monkeypatch.setattr(text, "_import_torch", boom)
        assert text._resolve_local_device() == "cuda"

    def test_auto_prefers_mps_when_available(self, monkeypatch):
        monkeypatch.setattr(text, "LOCAL_DEVICE", "auto")
        monkeypatch.setattr(text, "_import_torch", lambda: self._fake_torch(mps=True, cuda=True))
        assert text._resolve_local_device() == "mps"

    def test_auto_falls_back_to_cuda_when_no_mps(self, monkeypatch):
        monkeypatch.setattr(text, "LOCAL_DEVICE", "auto")
        monkeypatch.setattr(text, "_import_torch", lambda: self._fake_torch(mps=False, cuda=True))
        assert text._resolve_local_device() == "cuda"

    def test_auto_falls_back_to_cpu_when_no_accelerator(self, monkeypatch):
        monkeypatch.setattr(text, "LOCAL_DEVICE", "auto")
        monkeypatch.setattr(text, "_import_torch", lambda: self._fake_torch(mps=False, cuda=False))
        assert text._resolve_local_device() == "cpu"

    def test_auto_returns_cpu_when_torch_unavailable(self, monkeypatch):
        monkeypatch.setattr(text, "LOCAL_DEVICE", "auto")
        def boom():
            raise ImportError("no torch")
        monkeypatch.setattr(text, "_import_torch", boom)
        assert text._resolve_local_device() == "cpu"

    def test_unknown_value_treated_as_auto(self, monkeypatch):
        monkeypatch.setattr(text, "LOCAL_DEVICE", "banana")
        monkeypatch.setattr(text, "_import_torch", lambda: self._fake_torch(mps=False, cuda=True))
        assert text._resolve_local_device() == "cuda"


# ─── Whisper device clamping (Part A) ─────────────────────────────────
class TestWhisperDeviceConfig:
    """faster-whisper accepts only cpu/cuda (CTranslate2 has no MPS backend)."""

    def test_mps_is_clamped_to_cpu_int8(self):
        assert text._whisper_device_config("mps") == ("cpu", "int8")

    def test_cuda_uses_float16(self):
        assert text._whisper_device_config("cuda") == ("cuda", "float16")

    def test_cpu_uses_int8(self):
        assert text._whisper_device_config("cpu") == ("cpu", "int8")


# ─── Qwen vision load config (Part A) ─────────────────────────────────
class TestVisionLoadConfig:
    """Qwen dtype: float16 on accelerators (mps/cuda), float32 on cpu.
    device_map='auto' is used only for cuda (no transformers mps device_map)."""

    def _fake_torch(self):
        return SimpleNamespace(float16="fp16", float32="fp32")

    def test_mps_uses_float16_and_no_device_map(self):
        dtype, device_map = text._vision_load_config(self._fake_torch(), "mps")
        assert dtype == "fp16"
        assert device_map is None

    def test_cuda_uses_float16_and_auto_device_map(self):
        dtype, device_map = text._vision_load_config(self._fake_torch(), "cuda")
        assert dtype == "fp16"
        assert device_map == "auto"

    def test_cpu_uses_float32_and_no_device_map(self):
        dtype, device_map = text._vision_load_config(self._fake_torch(), "cpu")
        assert dtype == "fp32"
        assert device_map is None


# ─── Frame-count resolution (ticket 02) ───────────────────────────────
class TestResolveFrameCount:
    def test_none_uses_config(self, monkeypatch):
        monkeypatch.setattr(text, "VIDEO_FRAMES_COUNT", 4)
        assert text._resolve_frame_count(None) == 4

    def test_positive_int_is_used(self, monkeypatch):
        monkeypatch.setattr(text, "VIDEO_FRAMES_COUNT", 4)
        assert text._resolve_frame_count(6) == 6

    def test_zero_falls_back_to_config(self, monkeypatch):
        monkeypatch.setattr(text, "VIDEO_FRAMES_COUNT", 4)
        assert text._resolve_frame_count(0) == 4

    def test_non_numeric_falls_back_to_config(self, monkeypatch):
        monkeypatch.setattr(text, "VIDEO_FRAMES_COUNT", 4)
        assert text._resolve_frame_count("banana") == 4

    def test_float_string_falls_back_to_config(self, monkeypatch):
        monkeypatch.setattr(text, "VIDEO_FRAMES_COUNT", 4)
        assert text._resolve_frame_count("5.5") == 4


# ─── Video frame extraction (ticket 02) ───────────────────────────────
class TestExtractFramesFromVideo:
    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
    def test_extracts_evenly_spaced_frames_from_local_video(self, tmp_path):
        # Generate a tiny 2-second test clip without touching the network
        video_path = tmp_path / "clip.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", "testsrc=duration=2:size=64x64:rate=5",
             "-pix_fmt", "yuv420p", str(video_path)],
            check=True,
        )

        out_dir = tmp_path / "frames"
        frames = text._extract_frames_from_video(str(video_path), 2, "jobtest", str(out_dir))

        assert len(frames) == 2
        assert sorted(os.path.basename(p) for p in frames) == [
            "frame_jobtest_01.jpg",
            "frame_jobtest_02.jpg",
        ]
        for p in frames:
            assert os.path.exists(p)
            Image.open(p).verify()  # each frame is a valid image file


# ─── Worklog serialization (ticket 03) ──────────────────────────────────
class _FakeTiming:
    """Minimal stand-in for smolagents.monitoring.Timing."""

    def __init__(self, duration):
        self._duration = duration

    @property
    def duration(self):
        return self._duration


class _FakeToolCall:
    """Minimal stand-in for smolagents.memory.ToolCall."""

    def __init__(self, name, arguments=None):
        self.name = name
        self.arguments = arguments


class _FakeActionStep:
    """Minimal stand-in for smolagents.memory.ActionStep (duck-typed)."""

    def __init__(self, step_number, thought=None, tool_calls=None, observations=None,
                 code_action=None, duration=None, error=None,
                 is_final_answer=False, action_output=None):
        self.step_number = step_number
        self.model_output = thought
        self.tool_calls = tool_calls
        self.observations = observations
        self.code_action = code_action
        self.timing = _FakeTiming(duration) if duration is not None else None
        self.error = error
        self.is_final_answer = is_final_answer
        self.action_output = action_output


class _FakeTaskStep:
    """Minimal stand-in for smolagents.memory.TaskStep (no step_number)."""

    def __init__(self, task):
        self.task = task


class TestSerializeWorklog:
    def test_full_trace_fields_per_step(self):
        steps = [
            _FakeTaskStep(task="Q?"),
            _FakeActionStep(
                step_number=1,
                thought="I should read the PDF.",
                tool_calls=[_FakeToolCall("inspect_pdf", {"pdf_path": "a.pdf"})],
                observations="Page 1 contents...",
                code_action="",
                duration=2.5,
            ),
        ]
        worklog = text._serialize_worklog(steps, status="completed")
        assert worklog["status"] == "completed"
        assert worklog["total_duration_sec"] == 2.5
        assert len(worklog["steps"]) == 1
        step = worklog["steps"][0]
        assert step["step_number"] == 1
        assert step["type"] == "action"
        assert step["thought"] == "I should read the PDF."
        assert step["observations"] == "Page 1 contents..."
        assert step["code_action"] == ""
        assert step["duration_sec"] == 2.5
        assert step["tool_calls"] == [{"name": "inspect_pdf", "arguments": {"pdf_path": "a.pdf"}}]

    def test_final_answer_step_is_marked(self):
        steps = [_FakeActionStep(step_number=1, is_final_answer=True, action_output="42")]
        worklog = text._serialize_worklog(steps)
        assert worklog["steps"][0]["type"] == "final"
        assert worklog["steps"][0]["action_output"] == "42"

    def test_tool_summary_counts_and_timing(self):
        steps = [
            _FakeActionStep(1, tool_calls=[_FakeToolCall("inspect_pdf")], duration=10.0),
            _FakeActionStep(
                2,
                tool_calls=[_FakeToolCall("inspect_pdf"), _FakeToolCall("transcribe_audio")],
                duration=4.0,
            ),
        ]
        summary = text._serialize_tool_summary(steps)
        by_name = {s["name"]: s for s in summary}
        assert set(by_name) == {"inspect_pdf", "transcribe_audio"}
        assert by_name["inspect_pdf"]["calls"] == 2
        assert by_name["inspect_pdf"]["total_sec"] == 12.0  # 10 + half of 4
        assert by_name["inspect_pdf"]["avg_sec"] == 6.0
        assert by_name["transcribe_audio"]["calls"] == 1
        assert by_name["transcribe_audio"]["total_sec"] == 2.0
        assert by_name["transcribe_audio"]["avg_sec"] == 2.0

    def test_skips_task_steps(self):
        steps = [
            _FakeTaskStep(task="Q?"),
            _FakeActionStep(1, thought="x", duration=1.0),
        ]
        worklog = text._serialize_worklog(steps)
        assert len(worklog["steps"]) == 1
        assert worklog["steps"][0]["thought"] == "x"

    def test_error_field_included(self):
        steps = [_FakeActionStep(1, error=RuntimeError("boom"))]
        worklog = text._serialize_worklog(steps)
        assert worklog["steps"][0]["error"] == "boom"

    def test_missing_attributes_are_safe(self):
        steps = [_FakeActionStep(1, thought=None, tool_calls=None, observations=None)]
        worklog = text._serialize_worklog(steps)
        step = worklog["steps"][0]
        assert step["thought"] == ""
        assert step["observations"] == ""
        assert step["tool_calls"] == []
        assert step["duration_sec"] == 0.0

    def test_observations_list_is_joined(self):
        steps = [_FakeActionStep(1, observations=["a", "b"])]
        worklog = text._serialize_worklog(steps)
        assert worklog["steps"][0]["observations"] == "a\nb"

    def test_string_tool_arguments_are_parsed_as_json(self):
        steps = [_FakeActionStep(1, tool_calls=[_FakeToolCall("search", '{"q": "x"}')])]
        worklog = text._serialize_worklog(steps)
        assert worklog["steps"][0]["tool_calls"][0]["arguments"] == {"q": "x"}

    def test_total_duration_sums_steps(self):
        steps = [
            _FakeActionStep(1, duration=1.5),
            _FakeActionStep(2, duration=2.5),
        ]
        worklog = text._serialize_worklog(steps)
        assert worklog["total_duration_sec"] == 4.0


# ─── Answer bundle I/O (ticket 03) ──────────────────────────────────────
class TestAnswerBundle:
    def test_make_bundle_entry_shape(self):
        entry = text._make_bundle_entry(
            task_id="t1", question="Q?", level="1", file_name="a.pdf",
            answer="42", worklog={"status": "completed", "steps": []},
            timestamp="2026-01-01T00:00:00Z",
        )
        assert entry["task_id"] == "t1"
        assert entry["question"] == "Q?"
        assert entry["level"] == "1"
        assert entry["file_name"] == "a.pdf"
        assert entry["answer"] == "42"
        assert entry["worklog"] == {"status": "completed", "steps": []}
        assert entry["timestamp"] == "2026-01-01T00:00:00Z"

    def test_load_missing_bundle_returns_empty(self, tmp_path):
        assert text._load_answer_bundle(str(tmp_path / "missing.json")) == {}

    def test_load_corrupt_bundle_returns_empty(self, tmp_path):
        path = tmp_path / "bundle.json"
        path.write_text("{not valid json")
        assert text._load_answer_bundle(str(path)) == {}

    def test_save_then_load_round_trip(self, tmp_path):
        path = str(tmp_path / "bundle.json")
        bundle = {"t1": text._make_bundle_entry(
            "t1", "Q?", "1", "", "42", {"steps": []}, "2026-01-01T00:00:00Z")}
        saved = text._save_answer_bundle(bundle, path)
        assert saved == path
        assert text._load_answer_bundle(path) == bundle

    def test_save_leaves_no_temp_file(self, tmp_path):
        path = str(tmp_path / "bundle.json")
        text._save_answer_bundle({}, path)
        assert os.path.exists(path)
        assert not os.path.exists(path + ".tmp")


# ─── Run pipeline bundling (ticket 03) ──────────────────────────────────
class _FakeSolverAgent:
    calls = []

    def __init__(self):
        pass

    def solve(self, task_id, question, file_name):
        _FakeSolverAgent.calls.append(task_id)
        worklog = {
            "steps": [{"step_number": 1, "type": "action", "thought": "t",
                       "tool_calls": [], "observations": "o", "code_action": "",
                       "duration_sec": 0.0}],
            "tool_summary": [],
            "total_duration_sec": 0.0,
            "status": "completed",
        }
        return f"answer-{task_id}", worklog


class _FlakySolverAgent:
    calls = []

    def __init__(self):
        pass

    def solve(self, task_id, question, file_name):
        _FlakySolverAgent.calls.append(task_id)
        if task_id == "t1":
            return "Error: boom", {
                "steps": [], "tool_summary": [],
                "total_duration_sec": 0.0, "status": "error",
            }
        worklog = {
            "steps": [], "tool_summary": [],
            "total_duration_sec": 0.0, "status": "completed",
        }
        return f"answer-{task_id}", worklog


class TestRunPipelineBundling:
    FAKE_QUESTIONS = [
        {"task_id": "t1", "question": "Q1?", "Level": "1", "file_name": ""},
        {"task_id": "t2", "question": "Q2?", "Level": "2", "file_name": ""},
    ]

    def _patch(self, monkeypatch, tmp_path, agent_cls, questions=None):
        questions = self.FAKE_QUESTIONS if questions is None else questions
        monkeypatch.setattr(text, "_call_with_retry",
                            lambda fn, **kw: questions)
        monkeypatch.setattr(text, "ANSWER_BUNDLE_PATH",
                            str(tmp_path / "bundle.json"))
        monkeypatch.setattr(text, "RESULTS_CSV_PATH",
                            str(tmp_path / "results.csv"))
        monkeypatch.setattr(text, "GAIASolverAgent", agent_cls)

    def test_first_run_solves_and_writes_bundle(self, monkeypatch, tmp_path):
        _FakeSolverAgent.calls = []
        self._patch(monkeypatch, tmp_path, _FakeSolverAgent)
        text.run_pipeline_and_save_csv(force=False)

        assert set(_FakeSolverAgent.calls) == {"t1", "t2"}
        bundle = text._load_answer_bundle(str(tmp_path / "bundle.json"))
        assert set(bundle) == {"t1", "t2"}
        assert bundle["t1"]["answer"] == "answer-t1"
        assert bundle["t1"]["worklog"]["status"] == "completed"

        df = pd.read_csv(tmp_path / "results.csv")
        assert set(df["task_id"]) == {"t1", "t2"}
        assert set(df["source"]) == {"live"}
        assert set(df["status"]) == {"completed"}

    def test_rerun_submits_from_bundle_without_solving(self, monkeypatch, tmp_path):
        _FakeSolverAgent.calls = []
        self._patch(monkeypatch, tmp_path, _FakeSolverAgent)
        text.run_pipeline_and_save_csv(force=False)
        _FakeSolverAgent.calls = []  # reset

        text.run_pipeline_and_save_csv(force=False)

        assert _FakeSolverAgent.calls == []
        bundle = text._load_answer_bundle(str(tmp_path / "bundle.json"))
        assert set(bundle) == {"t1", "t2"}
        df = pd.read_csv(tmp_path / "results.csv")
        assert set(df["source"]) == {"bundle"}
        assert df.loc[df["task_id"] == "t1", "answer"].iloc[0] == "answer-t1"

    def test_force_reresolves_everything(self, monkeypatch, tmp_path):
        _FakeSolverAgent.calls = []
        self._patch(monkeypatch, tmp_path, _FakeSolverAgent)
        text.run_pipeline_and_save_csv(force=False)
        _FakeSolverAgent.calls = []

        text.run_pipeline_and_save_csv(force=True)

        assert set(_FakeSolverAgent.calls) == {"t1", "t2"}
        df = pd.read_csv(tmp_path / "results.csv")
        assert set(df["source"]) == {"live"}

    def test_failed_task_is_not_bundled_and_is_retried(self, monkeypatch, tmp_path):
        _FlakySolverAgent.calls = []
        self._patch(monkeypatch, tmp_path, _FlakySolverAgent)
        text.run_pipeline_and_save_csv(force=False)

        bundle = text._load_answer_bundle(str(tmp_path / "bundle.json"))
        assert "t1" not in bundle
        assert "t2" in bundle
        df = pd.read_csv(tmp_path / "results.csv")
        assert df.loc[df["task_id"] == "t1", "status"].iloc[0] == "error"
        assert df.loc[df["task_id"] == "t2", "status"].iloc[0] == "completed"

        # The failed Task is still missing from the bundle, so the next Run
        # solves it again; the already-solved Task is not re-solved.
        _FlakySolverAgent.calls = []
        text.run_pipeline_and_save_csv(force=False)
        assert _FlakySolverAgent.calls == ["t1"]

    def test_force_reresolve_removes_stale_entry_on_failure(self, monkeypatch, tmp_path):
        # Run 1 solves both Tasks into the bundle.
        _FakeSolverAgent.calls = []
        self._patch(monkeypatch, tmp_path, _FakeSolverAgent)
        text.run_pipeline_and_save_csv(force=False)
        assert set(text._load_answer_bundle(str(tmp_path / "bundle.json"))) == {"t1", "t2"}

        # Run 2 force re-solves: t1 now fails, t2 succeeds. The stale t1 entry
        # must not survive (a non-forced Run would otherwise resubmit the old
        # Answer while this Run's CSV shows an error).
        _FlakySolverAgent.calls = []
        self._patch(monkeypatch, tmp_path, _FlakySolverAgent)
        text.run_pipeline_and_save_csv(force=True)

        bundle = text._load_answer_bundle(str(tmp_path / "bundle.json"))
        assert "t1" not in bundle
        assert bundle["t2"]["answer"] == "answer-t2"
        df = pd.read_csv(tmp_path / "results.csv")
        assert df.loc[df["task_id"] == "t1", "status"].iloc[0] == "error"

    def test_bundle_is_pruned_to_served_set(self, monkeypatch, tmp_path):
        _FakeSolverAgent.calls = []
        self._patch(monkeypatch, tmp_path, _FakeSolverAgent)
        text.run_pipeline_and_save_csv(force=False)  # serves {t1, t2}
        assert set(text._load_answer_bundle(str(tmp_path / "bundle.json"))) == {"t1", "t2"}

        # Server now serves only t1: the bundle must be pruned so it stays
        # consistent with the results CSV and the served Question set.
        _FakeSolverAgent.calls = []
        self._patch(monkeypatch, tmp_path, _FakeSolverAgent,
                    questions=self.FAKE_QUESTIONS[:1])
        text.run_pipeline_and_save_csv(force=False)

        bundle = text._load_answer_bundle(str(tmp_path / "bundle.json"))
        assert set(bundle) == {"t1"}


# ─── solve() Worklog capture on failure (ticket 03) ─────────────────────
class TestSolveWorklogOnError:
    def test_captures_partial_trace_when_run_fails(self):
        class _FakeMemory:
            def __init__(self):
                self.steps = []

            def reset(self):
                self.steps = []

        class _BoomAgent:
            def __init__(self):
                self.memory = _FakeMemory()

            def run(self, prompt):
                self.memory.steps = [
                    _FakeActionStep(1, thought="partial thought", duration=1.0)
                ]
                raise RuntimeError("model exploded")

        # Build a GAIASolverAgent without __init__ (which needs DEEPSEEK_API_KEY)
        solver = object.__new__(text.GAIASolverAgent)
        solver.agent = _BoomAgent()

        answer, worklog = solver.solve("t1", "Q?", "")

        assert answer.startswith("Error:")
        assert worklog["status"] == "error"
        assert worklog["error"] == "model exploded"
        assert len(worklog["steps"]) == 1
        assert worklog["steps"][0]["thought"] == "partial thought"


# ─── Agent toolset: text/web-only Space fallback (ticket 04, ADR-0002) ──
class TestGAIASolverAgentToolset:
    def _build(self, monkeypatch, multimodal):
        captured = {}

        class _FakeCodeAgent:
            def __init__(self, **kwargs):
                captured["tools"] = kwargs["tools"]

        monkeypatch.setattr(text, "DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setattr(text, "CodeAgent", _FakeCodeAgent)

        text.GAIASolverAgent(multimodal=multimodal)
        return {t.name for t in captured["tools"]}

    def test_full_toolset_includes_multimodal_tools(self, monkeypatch):
        names = self._build(monkeypatch, multimodal=True)
        assert {"inspect_image", "transcribe_audio", "extract_video_frames"} <= names
        assert {"web_search", "get_youtube_transcript", "inspect_pdf"} <= names

    def test_text_only_toolset_excludes_multimodal_tools(self, monkeypatch):
        # ADR-0002: the live-on-Space fallback is text/web-only (no local vision
        # or audio models), so those tools must not be registered.
        names = self._build(monkeypatch, multimodal=False)
        assert "inspect_image" not in names
        assert "transcribe_audio" not in names
        assert "extract_video_frames" not in names
        # Web + document tools stay available
        assert {"web_search", "get_youtube_transcript", "inspect_pdf",
                "inspect_excel", "read_file_as_text", "inspect_docx",
                "inspect_pptx", "extract_zip"} <= names


# ─── solve() explicit file_path (ticket 04) ────────────────────────────
class TestSolveExplicitFilePath:
    def _make_solver(self):
        class _FakeMemory:
            def __init__(self):
                self.steps = []

            def reset(self):
                self.steps = []

        class _FakeAgent:
            def __init__(self):
                self.memory = _FakeMemory()
                self.prompt = None

            def run(self, prompt):
                self.prompt = prompt
                return "42"

        solver = object.__new__(text.GAIASolverAgent)
        solver.agent = _FakeAgent()
        solver._clean_answer = lambda q, a: a  # skip the LLM cleaning call
        return solver

    def test_explicit_file_path_skips_local_lookup(self, monkeypatch):
        solver = self._make_solver()

        seen = {"called": False}

        def fake_lookup(task_id, file_name):
            seen["called"] = True
            return "/local/path"

        monkeypatch.setattr(text, "_get_local_task_file", fake_lookup)

        answer, worklog = solver.solve("t1", "Q?", "doc.pdf",
                                       file_path="/downloaded/t1.pdf")

        assert answer == "42"
        assert seen["called"] is False  # explicit path wins over local lookup
        assert "/downloaded/t1.pdf" in solver.agent.prompt
        assert worklog["status"] == "completed"

    def test_missing_file_path_uses_local_lookup(self, monkeypatch):
        solver = self._make_solver()

        seen = {"path": ""}

        def fake_lookup(task_id, file_name):
            seen["path"] = "/local/report.pdf"
            return "/local/report.pdf"

        monkeypatch.setattr(text, "_get_local_task_file", fake_lookup)

        solver.solve("t1", "Q?", "report.pdf")

        assert seen["path"] == "/local/report.pdf"
        assert "/local/report.pdf" in solver.agent.prompt
