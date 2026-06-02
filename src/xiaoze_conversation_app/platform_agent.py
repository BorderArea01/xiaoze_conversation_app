from __future__ import annotations
import json
import logging
from typing import Any

from fastrtc import AdditionalOutputs
from openai.types.realtime import RealtimeResponseCreateParamsParam

from xiaoze_conversation_app.config import PLATFORM_AGENT_BACKEND
from xiaoze_conversation_app.platform_client import PlatformClient, PlatformClientConfig
from xiaoze_conversation_app.huggingface_realtime import HuggingFaceRealtimeHandler
from xiaoze_conversation_app.openai_compatible_chat import OpenAICompatibleChatHandler


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


class PlatformAgentRealtimeHandler(HuggingFaceRealtimeHandler):
    """Use Hugging Face realtime ASR/TTS while routing LLM turns to the platform WS."""

    BACKEND_PROVIDER = PLATFORM_AGENT_BACKEND
    SAMPLE_RATE = HuggingFaceRealtimeHandler.SAMPLE_RATE
    REFRESH_CLIENT_ON_RECONNECT = HuggingFaceRealtimeHandler.REFRESH_CLIENT_ON_RECONNECT
    AUDIO_INPUT_COST_PER_1M = HuggingFaceRealtimeHandler.AUDIO_INPUT_COST_PER_1M
    AUDIO_OUTPUT_COST_PER_1M = HuggingFaceRealtimeHandler.AUDIO_OUTPUT_COST_PER_1M
    TEXT_INPUT_COST_PER_1M = HuggingFaceRealtimeHandler.TEXT_INPUT_COST_PER_1M
    TEXT_OUTPUT_COST_PER_1M = HuggingFaceRealtimeHandler.TEXT_OUTPUT_COST_PER_1M
    IMAGE_INPUT_COST_PER_1M = HuggingFaceRealtimeHandler.IMAGE_INPUT_COST_PER_1M

    def __init__(
        self,
        deps: Any,
        gradio_mode: bool = False,
        instance_path: str | None = None,
        startup_voice: str | None = None,
    ) -> None:
        """Initialize the platform agent realtime handler."""
        super().__init__(deps, gradio_mode, instance_path, startup_voice)
        self.platform_client = PlatformClient(PlatformClientConfig.from_env())

    def _get_session_config(self, tool_specs: list[dict[str, Any]]):
        session_config = super()._get_session_config(tool_specs)
        turn_detection = session_config["audio"]["input"]["turn_detection"]
        turn_detection["create_response"] = False
        return session_config

    async def _handle_completed_user_transcript(self, transcript: str) -> None:
        """Send ASR text to the platform WS, then ask realtime to speak the reply."""
        await self.output_queue.put(AdditionalOutputs({"role": "user", "content": transcript}))
        responses = await self.platform_client.send_asr_signal(transcript, response_timeout_s=8.0)
        chunks: list[str] = []
        for response in responses:
            chunk = _extract_text(response).strip()
            if chunk:
                chunks.append(chunk)
        reply = "".join(chunks).strip()
        if not reply:
            logger.info("Platform agent returned no text for transcript=%r", transcript)
            return

        await self.output_queue.put(AdditionalOutputs({"role": "assistant", "content": reply}))
        if not self.connection:
            logger.warning("Realtime connection missing; cannot synthesize platform reply")
            return

        await self._safe_response_create(
            response=RealtimeResponseCreateParamsParam(
                conversation="none",
                input=[
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": f"请只朗读下面这段内容，不要解释，不要改写：\n{reply}",
                            }
                        ],
                    }
                ],
                instructions="只把用户提供的内容转成自然语音。不要添加开场白、解释、总结或额外内容。",
                output_modalities=["audio"],
            ),
        )
