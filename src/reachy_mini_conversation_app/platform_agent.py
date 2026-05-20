from __future__ import annotations
import json
import logging
from typing import Any

from reachy_mini_conversation_app.config import PLATFORM_AGENT_BACKEND
from reachy_mini_conversation_app.platform_client import PlatformClient, PlatformClientConfig
from reachy_mini_conversation_app.openai_compatible_chat import OpenAICompatibleChatHandler


logger = logging.getLogger(__name__)


def _extract_text(value: Any) -> str:
    """Best-effort extraction of streamed platform agent text."""
    if isinstance(value, str):
        try:
            return _extract_text(json.loads(value))
        except Exception:
            return value
    if isinstance(value, dict):
        if value.get("type") == "TTS_MESSAGE" and isinstance(value.get("data"), dict):
            data = value["data"]
            if data.get("type") != "0":
                return ""
            return str(data.get("text") or data.get("content") or "")
        for key in (
            "answer",
            "content",
            "text",
            "delta",
            "message",
            "response",
            "result",
        ):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item
        data = value.get("data")
        if isinstance(data, (dict, str)):
            return _extract_text(data)
    return ""


class PlatformAgentHandler(OpenAICompatibleChatHandler):
    """Conversation handler that uses the platform agent as the core brain."""

    BACKEND_PROVIDER = PLATFORM_AGENT_BACKEND

    def __init__(
        self,
        deps: Any,
        gradio_mode: bool = False,
        instance_path: str | None = None,
        startup_voice: str | None = None,
    ) -> None:
        """Initialize ASR/TTS plus the platform terminal client."""
        super().__init__(deps, gradio_mode, instance_path, startup_voice)
        self.platform_client = PlatformClient(PlatformClientConfig.from_env())

    async def _chat(self, transcript: str) -> str:
        """Send transcript to the platform agent and merge streamed replies."""
        responses = await self.platform_client.send_asr_signal(transcript, response_timeout_s=8.0)
        chunks: list[str] = []
        for response in responses:
            chunk = _extract_text(response).strip()
            if chunk:
                chunks.append(chunk)
        reply = "".join(chunks).strip()
        if not reply:
            logger.info("Platform agent returned no text for transcript=%r", transcript)
        return reply
