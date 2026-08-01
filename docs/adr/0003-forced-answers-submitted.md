# Forced Answers are submitted for scoring; errors and timeouts are not

At the per-Task soft deadline, the solver stops the Agent and synthesizes a
best-effort Answer from its partial Worklog (status `forced`). Unlike `error`
and `timeout` results — which are never submitted, because an error string
would be scored as a guaranteed-wrong attempt — a `forced` Answer **is**
submitted and scored. It is a real attempt: the cleaning model produces a
candidate from whatever evidence the Agent gathered before being stopped, so
it has a genuine chance of matching the correct Answer, whereas an error or
timeout carries no candidate at all. Treating `forced` like `error`/`timeout`
would forgo those points for no gain. Implemented as Part B (two-stage
per-Task timeout, commit 6a102b8); the `forced` status is folded into the
Answer bundle and rendered with a distinct badge in the results view.
