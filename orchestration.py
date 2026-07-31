"""Space run orchestration — the single test seam for the GAIA Solver Space.

Given the served Questions, the committed Answer bundle, an Agent, and a server
API, this module produces the submission payload and the results view that
``app.py`` renders (ADR-0001 hybrid execution):

- Tasks matched in the Answer bundle are submitted from the bundle **without
  calling the Agent** (the bundle is authoritative for the Answer + Worklog);
- Tasks missing from the bundle are solved **live** (best-effort, text/web-only
  per ADR-0002): file-based Tasks have their Attachment downloaded from the
  server first, then the Agent is called and its live Worklog is recorded.

``app.py`` wires this module to Gradio + the real scoring server; tests inject
a fake Agent and a fake scoring server (the pre-agreed test seam).
"""

import os
import tempfile
import time

import requests

import text  # reuse the tested retry helper for transient network errors


# ScoreResponse fields returned by the /submit endpoint (aggregate only — the
# server does not return per-Question or per-Level correctness).
SCORE_CARD_FIELDS = (
    "username", "score", "correct_count", "total_attempted", "message", "timestamp",
)


def _now_iso() -> str:
    """Returns the current UTC time in ISO-8601 (seconds) format."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _has_attachment(file_name) -> bool:
    """True when a Task actually carries an Attachment.

    The server reports absent Attachments as ``""``/``None`` or the literal
    string ``"None"`` (the same convention the Answer bundle uses), so all of
    those are treated as no Attachment.
    """
    return bool(file_name and str(file_name).strip()
                and str(file_name).strip().lower() != "none")


def _empty_score_card(username: str) -> dict:
    """A results-view score card with every server field, unfilled."""
    return {field: None for field in SCORE_CARD_FIELDS} | {"username": username}


class ServerAPI:
    """Thin client for the scoring server.

    Encapsulates the three endpoints the Space uses: ``GET /questions``,
    ``GET /files/{task_id}`` (attachment download), and ``POST /submit``.
    ``session`` is injectable so tests can substitute a fake transport; it must
    expose ``get``/``post`` like the ``requests`` module (the default).
    """

    def __init__(self, api_url: str, *, timeout: int = 15,
                 download_dir: str | None = None, session=None):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        # Where live file Tasks' Attachments are written so the Agent can read
        # them. Lazily created on first download (temp dir by default).
        self.download_dir = download_dir
        self._session = session if session is not None else requests

    def fetch_questions(self) -> list:
        """GET /questions, retrying transient network errors with backoff."""
        def _fetch():
            response = self._session.get(
                f"{self.api_url}/questions", timeout=self.timeout)
            response.raise_for_status()
            return response.json()

        return text._call_with_retry(_fetch, description="questions fetch")

    def download_file(self, task_id: str, file_name: str = "") -> str:
        """GET /files/{task_id} and save the Attachment; returns the local path.

        The file is saved as ``{task_id}.{ext}`` (extension taken from the
        server-reported file_name) inside ``download_dir``, matching the local
        solver's task_id-based naming so ``_get_local_task_file`` can find it.
        Transient network errors are retried with backoff like the other
        endpoints.
        """
        def _fetch():
            response = self._session.get(
                f"{self.api_url}/files/{task_id}", timeout=self.timeout)
            response.raise_for_status()
            return response.content

        content = text._call_with_retry(_fetch, description="file download")

        if self.download_dir is None:
            self.download_dir = tempfile.mkdtemp(prefix="gaia_space_files_")
        os.makedirs(self.download_dir, exist_ok=True)

        ext = ""
        if file_name and "." in file_name:
            ext = "." + file_name.rsplit(".", 1)[-1]
        path = os.path.join(self.download_dir, f"{task_id}{ext}")
        with open(path, "wb") as f:
            f.write(content)
        return path

    def submit(self, payload: dict) -> dict:
        """POST /submit and return the parsed ScoreResponse, retrying transient
        network errors with backoff."""
        def _submit():
            response = self._session.post(
                f"{self.api_url}/submit", json=payload, timeout=self.timeout)
            response.raise_for_status()
            return response.json()

        return text._call_with_retry(_submit, description="submission")


def _bundle_row(task, entry, answer: str) -> dict:
    """Builds a results-view row for a Task submitted from the Answer bundle.

    The bundle is authoritative for the Answer, Worklog, timestamp, and the
    bundled Question/Level/file_name; the server's Task metadata is a fallback.
    """
    return {
        "task_id": task.get("task_id"),
        "question": entry.get("question", task.get("question", "")),
        "level": entry.get("level", task.get("Level", "?")),
        "file_name": entry.get("file_name", task.get("file_name", "") or "None"),
        "answer": answer,
        "source": "bundle",
        "status": "completed",
        "timestamp": entry.get("timestamp", _now_iso()),
        "worklog": entry.get("worklog"),
    }


def _live_row(task, answer: str, *, status: str, worklog=None) -> dict:
    """Builds a results-view row for a Task solved live by the Agent."""
    return {
        "task_id": task.get("task_id"),
        "question": task.get("question", ""),
        "level": task.get("Level", "?"),
        "file_name": task.get("file_name", "") or "None",
        "answer": answer,
        "source": "live",
        "status": status,
        "timestamp": _now_iso(),
        "worklog": worklog,
    }


def orchestrate_run(questions, bundle, agent, server, *, username: str,
                    agent_code: str) -> tuple[dict, dict]:
    """Builds the ``(submission_payload, results_view)`` for a Run.

    Args:
        questions: The served Tasks, as returned by ``server.fetch_questions()``
            (each has ``task_id``, ``question``, ``Level``, ``file_name``).
        bundle: The committed Answer bundle (dict keyed by ``task_id``).
        agent: The live Agent, with ``solve(task_id, question, file_name,
            file_path=None) -> (answer, worklog)``. ``None`` is allowed: then
            every unbundled Task is recorded as an error (best-effort) and the
            bundled Answers are still submitted.
        server: A server API with ``download_file(task_id, file_name)``.
        username: The HF username the submission is attributed to.
        agent_code: The agent's code link included in the payload.

    Returns:
        A ``(payload, view)`` pair:
        - ``payload`` matches the ``/submit`` contract
          ``{username, agent_code, answers: [{task_id, submitted_answer}]}``;
          every served Task with a real Answer (bundled or live) is included;
          Tasks the live Agent failed to solve are **not** submitted (an error
          string would be scored as a guaranteed-wrong attempt) — they appear
          as ``status: "error"`` rows in the view instead.
        - ``view`` is the results view ``{"score_card": {...}, "rows": [...]}``.
          The score card holds every ScoreResponse field (unfilled until the
          caller posts the payload and merges ``parse_score_response``); each
          row holds ``{task_id, question, level, file_name, answer, source,
          status, timestamp, worklog}``.
    """
    rows = []
    answers = []
    for task in questions:
        task_id = task.get("task_id")
        if not task_id:
            continue

        entry = bundle.get(task_id)
        if entry is not None:
            # Bundle match: submit the bundled Answer + Worklog, never call the
            # Agent (ADR-0001).
            answer = entry.get("answer", "")
            rows.append(_bundle_row(task, entry, answer))
            answers.append({"task_id": task_id, "submitted_answer": answer})
            continue

        # Live fallback (best-effort, text/web-only): download the Attachment
        # from the server first so the Agent can read it.
        file_name = task.get("file_name", "")
        file_path = None
        if _has_attachment(file_name):
            try:
                file_path = server.download_file(task_id, file_name)
            except Exception as e:
                # A failed download must not kill the Run: the Agent still gets
                # a chance to answer from the Question alone (best-effort).
                print(f"⚠️ Could not download attachment for {task_id}: {e}")

        if agent is None:
            # No live Agent (e.g. fully-bundled Run without a key): record an
            # error row but still submit the bundle. The failed Task is not
            # submitted — an error string must not be scored as a wrong attempt.
            answer = "Error: No live agent available to solve this Task."
            rows.append(_live_row(task, answer, status="error"))
            continue

        try:
            answer, worklog = agent.solve(
                task_id, task.get("question", ""), file_name, file_path=file_path)
            status = worklog.get("status", "completed") if worklog else "completed"
            rows.append(_live_row(task, answer, status=status, worklog=worklog))
            answers.append({"task_id": task_id, "submitted_answer": answer})
        except Exception as e:
            # A throwing Agent must not fail the whole Run: surface the failure
            # as an error row in the view, but do not submit the error string
            # (it would count as a guaranteed-wrong attempt).
            answer = f"Error: {e}"
            rows.append(_live_row(task, answer, status="error"))

    payload = {
        "username": username,
        "agent_code": agent_code,
        "answers": answers,
    }
    view = {
        "score_card": _empty_score_card(username),
        "rows": rows,
    }
    return payload, view


def parse_score_response(response: dict) -> dict:
    """Extracts the ScoreResponse fields into a score-card dict.

    Missing fields default to ``None`` so the view is always well-shaped.
    """
    return {field: response.get(field) for field in SCORE_CARD_FIELDS}
