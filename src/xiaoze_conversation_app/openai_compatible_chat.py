from __future__ import annotations
import io
import json
import wave
import asyncio
import logging
from typing import Any

import numpy as np
from openai import AsyncOpenAI
from fastrtc import AdditionalOutputs, audio_to_int16
from numpy.typing import NDArray
from scipy.signal import resample

from xiaoze_conversation_app.config import (
    OPENAI_COMPATIBLE_CHAT_BACKEND,
    config,
    set_custom_profile,
    get_default_voice_for_backend,
    get_available_voices_for_backend,
)
from xiaoze_conversation_app.prompts import get_session_voice, get_session_instructions
from xiaoze_conversation_app.tools.core_tools import (
    ToolDependencies,
    dispatch_tool_call,
    get_active_tool_specs,
)
from xiaoze_conversation_app.conversation_handler import ConversationHandler


logger = logging.getLogger(__name__)

AudioFrame = tuple[int, NDArray[np.int16]]


def _client(api_key: str, base_url: str | None) -> AsyncOpenAI:
    kwargs: dict[str, str] = {"api_key": api_key or "DUMMY"}
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)


def _tool_specs_for_chat(tool_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec.get("description", ""),
                "parameters": spec.get("parameters", {}),
            },
        }
        for spec in tool_specs
    ]


def _wav_bytes(sample_rate: int, audio: NDArray[np.int16]) -> bytes:
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio.astype(np.int16).tobytes())
        return buffer.getvalue()


def _decode_tts_audio(data: bytes, response_format: str, sample_rate: int) -> tuple[int, NDArray[np.int16]]:
    normalized_format = response_format.strip().lower()
    if normalized_format == "wav":
        with wave.open(io.BytesIO(data), "rb") as wav_file:
            channels = wav_file.getnchannels()
            width = wav_file.getsampwidth()
            rate = wav_file.getframerate()
            raw = wav_file.readframes(wav_file.getnframes())
        if width != 2:
            raise ValueError(f"Only 16-bit wav TTS output is supported, got sample width={width}")
        audio = np.frombuffer(raw, dtype=np.int16)
        if channels > 1:
            audio = audio.reshape(-1, channels)[:, 0]
        return rate, audio

    if normalized_format != "pcm":
        raise ValueError("OPENAI_COMPATIBLE_TTS_RESPONSE_FORMAT must be 'pcm' or 'wav'")
    return sample_rate, np.frombuffer(data, dtype=np.int16)


class OpenAICompatibleChatHandler(ConversationHandler):
    """ASR + chat-completions + TTS bridge for OpenAI-compatible providers."""

    BACKEND_PROVIDER = OPENAI_COMPATIBLE_CHAT_BACKEND
    INPUT_SAMPLE_RATE = 16000

    def __init__(
        self,
        deps: ToolDependencies,
        gradio_mode: bool = False,
        instance_path: str | None = None,
        startup_voice: str | None = None,
    ) -> None:
        """Initialize clients, audio buffers, and turn-processing state."""
        super().__init__(
            expected_layout="mono",
            output_sample_rate=config.OPENAI_COMPATIBLE_TTS_SAMPLE_RATE,
            input_sample_rate=self.INPUT_SAMPLE_RATE,
        )
        self.deps = deps
        self.gradio_mode = gradio_mode
        self.instance_path = instance_path
        self._voice_override = startup_voice
        self.output_queue: asyncio.Queue[AudioFrame | AdditionalOutputs] = asyncio.Queue()
        self._utterance_queue: asyncio.Queue[NDArray[np.int16]] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._closed = False
        self._speech_frames: list[NDArray[np.int16]] = []
        self._speech_started = False
        self._speech_seconds = 0.0
        self._silence_seconds = 0.0
        self._messages: list[dict[str, Any]] = []
        api_key = (config.OPENAI_COMPATIBLE_API_KEY or "").strip() or "DUMMY"
        chat_base_url = (config.OPENAI_COMPATIBLE_CHAT_BASE_URL or config.OPENAI_COMPATIBLE_BASE_URL or "").strip()
        asr_base_url = (config.OPENAI_COMPATIBLE_ASR_BASE_URL or chat_base_url or "").strip()
        tts_base_url = (config.OPENAI_COMPATIBLE_TTS_BASE_URL or chat_base_url or "").strip()
        self.chat_client = _client(api_key, chat_base_url or None)
        self.asr_client = _client(api_key, asr_base_url or None)
        self.tts_client = _client(api_key, tts_base_url or None)

    def copy(self) -> "OpenAICompatibleChatHandler":
        """Create a copy for fastrtc stream sessions."""
        return type(self)(self.deps, self.gradio_mode, self.instance_path, self._voice_override)

    def _instructions(self) -> str:
        return get_session_instructions()

    def get_current_voice(self) -> str:
        """Return the active TTS voice."""
        default_voice = get_default_voice_for_backend(self.BACKEND_PROVIDER)
        return self._voice_override or get_session_voice(default_voice) or default_voice

    async def get_available_voices(self) -> list[str]:
        """Return configured voice candidates for this backend."""
        return get_available_voices_for_backend(self.BACKEND_PROVIDER)

    async def change_voice(self, voice: str) -> str:
        """Change the TTS voice for future turns."""
        self._voice_override = voice.strip() or get_default_voice_for_backend(self.BACKEND_PROVIDER)
        return f"Voice changed to {self.get_current_voice()}."

    async def apply_personality(self, profile: str | None) -> str:
        """Apply a profile for subsequent chat turns."""
        set_custom_profile(profile)
        self._messages = []
        return "Applied personality. It will be used on the next turn."

    async def start_up(self) -> None:
        """Start the background ASR/chat/TTS turn worker."""
        self._closed = False
        self._worker_task = asyncio.create_task(self._turn_worker(), name="openai-compatible-chat-turn-worker")

    async def shutdown(self) -> None:
        """Stop the background turn worker."""
        self._closed = True
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def receive(self, frame: AudioFrame) -> None:
        """Collect microphone frames and enqueue complete utterances."""
        if self._closed:
            return
        input_sample_rate, audio_frame = frame
        if audio_frame.ndim == 2:
            if audio_frame.shape[1] > audio_frame.shape[0]:
                audio_frame = audio_frame.T
            if audio_frame.shape[1] > 1:
                audio_frame = audio_frame[:, 0]
        if input_sample_rate != self.INPUT_SAMPLE_RATE:
            audio_frame = resample(audio_frame, int(len(audio_frame) * self.INPUT_SAMPLE_RATE / input_sample_rate))
        audio = audio_to_int16(audio_frame).reshape(-1)
        if audio.size == 0:
            return

        rms = float(np.sqrt(np.mean((audio.astype(np.float32) / 32768.0) ** 2)))
        frame_seconds = audio.size / self.INPUT_SAMPLE_RATE
        is_speech = rms >= config.OPENAI_COMPATIBLE_VAD_THRESHOLD

        if is_speech:
            if not self._speech_started:
                if hasattr(self, "_clear_queue") and callable(self._clear_queue):
                    self._clear_queue()
                if self.deps.head_wobbler is not None:
                    self.deps.head_wobbler.reset()
                self.deps.movement_manager.set_listening(True)
            self._speech_started = True
            self._silence_seconds = 0.0
            self._speech_seconds += frame_seconds
            self._speech_frames.append(audio)
            return

        if not self._speech_started:
            return

        self._speech_frames.append(audio)
        self._silence_seconds += frame_seconds
        silence_limit = max(0.2, config.OPENAI_COMPATIBLE_TURN_SILENCE_MS / 1000.0)
        if self._silence_seconds < silence_limit:
            return

        self.deps.movement_manager.set_listening(False)
        if self._speech_seconds >= 0.25:
            await self._utterance_queue.put(np.concatenate(self._speech_frames))
        self._speech_frames = []
        self._speech_started = False
        self._speech_seconds = 0.0
        self._silence_seconds = 0.0

    async def emit(self) -> AudioFrame | AdditionalOutputs | None:
        """Emit transcript messages and synthesized speech audio."""
        return await self.output_queue.get()

    async def _turn_worker(self) -> None:
        while not self._closed:
            utterance = await self._utterance_queue.get()
            try:
                await self._handle_utterance(utterance)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("OpenAI-compatible chat turn failed: %s", e)
                await self.output_queue.put(
                    AdditionalOutputs({"role": "assistant", "content": f"Turn failed: {type(e).__name__}: {e}"})
                )

    async def _handle_utterance(self, audio: NDArray[np.int16]) -> None:
        transcript = await self._transcribe(audio)
        if not transcript:
            return
        await self.output_queue.put(AdditionalOutputs({"role": "user", "content": transcript}))
        reply = await self._chat(transcript)
        if not reply:
            return
        await self.output_queue.put(AdditionalOutputs({"role": "assistant", "content": reply}))
        sample_rate, tts_audio = await self._speak(reply)
        await self.output_queue.put((sample_rate, tts_audio))

    async def _transcribe(self, audio: NDArray[np.int16]) -> str:
        kwargs: dict[str, Any] = {
            "file": ("speech.wav", _wav_bytes(self.INPUT_SAMPLE_RATE, audio), "audio/wav"),
            "model": config.OPENAI_COMPATIBLE_TRANSCRIPTION_MODEL,
        }
        language = (config.OPENAI_COMPATIBLE_ASR_LANGUAGE or "").strip()
        if language:
            kwargs["language"] = language
        result = await self.asr_client.audio.transcriptions.create(**kwargs)
        if isinstance(result, str):
            return result.strip()
        return str(getattr(result, "text", "") or "").strip()

    async def _chat(self, transcript: str) -> str:
        if not self._messages:
            self._messages.append({"role": "system", "content": self._instructions()})
        self._messages.append({"role": "user", "content": transcript})
        tools = _tool_specs_for_chat(get_active_tool_specs(self.deps))

        for _ in range(4):
            completion = await self.chat_client.chat.completions.create(
                model=config.MODEL_NAME,
                messages=self._messages,
                tools=tools,
                tool_choice="auto",
            )
            message = completion.choices[0].message
            assistant_message = message.model_dump(exclude_none=True)
            self._messages.append(assistant_message)
            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls:
                return str(getattr(message, "content", "") or "").strip()

            for tool_call in tool_calls:
                function = getattr(tool_call, "function", None)
                tool_name = getattr(function, "name", "")
                args_json = getattr(function, "arguments", "{}")
                result = await dispatch_tool_call(tool_name, args_json, self.deps)
                tool_result = dict(result)
                if tool_name == "camera" and "b64_im" in tool_result:
                    tool_result.pop("b64_im", None)
                    tool_result["image_attached"] = True
                await self.output_queue.put(
                    AdditionalOutputs(
                        {
                            "role": "assistant",
                            "content": f"Used tool {tool_name} with args {args_json}.",
                            "metadata": {"title": f"Used tool {tool_name}", "status": "done"},
                        }
                    )
                )
                self._messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": getattr(tool_call, "id", ""),
                        "content": json.dumps(tool_result),
                    }
                )
        return ""

    async def _speak(self, text: str) -> tuple[int, NDArray[np.int16]]:
        response_format = config.OPENAI_COMPATIBLE_TTS_RESPONSE_FORMAT
        response = await self.tts_client.audio.speech.create(
            model=config.OPENAI_COMPATIBLE_TTS_MODEL,
            voice=self.get_current_voice(),
            input=text,
            response_format=response_format,
        )
        data = await response.aread() if hasattr(response, "aread") else response.read()
        return _decode_tts_audio(data, response_format, config.OPENAI_COMPATIBLE_TTS_SAMPLE_RATE)
