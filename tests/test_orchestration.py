"""Unit tests for the Space run orchestration module (ticket 04).

Tests inject a fake Agent and a fake scoring server — the pre-agreed test seam
from the spec: the orchestration module takes `(Questions, Answer bundle,
Agent, server API)` and returns `(submission payload, results view)`. Only
external behavior is asserted:

- bundle-matched Tasks are submitted from the Answer bundle (the Agent is not
  called) and carry the bundled Worklog;
- unbundled Tasks call the Agent and record its live Worklog, with file-based
  Tasks having their Attachment downloaded from the server first;
- the submission payload matches the `/submit` contract
  (`{username, agent_code, answers: [{task_id, submitted_answer}]}`);
- the results-view shape is correct (score-card fields, per-Task rows, Worklog).
"""

import text
import orchestration


def _bundle_entry(task_id, answer, question="Q?", level="1", file_name="",
                  timestamp="2026-01-01T00:00:00Z", worklog=None):
    return text._make_bundle_entry(
        task_id, question, level, file_name, answer,
        worklog if worklog is not None else {"status": "completed", "steps": []},
        timestamp,
    )


class _FakeAgent:
    """Records every solve() call; returns a canned answer + Worklog."""

    def __init__(self):
        self.calls = []

    def solve(self, task_id, question, file_name, file_path=None):
        self.calls.append({"task_id": task_id, "file_name": file_name,
                           "file_path": file_path})
        worklog = {
            "steps": [{"step_number": 1, "type": "final",
                       "thought": "done", "tool_calls": [], "observations": "o",
                       "code_action": "", "duration_sec": 0.0}],
            "tool_summary": [{"name": "search", "calls": 1, "total_sec": 0.0,
                              "avg_sec": 0.0}],
            "total_duration_sec": 0.0,
            "status": "completed",
        }
        return f"answer-{task_id}", worklog


class _FailingAgent(_FakeAgent):
    def solve(self, task_id, question, file_name, file_path=None):
        self.calls.append({"task_id": task_id, "file_name": file_name,
                           "file_path": file_path})
        raise RuntimeError("model exploded")


class _FakeServer:
    """Fake scoring server: records downloads/submissions, serves questions."""

    def __init__(self, questions=None, download_dir=""):
        self.questions = questions or []
        self.download_dir = download_dir
        self.download_calls = []
        self.submit_calls = []
        self.score_response = {
            "username": "alex",
            "score": 66.67,
            "correct_count": 2,
            "total_attempted": 3,
            "message": "ok",
            "timestamp": "2026-08-01T00:00:00Z",
        }

    def fetch_questions(self):
        return self.questions

    def download_file(self, task_id, file_name=""):
        self.download_calls.append((task_id, file_name))
        return f"{self.download_dir}/{task_id}.pdf"

    def submit(self, payload):
        self.submit_calls.append(payload)
        return dict(self.score_response)


# ─── Bundle matching ──────────────────────────────────────────────────
class TestBundleMatching:
    def test_fully_bundled_run_never_calls_the_agent(self):
        questions = [
            {"task_id": "t1", "question": "Q1?", "Level": "1", "file_name": ""},
            {"task_id": "t2", "question": "Q2?", "Level": "2", "file_name": ""},
        ]
        bundle = {
            "t1": _bundle_entry("t1", "42"),
            "t2": _bundle_entry("t2", "Paris"),
        }
        agent = _FakeAgent()
        server = _FakeServer(questions)

        payload, view = orchestration.orchestrate_run(
            questions, bundle, agent, server, username="alex",
            agent_code="https://hf.co/spaces/x",
        )

        assert agent.calls == []
        assert payload["answers"] == [
            {"task_id": "t1", "submitted_answer": "42"},
            {"task_id": "t2", "submitted_answer": "Paris"},
        ]
        assert server.download_calls == []

    def test_unbundled_task_calls_agent_but_bundled_task_does_not(self):
        questions = [
            {"task_id": "t1", "question": "Q1?", "Level": "1", "file_name": ""},
            {"task_id": "t2", "question": "Q2?", "Level": "2", "file_name": ""},
        ]
        bundle = {"t1": _bundle_entry("t1", "42")}
        agent = _FakeAgent()
        server = _FakeServer(questions)

        payload, _ = orchestration.orchestrate_run(
            questions, bundle, agent, server, username="alex",
            agent_code="https://hf.co/spaces/x",
        )

        assert [c["task_id"] for c in agent.calls] == ["t2"]
        by_id = {a["task_id"]: a["submitted_answer"] for a in payload["answers"]}
        assert by_id["t1"] == "42"      # from the bundle
        assert by_id["t2"] == "answer-t2"  # from the live Agent

    def test_tasks_without_task_id_are_skipped(self):
        questions = [
            {"task_id": None, "question": "no-id?", "Level": "1", "file_name": ""},
            {"task_id": "t1", "question": "Q1?", "Level": "1", "file_name": ""},
        ]
        bundle = {"t1": _bundle_entry("t1", "42")}
        agent = _FakeAgent()

        payload, _ = orchestration.orchestrate_run(
            questions, bundle, agent, _FakeServer(questions),
            username="alex", agent_code="x",
        )
        assert [a["task_id"] for a in payload["answers"]] == ["t1"]


# ─── Live fallback + attachment download ──────────────────────────────
class TestLiveFallback:
    def test_file_based_task_downloads_attachment_before_agent(self, tmp_path):
        questions = [{"task_id": "t2", "question": "Q2?", "Level": "2",
                      "file_name": "doc.pdf"}]
        bundle = {}
        agent = _FakeAgent()
        server = _FakeServer(questions, download_dir=str(tmp_path))

        _, view = orchestration.orchestrate_run(
            questions, bundle, agent, server, username="alex",
            agent_code="x",
        )

        # Attachment downloaded from the server first...
        assert server.download_calls == [("t2", "doc.pdf")]
        # ...and its local path was handed to the Agent
        assert agent.calls[0]["file_path"] == f"{tmp_path}/t2.pdf"
        assert agent.calls[0]["task_id"] == "t2"

    def test_task_without_attachment_does_not_download(self):
        questions = [{"task_id": "t2", "question": "Q2?", "Level": "2",
                      "file_name": ""}]
        agent = _FakeAgent()
        server = _FakeServer(questions)

        _, view = orchestration.orchestrate_run(
            questions, {}, agent, server, username="alex", agent_code="x",
        )

        assert server.download_calls == []
        assert agent.calls[0]["file_path"] is None

    def test_string_none_file_name_is_not_an_attachment(self):
        questions = [{"task_id": "t2", "question": "Q2?", "Level": "2",
                      "file_name": "None"}]
        agent = _FakeAgent()
        server = _FakeServer(questions)

        _, _ = orchestration.orchestrate_run(
            questions, {}, agent, server, username="alex", agent_code="x",
        )
        assert server.download_calls == []
        assert agent.calls[0]["file_path"] is None

    def test_live_agent_error_is_recorded_but_not_submitted(self):
        questions = [{"task_id": "t2", "question": "Q2?", "Level": "2",
                      "file_name": ""}]
        agent = _FailingAgent()
        server = _FakeServer(questions)

        payload, view = orchestration.orchestrate_run(
            questions, {}, agent, server, username="alex", agent_code="x",
        )

        # Best-effort: the failure is surfaced in the view but an error string
        # is NOT submitted (it would be scored as a guaranteed-wrong attempt).
        row = view["rows"][0]
        assert row["status"] == "error"
        assert row["source"] == "live"
        assert "model exploded" in row["answer"]
        assert payload["answers"] == []

    def test_no_agent_available_submits_bundle_and_marks_live_error(self):
        questions = [
            {"task_id": "t1", "question": "Q1?", "Level": "1", "file_name": ""},
            {"task_id": "t2", "question": "Q2?", "Level": "2", "file_name": ""},
        ]
        bundle = {"t1": _bundle_entry("t1", "42")}
        server = _FakeServer(questions)

        payload, view = orchestration.orchestrate_run(
            questions, bundle, None, server, username="alex", agent_code="x",
        )

        # The bundled Task is submitted; the unsolvable live Task is recorded
        # as an error row but not submitted.
        assert [a["task_id"] for a in payload["answers"]] == ["t1"]
        assert payload["answers"][0]["submitted_answer"] == "42"
        rows = {r["task_id"]: r for r in view["rows"]}
        assert rows["t2"]["status"] == "error"
        assert rows["t2"]["source"] == "live"

    def test_mixed_run_submits_only_bundled_and_successful_live(self):
        questions = [
            {"task_id": "t1", "question": "Q1?", "Level": "1", "file_name": ""},
            {"task_id": "t2", "question": "Q2?", "Level": "2", "file_name": ""},
            {"task_id": "t3", "question": "Q3?", "Level": "3", "file_name": ""},
        ]
        bundle = {"t1": _bundle_entry("t1", "42")}

        class _PartialAgent(_FakeAgent):
            def solve(self, task_id, question, file_name, file_path=None):
                if task_id == "t3":
                    raise RuntimeError("boom")
                return super().solve(task_id, question, file_name, file_path)

        payload, view = orchestration.orchestrate_run(
            questions, bundle, _PartialAgent(), _FakeServer(questions),
            username="alex", agent_code="x",
        )

        # Only the bundled Task and the successful live Task are submitted; the
        # failed live Task appears as an error row but is not scored.
        assert [a["task_id"] for a in payload["answers"]] == ["t1", "t2"]
        rows = {r["task_id"]: r for r in view["rows"]}
        assert len(rows) == 3
        assert rows["t1"]["source"] == "bundle"
        assert rows["t2"]["source"] == "live"
        assert rows["t3"]["status"] == "error"

    def test_live_forced_answer_is_submitted(self):
        # A soft-deadline Task whose synthesized Answer carries status "forced"
        # IS submitted and scored (decision B3) — unlike error/timeout.
        questions = [{"task_id": "t2", "question": "Q2?", "Level": "2",
                      "file_name": ""}]

        class _ForcedAgent(_FakeAgent):
            def solve(self, task_id, question, file_name, file_path=None):
                self.calls.append({
                    "task_id": task_id, "question": question,
                    "file_name": file_name, "file_path": file_path,
                })
                worklog = {
                    "steps": [], "tool_summary": [],
                    "total_duration_sec": 0.0, "status": "forced",
                }
                return "forced-answer", worklog

        payload, view = orchestration.orchestrate_run(
            questions, {}, _ForcedAgent(), _FakeServer(questions),
            username="alex", agent_code="x",
        )

        assert [a["task_id"] for a in payload["answers"]] == ["t2"]
        assert payload["answers"][0]["submitted_answer"] == "forced-answer"
        row = view["rows"][0]
        assert row["status"] == "forced"
        assert row["worklog"]["status"] == "forced"


# ─── Submission payload contract ──────────────────────────────────────
class TestSubmissionPayload:
    def test_payload_matches_submit_contract(self):
        questions = [{"task_id": "t1", "question": "Q1?", "Level": "1",
                      "file_name": ""}]
        bundle = {"t1": _bundle_entry("t1", "42")}

        payload, _ = orchestration.orchestrate_run(
            questions, bundle, _FakeAgent(), _FakeServer(questions),
            username="alex", agent_code="https://hf.co/spaces/x",
        )

        assert set(payload) == {"username", "agent_code", "answers"}
        assert payload["username"] == "alex"
        assert payload["agent_code"] == "https://hf.co/spaces/x"
        assert all(set(a) == {"task_id", "submitted_answer"} for a in payload["answers"])

    def test_empty_questions_yields_empty_payload(self):
        payload, _ = orchestration.orchestrate_run(
            [], {}, _FakeAgent(), _FakeServer(), username="alex", agent_code="x",
        )
        assert payload == {"username": "alex", "agent_code": "x", "answers": []}


# ─── Results-view shape ───────────────────────────────────────────────
class TestResultsView:
    def test_score_card_has_all_server_fields(self):
        questions = [{"task_id": "t1", "question": "Q1?", "Level": "1",
                      "file_name": ""}]
        _, view = orchestration.orchestrate_run(
            questions, {"t1": _bundle_entry("t1", "42")}, _FakeAgent(),
            _FakeServer(questions), username="alex", agent_code="x",
        )
        assert set(view["score_card"]) == {
            "username", "score", "correct_count", "total_attempted",
            "message", "timestamp",
        }
        assert view["score_card"]["username"] == "alex"
        # Score fields are filled in after submission, not by orchestration
        assert view["score_card"]["score"] is None

    def test_bundled_row_carries_bundle_answer_worklog_and_source(self):
        questions = [{"task_id": "t1", "question": "Q1?", "Level": "1",
                      "file_name": ""}]
        bundle = {"t1": _bundle_entry(
            "t1", "42", question="Q1?", level="1", file_name="a.pdf",
            timestamp="2026-01-01T00:00:00Z",
            worklog={"status": "completed", "steps": [{"n": 1}]})}
        agent = _FakeAgent()

        _, view = orchestration.orchestrate_run(
            questions, bundle, agent, _FakeServer(questions),
            username="alex", agent_code="x",
        )

        row = view["rows"][0]
        assert row["task_id"] == "t1"
        assert row["answer"] == "42"
        assert row["source"] == "bundle"
        assert row["status"] == "completed"
        assert row["timestamp"] == "2026-01-01T00:00:00Z"
        assert row["worklog"] == {"status": "completed", "steps": [{"n": 1}]}

    def test_live_row_carries_live_worklog_and_source(self):
        questions = [{"task_id": "t2", "question": "Q2?", "Level": "2",
                      "file_name": ""}]
        agent = _FakeAgent()

        _, view = orchestration.orchestrate_run(
            questions, {}, agent, _FakeServer(questions),
            username="alex", agent_code="x",
        )

        row = view["rows"][0]
        assert row["source"] == "live"
        assert row["answer"] == "answer-t2"
        assert row["worklog"]["status"] == "completed"
        assert row["worklog"]["tool_summary"][0]["name"] == "search"
        assert row["timestamp"]


# ─── ScoreResponse parsing ────────────────────────────────────────────
class TestParseScoreResponse:
    def test_extracts_score_card_fields(self):
        card = orchestration.parse_score_response({
            "username": "alex", "score": 75.0, "correct_count": 3,
            "total_attempted": 4, "message": "nice", "timestamp": "T",
        })
        assert card == {
            "username": "alex", "score": 75.0, "correct_count": 3,
            "total_attempted": 4, "message": "nice", "timestamp": "T",
        }

    def test_missing_fields_default_to_none(self):
        card = orchestration.parse_score_response({"username": "alex"})
        assert card == {
            "username": "alex", "score": None, "correct_count": None,
            "total_attempted": None, "message": None, "timestamp": None,
        }


# ─── ServerAPI HTTP layer ─────────────────────────────────────────────
class _FakeResponse:
    def __init__(self, json_data=None, content=b"", status_code=200):
        self._json = json_data
        self.content = content
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, get_result=None, post_result=None):
        self.get_result = get_result
        self.post_result = post_result
        self.get_calls = []
        self.post_calls = []

    def get(self, url, timeout=None):
        self.get_calls.append((url, timeout))
        return self.get_result

    def post(self, url, json=None, timeout=None):
        self.post_calls.append((url, json, timeout))
        return self.post_result


class TestServerAPI:
    def test_fetch_questions_hits_questions_endpoint(self):
        session = _FakeSession(get_result=_FakeResponse([{"task_id": "t1"}]))
        api = orchestration.ServerAPI("https://server.example", session=session)

        assert api.fetch_questions() == [{"task_id": "t1"}]
        assert session.get_calls[0][0] == "https://server.example/questions"

    def test_download_file_saves_attachment_with_task_id_name(self, tmp_path):
        session = _FakeSession(get_result=_FakeResponse(content=b"%PDF"))
        api = orchestration.ServerAPI("https://server.example", session=session,
                                     download_dir=str(tmp_path))

        path = api.download_file("abc123", "doc.pdf")

        assert session.get_calls[0][0] == "https://server.example/files/abc123"
        assert path == str(tmp_path / "abc123.pdf")
        assert open(path, "rb").read() == b"%PDF"

    def test_submit_posts_payload_and_returns_score_response(self):
        payload = {"username": "alex", "agent_code": "x", "answers": []}
        session = _FakeSession(
            post_result=_FakeResponse({"username": "alex", "score": 50.0}))
        api = orchestration.ServerAPI("https://server.example", session=session)

        result = api.submit(payload)

        assert session.post_calls[0][0] == "https://server.example/submit"
        assert session.post_calls[0][1] == payload
        assert result["score"] == 50.0
