# Local-only multimodal: Qwen2-VL-2B and local Whisper, no hosted vision/audio

Image and audio understanding run on local models (Qwen2-VL-2B and faster-whisper) during local solving, not hosted APIs. This keeps the pipeline free and private. Deliberate rejection of the OpenRouter-vision and Whisper-API paths that were partially coded: the live-on-Space fallback therefore cannot solve image or audio Tasks (no GPU, no hosted key), which is acceptable because the Answer bundle covers the stable question set.

## Update (2026-07-31, ticket 02): documented exception for hosted audio fallback

Ticket 02 ("multimodal tools") mandates a carve-out to the "no hosted audio"
rule above: the local faster-whisper path remains the default
(`WHISPER_MODEL`), but the hosted Whisper API (OpenAI/OpenRouter) is kept as
an **explicit fallback** — used only when `USE_WHISPER_API=1` is set, or the
local model fails *and* a hosted key is configured. This exception is required
by the ticket's acceptance criteria and is documented in the
`transcribe_audio` docstring. Vision remains strictly local-only (Qwen2-VL);
the live-on-Space fallback is still text/web-only.

## Update (2026-08-01, Part A device auto-selection)

The local models now auto-select their compute device via the `LOCAL_DEVICE`
setting (`auto` probes Apple MPS → CUDA → CPU, else CPU). faster-whisper always
runs on CPU on Apple Silicon because CTranslate2 — its inference backend — has
no MPS support; the resolver clamps `mps → cpu` for whisper
(`compute_type="int8"` on CPU, `"float16"` on CUDA). Qwen2-VL uses MPS/CUDA when
available (`float16`) and CPU otherwise (`float32`). Vision remains local-only.

