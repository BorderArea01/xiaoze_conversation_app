# Reachy Mini Conversation App Build/Deploy Plan

## Goal

Turn this repository into a Reachy Mini Python app that can be installed locally through the Reachy Mini daemon/dashboard and published as a Hugging Face Space for community installation.

## References

- Build model: https://github.com/pollen-robotics/reachy_mini/tree/main/docs/SDK
- Deploy model: https://huggingface.co/blog/pollen-robotics/make-and-publish-your-reachy-mini-apps

## App Type

This project is a Python Reachy Mini app, not a static JS app. It needs local robot access, realtime audio, motion tools, optional camera processing, and platform websocket integration.

## Implementation Notes

- Python app entry point is `XiaozeConversationApp`.
- Package entry point is declared in `pyproject.toml` under `xiaoze_apps`.
- Runtime settings UI lives inside `src/xiaoze_conversation_app/static`.
- Platform agent settings are stored in the per-instance `.env` by the settings UI.
- Secrets must not be committed; use local `.env`, dashboard settings, or Hugging Face Space secrets.

## Build Steps

1. Install locally with `pip install -e .` or `uv sync`.
2. Run static checks and tests.
3. Run `reachy-mini-app-assistant check`.
4. Test from the daemon dashboard.

## Deploy Steps

1. Authenticate Hugging Face CLI with a write token.
2. Run `reachy-mini-app-assistant publish`.
3. Choose public/private Space.
4. Configure runtime secrets after install, not in source control.

## Confirmation Needed

- Hugging Face namespace and final app slug.
- Whether the Space should be public or private.
- Final production values for platform URLs and digital employee ID.
