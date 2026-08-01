# Deployment runbook

How the GAIA Solver is deployed and kept in sync across the HF Space
(primary) and the GitHub mirror.

## Topology

- **Primary:** Hugging Face Space — `origin` →
  `https://huggingface.co/spaces/ovo456/Final_Assignment_Template`
- **Mirror:** GitHub — same `main` branch (see "Add the GitHub mirror"
  below). Exists for visibility and history preservation.

The Space reads the committed `answer_bundle.json` and submits it at runtime
(ADR-0001 hybrid execution); the GitHub mirror carries the same history.

## Environment variables & Space secrets

The full env-var table lives in [`README.md`](../README.md). The only
**Space secrets** needed are:

| Secret | Required? | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | Required for live solves | Lets the Space solve unbundled Tasks (text/web-only). |
| `OPENAI_API_KEY` or `OPENROUTER_API_KEY` | Optional | Hosted audio-transcription fallback. |

Set them in the Space: **Settings → Variables and secrets → New variable**.

## Push flow (both remotes)

```bash
# Push the same main to the HF Space (primary)
git push origin main

# Push to the GitHub mirror
git remote add github https://github.com/<owner>/<repo>.git   # one-time
git push github main
```

## Dev loop (regenerate the Answer bundle)

```bash
cp .env.example .env          # fill in DEEPSEEK_API_KEY, GAIA_API_URL, ...
pip install -r requirements.txt
python text.py                # solves + writes answer_bundle.json and gaia_results.csv
git add answer_bundle.json
git commit -m "Regenerate Answer bundle"
git push origin main          # the Space picks up the new bundle
```

## Notes

- `files/` (pre-downloaded Attachments) and `.env` are gitignored — never
  force-add them.
- Agent-internal tooling (`.agents/`, `.scratch/`, `skills-lock.json`,
  `SESSION_HANDOFF.md`) is gitignored and kept out of the shared repo.
- The results-view prototype lives on branch `prototype/results-view` (out of
  `main`).
