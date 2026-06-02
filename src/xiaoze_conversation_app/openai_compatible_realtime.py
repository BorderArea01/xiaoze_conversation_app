import logging
from typing import Any

from openai import AsyncOpenAI
from openai.types.realtime import (
    AudioTranscriptionParam,
    RealtimeAudioConfigParam,
    RealtimeAudioConfigInputParam,
    RealtimeAudioConfigOutputParam,
    RealtimeSessionCreateRequestParam,
)
from openai.types.realtime.realtime_audio_formats_param import AudioPCM
from openai.types.realtime.realtime_audio_input_turn_detection_param import ServerVad

from xiaoze_conversation_app.config import OPENAI_COMPATIBLE_BACKEND, config
from xiaoze_conversation_app.base_realtime import to_realtime_tools_config
from xiaoze_conversation_app.openai_realtime import OpenaiRealtimeHandler


logger = logging.getLogger(__name__)

__all__ = ["OpenAICompatibleRealtimeHandler"]


class OpenAICompatibleRealtimeHandler(OpenaiRealtimeHandler):
    """Realtime handler for OpenAI-compatible realtime providers.

    This is intended for vendors that implement the OpenAI Realtime websocket
    protocol. It does not adapt plain chat-completions models into voice.
    """

    BACKEND_PROVIDER = OPENAI_COMPATIBLE_BACKEND
    REFRESH_CLIENT_ON_RECONNECT = True

    def _persist_credentials_if_needed(self) -> None:
        """Do not persist generic provider credentials from the OpenAI textbox."""
        return

    def _get_session_config(self, tool_specs: list[dict[str, Any]]) -> RealtimeSessionCreateRequestParam:
        """Return an OpenAI-compatible realtime session config."""
        return RealtimeSessionCreateRequestParam(
            type="realtime",
            instructions=self._get_session_instructions(),
            audio=RealtimeAudioConfigParam(
                input=RealtimeAudioConfigInputParam(
                    format=AudioPCM(type="audio/pcm", rate=24000),
                    transcription=AudioTranscriptionParam(
                        model=config.OPENAI_COMPATIBLE_TRANSCRIPTION_MODEL,
                        language="en",
                    ),
                    turn_detection=ServerVad(type="server_vad", interrupt_response=True),
                ),
                output=RealtimeAudioConfigOutputParam(
                    format=AudioPCM(type="audio/pcm", rate=24000),
                    voice=self.get_current_voice(),
                ),
            ),
            tools=to_realtime_tools_config(tool_specs),
            tool_choice="auto",
        )

    async def _build_realtime_client(self) -> AsyncOpenAI:
        """Build an OpenAI-compatible realtime SDK client."""
        self._realtime_connect_query = {}
        resolved_api_key = (config.OPENAI_COMPATIBLE_API_KEY or "").strip()
        if not resolved_api_key:
            logger.warning("OPENAI_COMPATIBLE_API_KEY missing. Proceeding with a placeholder (tests/offline).")
            resolved_api_key = "DUMMY"

        kwargs: dict[str, str] = {"api_key": resolved_api_key}
        base_url = (config.OPENAI_COMPATIBLE_BASE_URL or "").strip()
        websocket_base_url = (config.OPENAI_COMPATIBLE_WEBSOCKET_BASE_URL or "").strip()
        if base_url:
            kwargs["base_url"] = base_url
        if websocket_base_url:
            kwargs["websocket_base_url"] = websocket_base_url
        return AsyncOpenAI(**kwargs)
