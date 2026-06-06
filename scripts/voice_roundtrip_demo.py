#!/usr/bin/env python3
"""One-shot voice roundtrip demo: mic -> local ASR -> local filter -> Bailian LLM -> local TTS -> speaker."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import queue
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd
import websockets


DEFAULT_HOST = "192.168.0.234"
BAILIAN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def print_safe(*values: object) -> None:
    text = " ".join(str(value) for value in values)
    print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"))


def list_audio_devices() -> None:
    print_safe("\n可用录音设备：")
    for idx, device in enumerate(sd.query_devices()):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        hostapi = sd.query_hostapis(device["hostapi"])["name"]
        marker = "*" if sd.default.device and idx == sd.default.device[0] else " "
        print_safe(f"{marker} {idx:>2}  {device['name']}  [{hostapi}]")


def record_fixed(duration_s: float, sample_rate: int = 16000, device: int | str | None = None) -> bytes:
    print_safe(f"\n录音 {duration_s:.1f}s：请现在开始说话...")
    audio = sd.rec(int(duration_s * sample_rate), samplerate=sample_rate, channels=1, dtype="int16", device=device)
    sd.wait()
    print_safe("录音结束。")
    return audio.reshape(-1).tobytes()


def record_manual(sample_rate: int = 16000, device: int | str | None = None) -> bytes:
    chunks: queue.Queue[np.ndarray] = queue.Queue()

    def callback(indata: np.ndarray, frames: int, time_info: object, status: object) -> None:
        if status:
            print_safe(f"录音状态：{status}")
        chunks.put(indata.copy())

    input("\n按 Enter 开始录音，然后对着麦克风说话...")
    print_safe("正在录音。说完后按 Enter 停止。")
    with sd.InputStream(samplerate=sample_rate, channels=1, dtype="int16", device=device, callback=callback):
        input()

    parts: list[np.ndarray] = []
    while not chunks.empty():
        parts.append(chunks.get())
    if not parts:
        return b""
    audio = np.concatenate(parts, axis=0).reshape(-1)
    duration_s = len(audio) / sample_rate
    print_safe(f"录音结束，时长 {duration_s:.1f}s。")
    return audio.tobytes()


def audio_peak(audio: bytes) -> int:
    if not audio:
        return 0
    samples = np.frombuffer(audio, dtype=np.int16)
    return int(np.max(np.abs(samples))) if samples.size else 0


async def transcribe(host: str, audio: bytes) -> dict:
    uri = f"ws://{host}:3100"
    chunk_size = 3200
    async with websockets.connect(uri, open_timeout=8, close_timeout=2) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=8)
        print(f"ASR ready: {first}")
        for i in range(0, len(audio), chunk_size):
            await ws.send(audio[i : i + chunk_size])
            await asyncio.sleep(0.03)
        await ws.send(json.dumps({"type": "process"}))
        raw = await asyncio.wait_for(ws.recv(), timeout=45)
        return json.loads(raw)


def extract_transcript(result: dict) -> str:
    candidates = [
        result.get("text"),
        result.get("transcript"),
        result.get("result"),
    ]
    data = result.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("text"), data.get("transcript"), data.get("result")])
    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def chat_completion(base_url: str, api_key: str, model: str, messages: list[dict], timeout_s: int = 60) -> str:
    response = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": 0.5, "max_tokens": 256},
        timeout=timeout_s,
    )
    if response.status_code != 200:
        raise RuntimeError(f"LLM HTTP {response.status_code}: {response.text[:1000]}")
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


def local_filter(host: str, transcript: str) -> tuple[bool, str]:
    normalized = transcript.strip()
    if not normalized:
        return False, "empty transcript"

    if len(normalized) <= 1:
        return False, "too short"
    if re.fullmatch(r"[\uac00-\ud7af\s]+", normalized):
        return False, "hangul-only ASR artifact"
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", normalized):
        return False, "no supported speech content"

    direct_cues = ("小泽", "机器人", "智能体", "你好", "请你", "帮我", "介绍一下", "回答一下")
    if any(cue in normalized for cue in direct_cues):
        return True, "direct conversational cue"

    prompt = (
        "你是机器人前置筛选器。判断用户这句话是否是在跟智能体/机器人说话，"
        "只输出 YES 或 NO。"
        "如果是闲聊、提问、命令、喊机器人，都算 true；明显背景噪声、旁人对话、空话算 false。"
    )
    try:
        response = requests.post(
            f"http://{host}:3000/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": transcript},
                ],
                "temperature": 0,
                "max_tokens": 80,
            },
            timeout=20,
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"].strip()
        verdict = re.sub(r"[^A-Za-z\u4e00-\u9fff]", "", text).upper()
        if verdict in {"YES", "TRUE", "是", "通过"}:
            return True, text
        if verdict in {"NO", "FALSE", "否", "不是", "不通过"}:
            return False, text
        return False, text
    except Exception as exc:
        return True, f"filter fallback pass: {exc}"


def bailian_reply(api_key: str, model: str, transcript: str) -> str:
    return chat_completion(
        BAILIAN_BASE_URL,
        api_key,
        model,
        [
            {
                "role": "system",
                "content": "你是一个在机器人身体里的中文语音助手。回复要自然、简短，适合直接被 TTS 朗读，最多两句话。",
            },
            {"role": "user", "content": transcript},
        ],
    )


async def synthesize(host: str, text: str, output_wav: Path) -> None:
    uri = f"ws://{host}:3200"
    pcm_chunks: list[bytes] = []
    async with websockets.connect(uri, open_timeout=8, close_timeout=2) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=8)
        print(f"TTS ready: {first}")
        await ws.send(json.dumps({"type": "synthesize", "text": text, "language": "zh"}, ensure_ascii=False))
        t0 = time.time()
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=120)
            data = json.loads(raw)
            msg_type = data.get("type")
            if msg_type == "audio":
                pcm_chunks.append(base64.b64decode(data.get("data", "")))
            elif msg_type == "done":
                break
            elif msg_type == "error":
                raise RuntimeError(data.get("message", "TTS error"))
        print(f"TTS 完成，用时 {time.time() - t0:.1f}s，收到 {sum(len(c) for c in pcm_chunks)} bytes。")

    samples = np.frombuffer(b"".join(pcm_chunks), dtype=np.float32)
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(np.clip(samples * 32767, -32768, 32767).astype(np.int16).tobytes())


def play_wav(path: Path) -> None:
    if os.name == "nt":
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME)
    else:
        print(f"音频已保存：{path}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--record-mode", choices=["manual", "fixed"], default="manual")
    parser.add_argument("--model", default="qwen-plus")
    parser.add_argument("--output", default="artifacts/voice_roundtrip_reply.wav")
    parser.add_argument("--input-device", help="sounddevice input device id or name")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--no-playback", action="store_true")
    parser.add_argument("--skip-filter", action="store_true")
    args = parser.parse_args()

    if args.list_devices:
        list_audio_devices()
        return

    api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("BAILIAN_API_KEY")
    if not api_key:
        raise SystemExit("请先设置 DASHSCOPE_API_KEY 或 BAILIAN_API_KEY。")

    input_device: int | str | None = None
    if args.input_device:
        input_device = int(args.input_device) if args.input_device.isdigit() else args.input_device

    print_safe("\n全链路：麦克风 -> 3100 ASR -> 3000 小模型过滤 -> 百炼 LLM -> 3200 TTS -> 扬声器")
    print_safe(f"目标主机：{args.host}")
    print_safe(f"百炼模型：{args.model}")
    if input_device is not None:
        print_safe(f"录音设备：{input_device}")

    if args.record_mode == "fixed":
        audio = record_fixed(args.duration, device=input_device)
    else:
        audio = record_manual(device=input_device)
    print_safe(f"录音峰值：{audio_peak(audio)}")

    t0 = time.time()
    asr_result = await transcribe(args.host, audio)
    transcript = extract_transcript(asr_result)
    print_safe(f"\nASR 识别 ({time.time() - t0:.1f}s)：{transcript or '<空>'}")
    if not transcript:
        print_safe("ASR 没识别到有效文本，本轮不回复。可以换 --input-device 或靠近麦克风再试。")
        return

    if not args.skip_filter:
        t0 = time.time()
        engaged, filter_raw = local_filter(args.host, transcript)
        print_safe(f"小模型过滤 ({time.time() - t0:.1f}s)：{engaged} / {filter_raw}")
        if not engaged:
            print_safe("过滤结果认为这不是对智能体说话，本轮不回复。")
            return

    print_safe("\n调用阿里云百炼生成回复...")
    t0 = time.time()
    reply = bailian_reply(api_key, args.model, transcript)
    print_safe(f"百炼回复 ({time.time() - t0:.1f}s)：{reply}")

    output_wav = Path(args.output).resolve()
    t0 = time.time()
    await synthesize(args.host, reply, output_wav)
    print_safe(f"TTS 总耗时：{time.time() - t0:.1f}s")
    print_safe(f"回复音频：{output_wav}")
    if not args.no_playback:
        print_safe("开始播放回复。")
        play_wav(output_wav)


if __name__ == "__main__":
    asyncio.run(main())
