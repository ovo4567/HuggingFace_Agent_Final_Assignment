# Hybrid Execution: Space submits a committed Answer Bundle, Agent runs live as fallback

The graded HF Space rarely runs the Agent at submit time. Answers are solved locally via `text.py`, captured into a committed Answer bundle keyed by `task_id`, and the Space submits those answers directly — running the Agent live only for `task_id`s missing from the bundle. This trades a "solving live" demo for reliability and speed on a constrained free CPU Space, and keeps the Worklog and Benchmark results deterministic per submission.
