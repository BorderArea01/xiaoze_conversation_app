from __future__ import annotations

import inspect
import json
import logging
import os
import uuid
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import websockets

from fastrtc import AdditionalOutputs

from xiaoze_conversation_app.base_realtime import BaseRealtimeHandler
from xiaoze_conversation_app.config import ALIYUN_BACKEND, config, get_default_voice_for_backend
from xiaoze_conversation_app.prompts import get_session_instructions, get_session_voice
from xiaoze_conversation_app.tools.core_tools import ToolDependencies, get_active_tool_specs


logger = logging.getLogger(__name__)

ALIYUN_REALTIME_BASE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

_EVENT_TYPE_MAP = {
    "response.audio.delta": "response.output_audio.delta",
    "response.audio.done": "response.output_audio.done",
    "response.audio_transcript.delta": "response.output_audio_transcript.delta",
    "response.audio_transcript.done": "response.output_audio_transcript.done",
}


def _to_plain_data(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _to_plain_data(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return {key: _to_plain_data(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_to_plain_data(item) for item in value]
    return value


def _to_namespace(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _to_namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_to_namespace(item) for item in value]
    return value


def _aliyun_tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    if tool.get("type") != "function":
        return tool
    if isinstance(tool.get("function"), dict):
        return tool
    return {
        "type": "function",
        "function": {
            "name": tool.get("name"),
            "description": tool.get("description") or "",
            "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
        },
    }


def _supports_function_calling(model_name: str) -> bool:
    candidate = model_name.strip().lower()
    if not candidate:
        return True
    if candidate.startswith("qwen3-omni-flash-realtime"):
        return False
    if candidate.startswith("qwen-omni-turbo-realtime"):
        return False
    return True


class _AliyunSessionAPI:
    def __init__(self, connection: "_AliyunRealtimeConnection") -> None:
        self._connection = connection

    async def update(self, *, session: Any) -> None:
        await self._connection.send({"type": "session.update", "session": self._connection.normalize_session(session)})


class _AliyunResponseAPI:
    def __init__(self, connection: "_AliyunRealtimeConnection") -> None:
        self._connection = connection

    async def create(self, **kwargs: Any) -> None:
        await self._connection.send({"type": "response.create"})


class _AliyunConversationItemAPI:
    def __init__(self, connection: "_AliyunRealtimeConnection") -> None:
        self._connection = connection

    async def create(self, *, item: dict[str, Any]) -> None:
        await self._connection.send({"type": "conversation.item.create", "item": _to_plain_data(item)})


class _AliyunConversationAPI:
    def __init__(self, connection: "_AliyunRealtimeConnection") -> None:
        self.item = _AliyunConversationItemAPI(connection)


class _AliyunInputAudioBufferAPI:
    def __init__(self, connection: "_AliyunRealtimeConnection") -> None:
        self._connection = connection

    async def append(self, *, audio: str) -> None:
        await self._connection.send({"type": "input_audio_buffer.append", "audio": audio})

    async def commit(self) -> None:
        await self._connection.send({"type": "input_audio_buffer.commit"})


class _AliyunRealtimeConnection:
    def __init__(self, *, api_key: str, base_url: str, model: str, extra_query: dict[str, str] | None = None) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._extra_query = extra_query or {}
        self._websocket: Any = None
        self.session = _AliyunSessionAPI(self)
        self.response = _AliyunResponseAPI(self)
        self.conversation = _AliyunConversationAPI(self)
        self.input_audio_buffer = _AliyunInputAudioBufferAPI(self)

    async def __aenter__(self) -> "_AliyunRealtimeConnection":
        url = f"{self._base_url}?{urlencode({'model': self._model, **self._extra_query})}"
        parameters = inspect.signature(websockets.connect).parameters
        header_arg = "additional_headers" if "additional_headers" in parameters else "extra_headers"
        self._websocket = await websockets.connect(url, **{header_arg: {"Authorization": f"Bearer {self._api_key}"}})
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def normalize_session(self, session: Any) -> dict[str, Any]:
        payload = _to_plain_data(session)
        audio = payload.get("audio") or {}
        input_audio = audio.get("input") or {}
        output_audio = audio.get("output") or {}
        transcription = input_audio.get("transcription") or payload.get("input_audio_transcription")
        turn_detection = input_audio.get("turn_detection") or payload.get("turn_detection")
        session_payload: dict[str, Any] = {
            "modalities": payload.get("modalities") or ["text", "audio"],
            "instructions": payload.get("instructions") or "",
            "voice": output_audio.get("voice") or payload.get("voice"),
            "input_audio_format": payload.get("input_audio_format") or "pcm16",
            "output_audio_format": payload.get("output_audio_format") or "pcm24",
        }
        if transcription is not None:
            session_payload["enable_input_audio_transcription"] = True
            session_payload["input_audio_transcription_model"] = "qwen3-asr-flash-realtime"
        if isinstance(turn_detection, dict) and turn_detection.get("type"):
            session_payload["turn_detection"] = {
                key: value
                for key, value in turn_detection.items()
                if key
                in {
                    "type",
                    "threshold",
                    "silence_duration_ms",
                    "prefix_padding_ms",
                    "create_response",
                    "interrupt_response",
                }
            }
        tools = payload.get("tools")
        if isinstance(tools, list):
            session_payload["tools"] = [_aliyun_tool_schema(tool) for tool in tools if isinstance(tool, dict)]
        if payload.get("tool_choice") is not None:
            session_payload["tool_choice"] = payload.get("tool_choice")
        return {key: value for key, value in session_payload.items() if value not in (None, "", [], {})}

    async def send(self, payload: dict[str, Any]) -> None:
        if self._websocket is None:
            raise RuntimeError("Aliyun realtime websocket is not connected")
        await self._websocket.send(json.dumps({"event_id": f"evt_{uuid.uuid4().hex}", **payload}, ensure_ascii=True))

    def _normalize_event(self, payload: dict[str, Any]) -> SimpleNamespace:
        normalized = dict(payload)
        event_type = normalized.get("type")
        if isinstance(event_type, str):
            normalized["type"] = _EVENT_TYPE_MAP.get(event_type, event_type)
        if event_type == "conversation.item.input_audio_transcription.delta" and "delta" not in normalized:
            normalized["delta"] = f"{normalized.get('text') or ''}{normalized.get('stash') or ''}"
        return _to_namespace(normalized)

    def __aiter__(self) -> "_AliyunRealtimeConnection":
        return self

    async def __anext__(self) -> SimpleNamespace:
        if self._websocket is None:
            raise StopAsyncIteration
        raw_message = await self._websocket.recv()
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8")
        return self._normalize_event(json.loads(raw_message))

    async def close(self) -> None:
        if self._websocket is not None:
            await self._websocket.close()
            self._websocket = None


class _AliyunRealtimeAPI:
    def __init__(self, *, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url

    def connect(self, *, model: str, extra_query: dict[str, str] | None = None) -> _AliyunRealtimeConnection:
        return _AliyunRealtimeConnection(
            api_key=self._api_key,
            base_url=self._base_url,
            model=model,
            extra_query=extra_query,
        )


class _AliyunRealtimeClient:
    def __init__(self, *, api_key: str, base_url: str) -> None:
        self.realtime = _AliyunRealtimeAPI(api_key=api_key, base_url=base_url)


class AliyunRealtimeHandler(BaseRealtimeHandler):
    BACKEND_PROVIDER = ALIYUN_BACKEND
    MODEL_NAME = config.MODEL_NAME or "qwen3.5-omni-flash-realtime"
    VOICE = get_default_voice_for_backend(ALIYUN_BACKEND)
    SAMPLE_RATE = 16000
    INPUT_SAMPLE_RATE = 16000
    OUTPUT_SAMPLE_RATE = 24000
    TEXT_INPUT_COST_PER_1M = 0.0
    AUDIO_INPUT_COST_PER_1M = 0.0
    IMAGE_INPUT_COST_PER_1M = 0.0
    TEXT_OUTPUT_COST_PER_1M = 0.0
    AUDIO_OUTPUT_COST_PER_1M = 0.0
    REFRESH_CLIENT_ON_RECONNECT = True

    def __init__(
        self,
        deps: ToolDependencies,
        gradio_mode: bool = False,
        instance_path: str | None = None,
        startup_voice: str | None = None,
    ) -> None:
        super().__init__(deps, gradio_mode=gradio_mode, instance_path=instance_path, startup_voice=startup_voice)
        self._api_key = (os.getenv("ALIYUN_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or "").strip()

    async def _prepare_startup_credentials(self) -> None:
        api_key = (os.getenv("ALIYUN_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("ALIYUN_API_KEY or DASHSCOPE_API_KEY environment variable is required")
        self._api_key = api_key

    def _persist_credentials_if_needed(self) -> None:
        return

    def _get_session_instructions(self) -> str:
        return (
            f"{get_session_instructions()}\n\n"
            "语言规则：默认用简体中文交流。除非用户明确要求其他语言，"
            "所有文本回复和语音回复都必须使用中文。"
        )

    def _get_session_voice(self, default: str | None = None) -> str:
        return get_session_voice(default=default or self.VOICE)

    def _get_active_tool_specs(self) -> list[dict[str, Any]]:
        return get_active_tool_specs(self.deps)

    def _build_session_tools(self, tool_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not _supports_function_calling(config.MODEL_NAME or self.MODEL_NAME):
            return []
        return [
            _aliyun_tool_schema(
                {
                    "type": "function",
                    "name": spec.get("name"),
                    "description": spec.get("description") or "",
                    "parameters": spec.get("parameters") or {"type": "object", "properties": {}},
                }
            )
            for spec in tool_specs
        ]

    def _get_session_config(self, tool_specs: list[dict[str, Any]]) -> dict[str, Any]:
        tools = self._build_session_tools(tool_specs)
        return {
            "modalities": ["text", "audio"],
            "instructions": self._get_session_instructions(),
            "voice": self.get_current_voice(),
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm24",
            "enable_input_audio_transcription": True,
            "input_audio_transcription_model": "qwen3-asr-flash-realtime",
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "silence_duration_ms": 500,
                "prefix_padding_ms": 300,
                "create_response": True,
                "interrupt_response": True,
            },
            **({"tools": tools, "tool_choice": "auto"} if tools else {}),
        }

    def _build_personality_update_session(self, instructions: str, voice: str) -> dict[str, Any]:
        return {
            "modalities": ["text", "audio"],
            "instructions": instructions,
            "voice": voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm24",
        }

    async def _build_realtime_client(self) -> _AliyunRealtimeClient:
        await self._prepare_startup_credentials()
        return _AliyunRealtimeClient(api_key=self._api_key, base_url=ALIYUN_REALTIME_BASE_URL)

    async def handle_text_turn(self, text: str) -> str:
        clean_text = text.strip()
        if not clean_text:
            return ""
        if not self.connection:
            raise RuntimeError("Aliyun realtime connection is not ready.")
        message = "阿里云 Omni 实时模式请直接对机器人麦克风说话；文字输入不会注入实时语音会话。"
        await self.output_queue.put(AdditionalOutputs({"role": "assistant", "content": message}))
        return message
