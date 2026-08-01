---
title: Template Final Assignment
emoji: 🕵🏻‍♂️
colorFrom: indigo
colorTo: indigo
sdk: gradio
sdk_version: 5.25.2
app_file: app.py
pinned: false
hf_oauth: true
# optional, default duration is 8 hours/480 minutes. Max duration is 30 days/43200 minutes.
hf_oauth_expiration_minutes: 480
---

# GAIA Solver

A single ReAct agent that answers [GAIA](https://huggingface.co/datasets/gaia-benchmark/GAIA)
benchmark Questions, submits its Answers to a scoring server, and presents its
Worklog and Benchmark results on the Space.

## How it works

- **Local solving** (`text.py`): a single hardened smolagents `CodeAgent` solves
  Questions with web search, lightweight browsing, code execution, and local
  multimodal tools (vision = Qwen2-VL, audio = faster-whisper, video =
  transcript + frame sampling). Each local Run writes a committed **Answer
  bundle** (`answer_bundle.json`, keyed by `task_id`) alongside
  `gaia_results.csv`.
- **Hybrid submission** (`orchestration.py` + `app.py`, ADR-0001): the Space
  submits the pre-computed Answer bundle for every Task it recognizes — no
  re-solving — and runs the live Agent (text/web-only per ADR-0002) only for
  Tasks missing from the bundle.
- **Benchmark results** (`results_view.py`): after submission, the Space shows a
  score card, a per-Question table of submitted Answers, and an expandable
  per-Task Worklog.

## Quick start (local dev loop)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure secrets (never committed)
cp .env.example .env        # then fill in DEEPSEEK_API_KEY, GAIA_API_URL, ...

# 3. Solve the served Questions and regenerate the Answer bundle + CSV
python text.py

# 4. Commit the regenerated Answer bundle so the Space submits it
#    (the bundle is deliberately NOT gitignored)
git add answer_bundle.json
git commit -m "Regenerate Answer bundle"
git push origin main
```

The Space then submits the bundle on the next **Run Evaluation & Submit All
Answers** click.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | ✅ live solves | — | DeepSeek key for the ReAct Agent and the answer-cleaning pass. **Set as an HF Space secret.** |
| `GAIA_API_URL` | — | `https://agents-course-unit4-scoring.hf.space` | Scoring server base URL (`/questions`, `/files/{task_id}`, `/submit`). |
| `SPACE_ID` | — | — | HF Space id, e.g. `ovo456/Final_Assignment_Template`; used to build the `agent_code` link. Auto-set on the Space. |
| `SPACE_HOST` | — | — | Runtime host, printed at startup. Auto-set on the Space. |
| `DEEPSEEK_API_BASE` | — | `https://api.deepseek.com/v1` | OpenAI-compatible base URL for DeepSeek. |
| `DEEPSEEK_AGENT_MODEL` | — | `deepseek-chat` | Reasoning model for the Agent. |
| `DEEPSEEK_CLEANING_MODEL` | — | `deepseek-chat` | Model for the final-answer cleaning pass. |
| `GAIA_NUM_WORKERS` | — | `4` | Parallel Agent workers in a local Run. |
| `CLEANING_MAX_TOKENS` | — | `1024` | Max tokens for the cleaning pass (raised so long list Answers aren't truncated). |
| `GAIA_TASK_TIMEOUT` | — | `600` | Per-Task safety timeout (seconds). |
| `GAIA_RESULTS_CSV` | — | `gaia_results.csv` | Local Run results CSV path. |
| `ANSWER_BUNDLE_PATH` | — | `<repo>/answer_bundle.json` | Answer bundle path (committed artifact). |
| `VISION_MODEL_ID` | — | `Qwen/Qwen2-VL-2B-Instruct` | Local vision model (local-only per ADR-0002). |
| `LOCAL_DEVICE` | — | `auto` | Local compute device for Qwen vision + faster-whisper: `auto` (probe MPS→CUDA→CPU) \| `cpu` \| `mps` \| `cuda`. Whisper clamps `mps→cpu` (CTranslate2 has no MPS). |
| `WHISPER_MODEL` | — | `tiny` | Local faster-whisper model size. |
| `USE_WHISPER_API` | — | off | Set `1`/`true` to prefer the hosted Whisper API over the local model. |
| `OPENAI_API_KEY` | — | — | Hosted Whisper fallback (`whisper-1`). |
| `OPENROUTER_API_KEY` | — | — | Hosted Whisper fallback (`openai/whisper-large-v3-turbo`). |
| `VIDEO_FRAMES_COUNT` | — | `4` | Frames sampled per video. |
| `VIDEO_FRAMES_DIR` | — | `<repo>/files/_video_frames` | Where sampled frames are written during a Run. |
| `HUGGINGFACE_HUB_TOKEN` | — | — | For pushing/deploying to the HF Space (local only; **never** a Space secret). |

## HF Space secrets

Set these in the Space: **Settings → Variables and secrets → New variable**.

- `DEEPSEEK_API_KEY` — required so the live fallback can solve unbundled Tasks.
  (A fully-bundled Run needs no key.)
- Optional: `OPENAI_API_KEY` or `OPENROUTER_API_KEY` — hosted audio-transcription
  fallback.

## Deployment

The HF Space is the **primary** repo (`origin`); GitHub is a **mirror** remote
receiving the same `main` branch. See [docs/deployment.md](docs/deployment.md)
for the full runbook — adding the GitHub mirror, setting Space secrets, and the
push flow.

## Security

- `.env` and `files/` (pre-downloaded Attachments) are gitignored and never
  committed.
- API keys are read from environment variables / Space secrets, never hard-coded.
- Safe zip extraction (`_safe_extract_all`) guards against zip-slip on
  untrusted Attachments.