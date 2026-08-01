"""Rendering of the Benchmark results view for the Space (ticket 05).

Turns the orchestration results view (``{"score_card": ..., "rows": [...]}``)
into a single self-contained HTML fragment:

- a **score card** showing every ScoreResponse field (score %, correct/total,
  timestamp, server message, username);
- a **per-Question table** of submitted Answers with Level, source
  (bundle/live), status, and timestamp;
- an **expandable per-Task Worklog** — the tool summary (calls + timing) and
  the full trace (thought / tool call / observation per step) — that expands
  via a plain ``<details>/<summary>`` element (no JavaScript, so it survives
  Gradio's iframe CSP).

The layout was settled by the ticket-05 throwaway prototype
(``prototype/results-view.html``); the winning variant is **"B — Stat grid +
table"** — a 4-up stat grid for the score card, then a data table whose Worklog
cell expands in place.

This module is pure string building with **no Gradio dependency**, so it is
unit-testable in isolation (the ticket-05 test seam): ``app.py`` only feeds it
the view dict and renders the result in a ``gr.HTML`` component.
"""

import html
import json
import re


# Long-running free-text fields are capped on *display* with a scrollable
# box, not truncated, so the full trace stays available without blowing up the
# page (mirrors text.py's action_output cap for observations).
_TRACE_MAX_HEIGHT = "14em"
_TOOL_ARGS_DISPLAY_LIMIT = 400

# Scoped CSS (all classes prefixed ``gv-``) so it cannot clash with Gradio's
# own stylesheet inside the iframe.
_CSS = """
.gv-root{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2328;line-height:1.5}
.gv-badge{display:inline-block;padding:1px 9px;border-radius:999px;font-size:11px;font-weight:600;color:#fff;white-space:nowrap}
.gv-badge.gv-level1{background:#10b981}.gv-badge.gv-level2{background:#f59e0b}.gv-badge.gv-level3{background:#ef4444}
.gv-badge.gv-source-bundle{background:#4f46e5}.gv-badge.gv-source-live{background:#0e7490}
.gv-badge.gv-status-completed{background:#6b7280}.gv-badge.gv-status-error{background:#b91c1c}.gv-badge.gv-status-timeout{background:#b45309}.gv-badge.gv-status-forced{background:#7c3aed}
.gv-muted{color:#6b7280}.gv-mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
.gv-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:10px}
.gv-stat{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px}
.gv-stat .gv-k{font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.04em}
.gv-stat .gv-v{font-size:22px;font-weight:700;margin-top:2px}
.gv-message{background:#fef9c3;border:1px solid #fde047;border-radius:10px;padding:10px 14px;margin-bottom:14px}
.gv-table-wrap{overflow-x:auto;background:#fff;border:1px solid #e5e7eb;border-radius:10px}
table.gv-table{width:100%;border-collapse:collapse;min-width:720px}
table.gv-table th,table.gv-table td{border-bottom:1px solid #e5e7eb;padding:10px;text-align:left;vertical-align:top}
table.gv-table th{background:#f1f2f4;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#6b7280}
table.gv-table tr:last-child td{border-bottom:none}
details.gv-worklog{margin-top:6px;border:1px solid #e5e7eb;border-radius:8px;background:#fafafa}
details.gv-worklog summary{cursor:pointer;padding:8px 10px;font-weight:600;user-select:none}
details.gv-worklog[open] summary{border-bottom:1px solid #e5e7eb}
.gv-wl-inner{padding:10px}
table.gv-wl-summary{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:10px}
table.gv-wl-summary th,table.gv-wl-summary td{border:1px solid #e5e7eb;padding:4px 8px;text-align:left}
.gv-step{border-left:3px solid #4f46e5;padding:6px 8px;margin:6px 0;background:#fff;border-radius:0 6px 6px 0}
.gv-step .gv-step-head{font-weight:700;font-size:12px}
.gv-step .gv-thought{font-style:italic;color:#374151;margin:4px 0;white-space:pre-wrap;word-break:break-word}
.gv-step .gv-obs{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:6px 8px;margin:4px 0;font-size:12px;white-space:pre-wrap;word-break:break-word}
.gv-step .gv-toolcall{background:#eef2ff;border:1px solid #c7d2fe;border-radius:6px;padding:6px 8px;margin:4px 0;font-size:12px;word-break:break-word}
.gv-trace{max-height:""" + _TRACE_MAX_HEIGHT + """;overflow:auto}
"""

def _esc(value) -> str:
    """HTML-escapes a value for safe embedding (answers, questions, traces)."""
    return html.escape("" if value is None else str(value))


def _fmt_score(value) -> str:
    """Formats a server score (0-100) for display; ``None`` becomes 'N/A'."""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return _esc(value)


def _fmt_count(value) -> str:
    """Formats a count for display; ``None`` becomes an em dash."""
    return "—" if value is None else str(value)


def _safe_token(value, default: str = "") -> str:
    """Coerces a value into a CSS-class-safe token (letters, digits, '-', '_').

    The token is embedded in ``class="gv-..."`` attribute values, so anything
    outside a safe alphabet is stripped — even a crafted ``level``/``source``/
    ``status`` from the server or Agent cannot break out of the attribute. The
    original value is still shown (HTML-escaped) in the badge's text.
    """
    token = re.sub(r"[^A-Za-z0-9_-]", "", str(value or "")).lower()
    return token or default


def _badge(text: str, kind: str) -> str:
    """A small coloured label (level/source/status).

    ``text`` (the visible value) is HTML-escaped; ``kind`` (the CSS class
    token) is sanitized by ``_safe_token`` so it cannot carry markup.
    """
    token = _safe_token(kind, "?") or "?"
    return f'<span class="gv-badge gv-{token}">{_esc(text)}</span>'


def _level_badge(level) -> str:
    value = "?" if level in (None, "") else str(level).strip()
    return _badge(f"L{value}", f"level{_safe_token(value, '?')}")


def _source_badge(source) -> str:
    value = source or "?"
    return _badge(value, f"source-{_safe_token(value, 'unknown')}")


def _status_badge(status) -> str:
    value = status or "?"
    return _badge(value, f"status-{_safe_token(value, 'unknown')}")


def _render_tool_args(arguments) -> str:
    """Renders a ToolCall's arguments as compact JSON for the trace.

    Large payloads (e.g. code actions) are shortened to the first
    ``_TOOL_ARGS_DISPLAY_LIMIT`` characters so a trace row stays readable.
    """
    try:
        text = json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(arguments)
    if len(text) > _TOOL_ARGS_DISPLAY_LIMIT:
        text = text[:_TOOL_ARGS_DISPLAY_LIMIT] + " …"
    return text


def _steps_label(count: int) -> str:
    """"1 step" / "3 steps" — a singular/plural step-count label."""
    return f"{count} step{'' if count == 1 else 's'}"


def _step_html(step: dict) -> str:
    """Renders one Worklog trace step (thought / tool call / observation)."""
    tool_calls = step.get("tool_calls") or []
    calls = "".join(
        f'<div class="gv-toolcall gv-mono"><b>{_esc(c.get("name", "unknown"))}</b>'
        f"({_esc(_render_tool_args(c.get('arguments')))} )</div>"
        for c in tool_calls
    )
    obs = step.get("observations") or ""
    obs_html = f'<div class="gv-obs">{_esc(obs)}</div>' if obs else ""
    thought = step.get("thought") or ""
    thought_html = f'<div class="gv-thought">{_esc(thought)}</div>' if thought else ""

    dur = step.get("duration_sec")
    dur_html = "" if dur is None else f" · {dur}s"
    step_type = step.get("type", "action")

    error = step.get("error")
    error_html = (
        f'<div class="gv-obs" style="border-color:#fecaca;background:#fef2f2">'
        f"Error: {_esc(error)}</div>"
    ) if error else ""

    return (
        f'<div class="gv-step">'
        f'<div class="gv-step-head">Step {_esc(step.get("step_number", "?"))} · '
        f"{_esc(step_type)}{dur_html}</div>"
        f"{thought_html}{calls}{obs_html}{error_html}</div>"
    )


def _tool_summary_html(summary: list) -> str:
    """Renders the per-tool summary table (name / calls / total / avg)."""
    if not summary:
        return '<div class="gv-muted">No tool calls.</div>'
    rows = "".join(
        f"<tr><td class=\"gv-mono\">{_esc(t.get('name', '?'))}</td>"
        f"<td>{_esc(t.get('calls'))}</td>"
        f"<td>{_esc(t.get('total_sec'))}</td>"
        f"<td>{_esc(t.get('avg_sec'))}</td></tr>"
        for t in summary
    )
    return (
        '<table class="gv-wl-summary"><thead><tr>'
        "<th>Tool</th><th>Calls</th><th>Total (s)</th><th>Avg (s)</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def _worklog_body(worklog) -> str:
    """Renders a Worklog's inner content (tool summary + full trace)."""
    if not worklog:
        return '<div class="gv-muted">No worklog recorded for this Task.</div>'
    steps = worklog.get("steps") or []
    total = worklog.get("total_duration_sec")
    total_html = "" if total is None else f", {total}s"

    # A failed live solve records the failure at the Worklog's top level
    # (text.py sets worklog["error"]); surface it rather than an empty trace.
    error = worklog.get("error")
    error_html = (
        f'<div class="gv-obs" style="border-color:#fecaca;background:#fef2f2">'
        f"Error: {_esc(error)}</div>"
    ) if error else ""

    steps_html = "".join(_step_html(s) for s in steps)
    trace = (
        f'<div class="gv-trace">{steps_html}</div>'
        if steps_html
        else '<div class="gv-muted">No steps recorded.</div>'
    )
    return (
        '<div class="gv-wl-inner">'
        f"{_tool_summary_html(worklog.get('tool_summary') or [])}"
        f"{error_html}"
        f'<div class="gv-muted" style="margin:4px 0">'
        f"Full trace ({_steps_label(len(steps))}{total_html}):"
        f"</div>{trace}</div>"
    )


def _worklog_details(worklog) -> str:
    """An expandable Worklog block (tool summary + full trace via <details>)."""
    steps = (worklog or {}).get("steps") or []
    label = f"Worklog · {_steps_label(len(steps))}"
    return (
        f'<details class="gv-worklog"><summary>{_esc(label)}</summary>'
        f"{_worklog_body(worklog)}</details>"
    )


def _score_card_html(score_card: dict) -> str:
    """The score card: a 4-up stat grid plus the server message and username."""
    def _stat(key: str, label: str, formatter=_esc) -> str:
        value = score_card.get(key)
        return (
            f'<div class="gv-stat"><div class="gv-k">{_esc(label)}</div>'
            f'<div class="gv-v">{formatter(value)}</div></div>'
        )

    grid = (
        _stat("score", "Overall Score", _fmt_score)
        + _stat("correct_count", "Correct", _fmt_count)
        + _stat("total_attempted", "Attempted", _fmt_count)
        + _stat("timestamp", "Submitted at")
    )

    message = score_card.get("message")
    user = score_card.get("username")
    message_html = ""
    if message is not None or user is not None:
        parts = []
        if message is not None:
            parts.append(_esc(message))
        if user is not None:
            parts.append(f'<span class="gv-muted">· user {_esc(user)}</span>')
        message_html = f'<div class="gv-message">{" ".join(parts)}</div>'

    return f'<div class="gv-grid">{grid}</div>{message_html}'


def _row_html(row: dict) -> str:
    """One row of the per-Question table."""
    task_id = row.get("task_id")
    question = row.get("question") or ""
    answer = row.get("answer") or ""
    timestamp = row.get("timestamp") or ""

    question_cell = f"{_esc(question)}"
    if task_id:
        question_cell += f'<div class="gv-muted gv-mono" style="margin-top:4px">{_esc(task_id)}</div>'

    answer_cell = f"<b>{_esc(answer)}</b>"
    if timestamp:
        answer_cell += f'<div class="gv-muted" style="font-size:11px">{_esc(timestamp)}</div>'

    return (
        "<tr>"
        f"<td>{_level_badge(row.get('level'))}</td>"
        f'<td style="min-width:260px">{question_cell}</td>'
        f"<td>{answer_cell}</td>"
        f"<td>{_source_badge(row.get('source'))}</td>"
        f"<td>{_status_badge(row.get('status'))}</td>"
        f'<td style="min-width:300px">{_worklog_details(row.get("worklog"))}</td>'
        "</tr>"
    )


def _table_html(rows: list) -> str:
    """The per-Question table of submitted Answers."""
    if not rows:
        return '<div class="gv-muted">No Tasks to show.</div>'
    body = "".join(_row_html(r) for r in rows)
    return (
        '<div class="gv-table-wrap"><table class="gv-table"><thead><tr>'
        "<th>Level</th><th>Question</th><th>Submitted Answer</th>"
        "<th>Source</th><th>Status</th><th>Worklog</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )


def render_results_view(view: dict) -> str:
    """Renders the full Benchmark results view (score card + table + Worklogs).

    Args:
        view: The orchestration results view
            ``{"score_card": {...}, "rows": [{task_id, question, level,
            file_name, answer, source, status, timestamp, worklog}]}``.

    Returns:
        A self-contained HTML fragment. Safe for embedding in ``gr.HTML``;
        every value from the server / Agent is HTML-escaped. A missing or
        empty view renders a well-formed empty state rather than raising.
    """
    if not view:
        return '<div class="gv-root"><div class="gv-muted">No results yet.</div></div>'
    score_card = view.get("score_card") or {}
    rows = view.get("rows") or []
    return (
        '<div class="gv-root">'
        f"<style>{_CSS}</style>"
        f"{_score_card_html(score_card)}"
        f"{_table_html(rows)}"
        "</div>"
    )
