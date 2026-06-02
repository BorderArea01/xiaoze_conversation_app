# Changelog

## 2026-06-02

### Added

- Synced the currently deployed `xiaoze_conversation_app` package from the Raspberry Pi into the local source tree.
- Added a `/personalities/delete` route from the deployed app snapshot.

### Fixed

- Added an Aliyun realtime backend option for `qwen3.5-omni-flash-realtime`, `qwen3-omni-flash-realtime`, and `qwen-omni-turbo-realtime`.
- Reworked the settings UI around two clearer modes: recommended Aliyun realtime voice and advanced split ASR / LLM / TTS.
- Added realtime model persistence through `model_name` so backend switches write the intended model cleanly.
- Improved robot conversation monitor state so stopped audio loops report `running=false` and expose the last loop error.
- Started the robot-side local audio loop in SPA mode so the Raspberry Pi microphone and speaker handle conversation instead of the browser microphone.
- Added `/conversation/status`, `/conversation/messages`, and `/conversation/text` for monitoring robot-side audio levels, chat history, and typed turns.
- Changed the main voice UI into a robot conversation monitor with robot microphone/speaker levels and local handler text input.
- Recorded backend conversation errors in the monitor chat history so failed turns no longer look like silent UI failures.
- Streamlined the xiaoze settings UI so credentials no longer appear as two large always-visible panels.
- Added compact provider tabs and credential fields that only appear for the providers currently in use.
- Changed the microphone visualizer to read the local microphone signal, so speaking into the mic now drives the audio level animation.
- Made backend config saves surface the server result message and refresh credential status immediately after saving.
- Added `has_openai_compatible_key` to the SPA status response so the UI can accurately decide which credential is still missing.
- Prevented the xiaoze movement loop from commanding `body_yaw=0` during idle, breathing, head tracking offsets, and head-only moves.
- Updated `move_head` so it no longer treats antenna joint values as body yaw or resets the body when looking left/right/up/down/front.
- Kept shutdown cleanup from rotating the body by leaving `body_yaw` uncontrolled.
- Normalized Aliyun TTS voice selection so invalid OpenAI voices such as `alloy` are replaced with the Aliyun-compatible default `Ethan`.
- Made the composed backend readiness check accurately report missing credentials:
  - `has_aliyun_key=false` when `ALIYUN_API_KEY` is absent.
  - `can_proceed=false` when ASR/LLM/TTS are configured for Aliyun but no Aliyun key is present.
- Rejected composed backend saves that select Aliyun components without an Aliyun API key.

### Daemon

- Removed the daemon-level forced neutral pose after stopping an app, so starting/stopping/restarting xiaoze no longer triggers an extra robot reset movement.

### Deployment Notes

- Deployed the updated xiaoze files to the Raspberry Pi package path:
  `/venvs/apps_venv/lib/python3.13/site-packages/xiaoze_conversation_app`
- Deployed the daemon `manager.py` fix to:
  `/home/roger/app/reachy_mini/src/reachy_mini/apps/manager.py`
- Restarted `reachy-mini-daemon.service`.
- Verified:
  - Daemon service is active.
  - `xiaoze_conversation_app` starts through the daemon app manager.
  - `http://127.0.0.1:7860/ready` returns ready.
  - `http://127.0.0.1:7860/status` reports composed Aliyun config with `TTS_VOICE=Ethan`.
  - Saving the current Aliyun backend config without re-entering the key returns `requires_restart=false` and keeps `has_aliyun_key=true`.
  - `http://127.0.0.1:7860/conversation/status` reports `running=true` with live robot microphone input level.
  - `http://127.0.0.1:7860/conversation/text` reaches the local handler and records errors in the monitor history.

### Known Remaining Issue

- The currently configured Aliyun key reaches the LLM provider but returns `Model.AccessDenied` for tested Qwen chat models, including `qwen-plus`, so ASR -> LLM -> TTS cannot complete until an authorized chat model/key is configured.
- `qwen3.6-plus` is available for LLM text generation with the current Aliyun key, but tested Aliyun realtime and TTS models still return `Model.AccessDenied` when an actual response is requested.
