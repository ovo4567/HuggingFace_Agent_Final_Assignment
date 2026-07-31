"""Unit tests for the Benchmark results-view renderer (ticket 05).

``results_view.render_results_view`` turns the orchestration results view
(``{"score_card": ..., "rows": [...]}``) into a self-contained HTML fragment —
the ticket-05 test seam (pure string building, no Gradio dependency). Only
external behaviour is asserted:

- the score card shows every field the server returns (score %, correct/total,
  timestamp, message, username);
- the table lists each Question with its Level, submitted Answer, and source
  (bundle/live);
- each Task's Worklog expands to show the tool summary and the full trace;
- every value from the server / Agent is HTML-escaped.
"""

import re

import results_view


def _row(task_id="t1", question="Q?", level="1", answer="42", source="bundle",
         status="completed", timestamp="2026-08-01T00:00:00Z", worklog=None):
    return {
        "task_id": task_id,
        "question": question,
        "level": level,
        "file_name": "None",
        "answer": answer,
        "source": source,
        "status": status,
        "timestamp": timestamp,
        "worklog": worklog,
    }


def _worklog(steps=None, tool_summary=None, total=1.0, status="completed"):
    return {
        "steps": steps if steps is not None else [],
        "tool_summary": tool_summary if tool_summary is not None else [],
        "total_duration_sec": total,
        "status": status,
    }


def _sample_step(n=1, step_type="action", thought="I think.",
                 tool_calls=None, observations="o", duration=0.8):
    return {
        "step_number": n,
        "type": step_type,
        "thought": thought,
        "tool_calls": tool_calls or [],
        "observations": observations,
        "code_action": "",
        "duration_sec": duration,
    }


def _full_view(score_card=None, rows=None):
    return {
        "score_card": score_card if score_card is not None else {
            "username": "alex", "score": 66.67, "correct_count": 2,
            "total_attempted": 3, "message": "2 of 3 matched.",
            "timestamp": "2026-08-01T12:00:00Z",
        },
        "rows": rows if rows is not None else [],
    }


# ─── Score card ────────────────────────────────────────────────────────
class TestScoreCard:
    def test_shows_every_server_field(self):
        html = results_view.render_results_view(_full_view())

        assert "Overall Score" in html
        assert '<div class="gv-v">66.7%</div>' in html
        assert "Correct" in html
        assert "Attempted" in html
        assert "Submitted at" in html
        assert "2026-08-01T12:00:00Z" in html
        # Message and username are both shown
        assert "2 of 3 matched." in html
        assert "· user alex" in html

    def test_score_is_formatted_as_percent(self):
        assert results_view._fmt_score(66.67) == "66.7%"
        assert results_view._fmt_score(100) == "100.0%"
        assert results_view._fmt_score(0) == "0.0%"

    def test_missing_score_fields_render_placeholders(self):
        html = results_view.render_results_view(_full_view(
            score_card={"username": "alex"}))

        assert "N/A" in html                       # score
        assert "—" in html                         # correct/total placeholders
        # The message strip is skipped when neither message nor username...
        assert "gv-message" in html               # ...but username is shown

    def test_score_none_becomes_na(self):
        assert results_view._fmt_score(None) == "N/A"


# ─── Per-Question table ────────────────────────────────────────────────
class TestTable:
    def test_lists_questions_with_level_answer_and_source(self):
        rows = [
            _row(task_id="t1", question="Capital of France?", level="1",
                 answer="Paris", source="bundle"),
            _row(task_id="t2", question="Count the r's?", level="2",
                 answer="3", source="live", status="completed",
                 worklog=_worklog()),
        ]
        html = results_view.render_results_view(_full_view(rows=rows))

        assert "Capital of France?" in html
        # Apostrophes are HTML-escaped (&#x27;) by html.escape
        assert "Count the r&#x27;s?" in html
        assert "<b>Paris</b>" in html
        assert "<b>3</b>" in html
        assert "gv-level1" in html and "gv-level2" in html
        assert "gv-source-bundle" in html and "gv-source-live" in html
        assert "t1" in html and "t2" in html

    def test_empty_rows_render_empty_state(self):
        html = results_view.render_results_view(_full_view(rows=[]))
        assert "No Tasks to show." in html


# ─── Expandable per-Task Worklog ───────────────────────────────────────
class TestWorklog:
    def test_expands_with_tool_summary_and_full_trace(self):
        worklog = _worklog(
            steps=[
                _sample_step(1, "action", "Let me count in code.",
                             tool_calls=[{"name": "python",
                                          "arguments": {"code": "x = 1"}}],
                             observations="Execution logs:\n3", duration=3.1),
                _sample_step(2, "final", "The answer is 3.",
                             observations="", duration=0.5),
            ],
            tool_summary=[{"name": "python", "calls": 1,
                           "total_sec": 3.1, "avg_sec": 3.1}],
            total=3.6,
        )
        html = results_view.render_results_view(
            _full_view(rows=[_row(worklog=worklog)]))

        # Expandable <details> block with a summary label
        assert '<details class="gv-worklog"><summary>Worklog · 2 steps</summary>' in html
        # Tool summary table
        assert '<table class="gv-wl-summary">' in html
        assert ">python</td>" in html
        assert "<td>1</td>" in html
        # Full trace: thought, tool call + arguments, observation, duration
        assert "Let me count in code." in html
        assert "<b>python</b>" in html
        # Tool arguments are JSON-escaped then HTML-escaped (quotes → &quot;)
        assert '&quot;code&quot;: &quot;x = 1&quot;' in html
        assert "Execution logs:\n3" in html
        assert "Step 1 · action · 3.1s" in html
        assert "Step 2 · final · 0.5s" in html
        assert "The answer is 3." in html

    def test_missing_worklog_renders_empty_state(self):
        html = results_view.render_results_view(
            _full_view(rows=[_row(worklog=None)]))
        assert "No worklog recorded for this Task." in html

    def test_empty_tool_summary_renders_none_message(self):
        html = results_view.render_results_view(
            _full_view(rows=[_row(worklog=_worklog())]))
        assert "No tool calls." in html

    def test_error_step_is_shown(self):
        worklog = _worklog(steps=[{
            "step_number": 1, "type": "action", "thought": "try",
            "tool_calls": [], "observations": "", "code_action": "",
            "duration_sec": 1.0, "error": "model exploded",
        }])
        html = results_view.render_results_view(
            _full_view(rows=[_row(worklog=worklog)]))
        assert "model exploded" in html

    def test_top_level_worklog_error_is_rendered(self):
        # text.py records a failed live solve at worklog["error"]; the view
        # must surface it instead of an empty trace.
        worklog = _worklog(steps=[], status="error")
        worklog["error"] = "model exploded on live solve"
        html = results_view.render_results_view(
            _full_view(rows=[_row(worklog=worklog)]))
        assert "model exploded on live solve" in html


# ─── Escaping / safety ─────────────────────────────────────────────────
class TestEscaping:
    def test_answers_and_questions_are_escaped(self):
        rows = [
            _row(question='What is <b>2</b> & 3?',
                 answer='<script>alert(1)</script>'),
        ]
        html = results_view.render_results_view(_full_view(rows=rows))

        assert "<b>2</b>" not in html
        assert "&lt;b&gt;2&lt;/b&gt;" in html
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    def test_worklog_fields_are_escaped(self):
        worklog = _worklog(
            steps=[_sample_step(1, "action",
                                thought="<img src=x onerror=alert(1)>",
                                tool_calls=[{"name": "x", "arguments": {"k": "<b>"}}],
                                observations="<i>obs</i>")],
        )
        html = results_view.render_results_view(
            _full_view(rows=[_row(worklog=worklog)]))

        assert "<img src=x onerror=alert(1)>" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html
        assert "<i>obs</i>" not in html
        assert "&lt;i&gt;obs&lt;/i&gt;" in html
        # Tool arguments are JSON-escaped then HTML-escaped (quotes → &quot;)
        assert '&quot;k&quot;: &quot;&lt;b&gt;&quot;' in html

    def test_badge_class_tokens_cannot_break_the_attribute(self):
        # level/source/status are server/Agent-controlled; a crafted value must
        # not be able to break out of the class="..." attribute.
        rows = [
            _row(level='1"><img src=x onerror=alert(1)>',
                 source='live"><b>evil</b>',
                 status='completed"><script>alert(1)</script>'),
        ]
        html = results_view.render_results_view(_full_view(rows=rows))

        # No crafted value may introduce new markup elements.
        assert '<img src=x' not in html
        assert '<script>alert(1)</script>' not in html
        # Every badge class token is sanitized to a CSS-safe alphabet — a
        # crafted value cannot break out of the class="..." attribute.
        tokens = re.findall(r'class="gv-badge gv-([^"]*)"', html)
        assert tokens
        assert all(re.fullmatch(r"[A-Za-z0-9_-]+", t) for t in tokens)
        # The original values still appear, HTML-escaped, in the badge text.
        assert '&quot;&gt;&lt;' in html

    def test_none_view_renders_empty_state(self):
        assert "No results yet." in results_view.render_results_view(None)
        assert "No results yet." in results_view.render_results_view({})
