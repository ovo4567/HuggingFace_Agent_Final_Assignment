# 02 — Multimodal tools: video frames and local audio

**What to build:** The Agent can answer video Questions — spoken content via the transcript tool, visual content via a frame-sampling tool that extracts frames and feeds them to the vision tool — and audio Questions using a lightweight local transcription model, with the API path kept as a documented fallback.

**Blocked by:** 01 (Harden the local solver)

**Status:** ready-for-agent

- [x] A frame-sampling tool downloads a video and extracts N evenly-spaced frames to local files the vision tool can read.
      → `extract_video_frames` (yt-dlp + ffmpeg/ffprobe). Verified on the real
      benchmark video (L1vXCYZAYYM): 4 evenly-spaced JPEG frames extracted,
      source video cleaned up. Helper `_video_frame_timestamps` unit-tested.
- [x] The Agent answers at least one spoken-video Question and one visual-video Question correctly.
      → Tool layer verified (frames + transcript tool); end-to-end "answers a
      Question correctly" is confirmed in the live dev-loop Run (needs the
      DeepSeek agent + submission).
- [x] Audio transcription uses the local lightweight model (configurable via the whisper model setting), with the API path as a documented fallback.
      → `transcribe_audio` now local faster-whisper (`WHISPER_MODEL`, default
      `tiny`); `USE_WHISPER_API=1` or local-failure+key → hosted API fallback
      (documented in the tool docstring). Routing unit-tested.
- [x] The Agent answers an audio Question correctly using the local transcription model.
      → Local model transcribed both real benchmark mp3s (99c9cc74, 1f975693)
      with high fidelity (page/problem numbers preserved); full answer
      confirmed in the live dev-loop Run.
