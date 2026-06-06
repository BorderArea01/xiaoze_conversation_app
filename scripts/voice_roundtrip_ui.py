#!/usr/bin/env python3
"""Local browser UI for mic -> ASR -> filter -> Bailian LLM -> TTS."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import queue
import sys
import time
import wave
import threading
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib import request as urlrequest
from urllib.parse import urlparse

import numpy as np
import sounddevice as sd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.voice_roundtrip_demo import bailian_reply, extract_transcript, local_filter, synthesize, transcribe


DEFAULT_HOST = "192.168.0.234"
DEFAULT_MODEL = "qwen-plus"
DEFAULT_MIN_PEAK = 600
DEFAULT_ALIYUN_TTS_MODEL = "qwen3-tts-flash"
DEFAULT_ALIYUN_TTS_VOICE = "Ethan"
ALIYUN_TTS_GENERATION_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"


class ServerRecorder:
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.chunks: queue.Queue[np.ndarray] = queue.Queue()
        self.stream: sd.InputStream | None = None

    def start(self) -> None:
        if self.stream is not None:
            raise RuntimeError("recording already started")
        while not self.chunks.empty():
            self.chunks.get()

        def callback(indata: np.ndarray, frames: int, time_info: object, status: object) -> None:
            self.chunks.put(indata.copy())

        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            callback=callback,
        )
        self.stream.start()

    def stop(self) -> bytes:
        if self.stream is None:
            raise RuntimeError("recording was not started")
        self.stream.stop()
        self.stream.close()
        self.stream = None

        parts: list[np.ndarray] = []
        while not self.chunks.empty():
            parts.append(self.chunks.get())
        if not parts:
            return b""
        return np.concatenate(parts, axis=0).reshape(-1).astype(np.int16).tobytes()


SERVER_RECORDER = ServerRecorder()


class ContinuousSession:
    def __init__(self) -> None:
        self.running = False
        self.events: list[dict] = []
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.config: dict = {}
        self.recent_replies: list[str] = []

    def start(self, config: dict) -> None:
        if self.running:
            raise RuntimeError("continuous session already running")
        self.config = config
        self.events = []
        self.recent_replies = []
        self.stop_event.clear()
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.running = False

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            events = self.events[since:]
            total = len(self.events)
        return {"running": self.running, "next": total, "events": events}

    def add_event(self, event: dict) -> None:
        event.setdefault("time", time.strftime("%H:%M:%S"))
        with self.lock:
            self.events.append(event)

    def _run(self) -> None:
        host = str(self.config.get("host") or DEFAULT_HOST)
        model = str(self.config.get("model") or DEFAULT_MODEL)
        tts_provider = str(self.config.get("tts_provider") or "local").strip().lower()
        aliyun_tts_model = str(self.config.get("aliyun_tts_model") or DEFAULT_ALIYUN_TTS_MODEL)
        aliyun_tts_voice = str(self.config.get("aliyun_tts_voice") or DEFAULT_ALIYUN_TTS_VOICE)
        api_key = str(self.config.get("api_key") or os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("BAILIAN_API_KEY") or "")
        chunk_seconds = float(self.config.get("chunk_seconds") or 4.0)
        min_peak = int(self.config.get("min_peak") or DEFAULT_MIN_PEAK)
        sample_rate = 16000
        self.add_event({"type": "status", "message": f"连续监听已启动，每 {chunk_seconds:.1f}s 检测一次，静音门限 peak>{min_peak}"})
        try:
            while not self.stop_event.is_set():
                self.add_event({"type": "cycle", "message": "新一轮检测"})
                self.add_event({"type": "stage", "stage": "record", "message": "正在录音"})
                audio = sd.rec(int(chunk_seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="int16")
                waited = 0.0
                while waited < chunk_seconds and not self.stop_event.is_set():
                    time.sleep(0.1)
                    waited += 0.1
                sd.wait()
                if self.stop_event.is_set():
                    break
                audio_bytes = audio.reshape(-1).tobytes()
                peak = int(np.max(np.abs(audio.reshape(-1)))) if audio.size else 0
                if peak < min_peak:
                    self.add_event({"type": "asr", "transcript": "", "elapsed": 0.0, "peak": peak})
                    self.add_event({"type": "filter", "engaged": False, "raw": f"below volume threshold peak={peak}", "elapsed": 0.0})
                    continue
                self.add_event({"type": "stage", "stage": "asr", "message": f"ASR 中，录音峰值 {peak}"})

                t0 = time.time()
                asr_result = asyncio.run(transcribe(host, audio_bytes))
                transcript = extract_transcript(asr_result)
                asr_s = time.time() - t0
                self.add_event({"type": "asr", "transcript": transcript, "elapsed": asr_s, "peak": peak})
                if not transcript:
                    self.add_event({"type": "filter", "engaged": False, "raw": "empty transcript", "elapsed": 0.0})
                    continue

                t0 = time.time()
                engaged, raw = local_filter(host, transcript)
                filter_s = time.time() - t0
                echo_score = self._self_echo_score(transcript)
                if echo_score >= 0.68:
                    engaged = False
                    raw = f"self-echo guard score={echo_score:.2f}"
                self.add_event({"type": "filter", "engaged": engaged, "raw": raw, "elapsed": filter_s})
                if not engaged:
                    continue

                if not api_key:
                    self.add_event({"type": "error", "message": "缺少百炼 Key"})
                    continue

                self.add_event({"type": "stage", "stage": "llm", "message": "Filter 通过，百炼生成中"})
                t0 = time.time()
                reply = bailian_reply(api_key, model, transcript)
                self.recent_replies.append(reply)
                self.recent_replies = self.recent_replies[-4:]
                llm_s = time.time() - t0
                self.add_event({"type": "llm", "reply": reply, "elapsed": llm_s})

                message = "本地 TTS 合成中，可能需要 20-40 秒"
                if tts_provider == "aliyun":
                    message = f"百炼 TTS 合成中：{aliyun_tts_model}"
                self.add_event({"type": "stage", "stage": "tts", "message": message})
                t0 = time.time()
                with NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    wav_path = Path(tmp.name)
                try:
                    if tts_provider == "aliyun":
                        if not api_key:
                            raise RuntimeError("缺少百炼 Key，无法使用 Aliyun TTS")
                        sample_rate, samples = dashscope_tts_request(api_key, aliyun_tts_model, aliyun_tts_voice, reply)
                        write_wav(wav_path, sample_rate, samples)
                    else:
                        asyncio.run(synthesize(host, reply, wav_path))
                    audio_duration = wav_duration(wav_path)
                    wav_base64 = base64.b64encode(wav_bytes_from_path(wav_path)).decode("ascii")
                finally:
                    try:
                        wav_path.unlink()
                    except OSError:
                        pass
                tts_s = time.time() - t0
                self.add_event({
                    "type": "tts",
                    "provider": tts_provider,
                    "audio_wav_base64": wav_base64,
                    "elapsed": tts_s,
                    "audio_duration": audio_duration,
                })
        except Exception as exc:
            self.add_event({"type": "error", "message": str(exc)})
        finally:
            self.running = False
            self.add_event({"type": "status", "message": "连续监听已停止"})

    def _self_echo_score(self, transcript: str) -> float:
        normalized = compact_text(transcript)
        if len(normalized) < 4:
            return 0.0
        best = 0.0
        for reply in self.recent_replies:
            ref = compact_text(reply)
            if not ref:
                continue
            ratio = SequenceMatcher(None, normalized, ref).ratio()
            containment = len(normalized) / len(ref) if normalized in ref else 0.0
            reverse_containment = len(ref) / len(normalized) if ref in normalized else 0.0
            best = max(best, ratio, containment, reverse_containment)
        return best


CONTINUOUS = ContinuousSession()


def compact_text(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Voice Roundtrip</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101215;
      --panel: #181b20;
      --panel-2: #20242b;
      --line: #323844;
      --text: #f2f4f7;
      --muted: #9aa4b2;
      --accent: #2f8cff;
      --ok: #41c782;
      --warn: #f3b34c;
      --bad: #f06464;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    }
    main {
      width: min(1120px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 24px 0 32px;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--line);
    }
    h1 {
      margin: 0;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 0;
    }
    .status {
      min-width: 150px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--muted);
      text-align: center;
      font-size: 13px;
    }
    .status.live { color: var(--ok); border-color: rgba(65,199,130,.45); }
    .status.busy { color: var(--warn); border-color: rgba(243,179,76,.45); }
    .status.err { color: var(--bad); border-color: rgba(240,100,100,.45); }
    .layout {
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr);
      gap: 18px;
      margin-top: 18px;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .controls {
      padding: 16px;
      display: grid;
      gap: 14px;
      align-content: start;
    }
    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }
    input {
      width: 100%;
      height: 38px;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #0d0f12;
      color: var(--text);
      padding: 0 10px;
      font: inherit;
      font-size: 13px;
      outline: none;
    }
    input:focus { border-color: var(--accent); }
    select {
      width: 100%;
      height: 38px;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #0d0f12;
      color: var(--text);
      padding: 0 10px;
      font: inherit;
      font-size: 13px;
      outline: none;
    }
    select:focus { border-color: var(--accent); }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    button {
      height: 42px;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--text);
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    button.primary { background: var(--accent); border-color: var(--accent); color: white; }
    button.danger { background: #3a2022; border-color: #6d3438; color: #ffb7b7; }
    button:disabled { opacity: .48; cursor: not-allowed; }
    .meter {
      height: 10px;
      overflow: hidden;
      border-radius: 999px;
      background: #0d0f12;
      border: 1px solid var(--line);
    }
    .meter > div {
      width: 0%;
      height: 100%;
      background: var(--ok);
      transition: width 80ms linear;
    }
    audio { width: 100%; height: 38px; }
    .results {
      min-height: 640px;
      display: grid;
      grid-template-rows: auto 1fr;
    }
    .timeline {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 1px;
      background: var(--line);
      border-bottom: 1px solid var(--line);
      border-radius: 8px 8px 0 0;
      overflow: hidden;
    }
    .step {
      min-height: 72px;
      background: var(--panel-2);
      padding: 12px;
      position: relative;
      padding-left: 34px;
    }
    .step::before {
      content: "";
      position: absolute;
      left: 12px;
      top: 15px;
      width: 11px;
      height: 11px;
      border-radius: 50%;
      background: #5b6471;
      box-shadow: 0 0 0 3px rgba(91,100,113,.15);
    }
    .step b { display: block; font-size: 13px; }
    .step span { display: block; margin-top: 6px; color: var(--muted); font-size: 12px; }
    .step.wait::before { background: #5b6471; box-shadow: 0 0 0 3px rgba(91,100,113,.15); }
    .step.busy::before { background: var(--warn); box-shadow: 0 0 0 3px rgba(243,179,76,.18), 0 0 16px rgba(243,179,76,.55); }
    .step.done::before { background: var(--ok); box-shadow: 0 0 0 3px rgba(65,199,130,.18), 0 0 16px rgba(65,199,130,.55); }
    .step.blocked::before { background: var(--bad); box-shadow: 0 0 0 3px rgba(240,100,100,.18), 0 0 16px rgba(240,100,100,.55); }
    .step.done span { color: var(--ok); }
    .step.busy span { color: var(--warn); }
    .step.blocked span { color: var(--bad); }
    .content {
      display: grid;
      gap: 14px;
      padding: 16px;
      align-content: start;
    }
    .block {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #12151a;
      padding: 14px;
    }
    .block h2 {
      margin: 0 0 10px;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .text {
      min-height: 52px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      line-height: 1.55;
      font-size: 15px;
    }
    .error {
      display: none;
      border-color: rgba(240,100,100,.55);
      color: #ffb7b7;
    }
    .error.show { display: block; }
    @media (max-width: 840px) {
      .layout { grid-template-columns: 1fr; }
      .timeline { grid-template-columns: 1fr 1fr; }
      main { width: min(100vw - 20px, 720px); padding-top: 14px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Voice Roundtrip</h1>
      <div id="status" class="status">Idle</div>
    </header>

    <div class="layout">
      <section class="controls">
        <label>百炼 Key
          <input id="apiKey" type="password" autocomplete="off" placeholder="可留空，默认使用服务端 Key" />
        </label>
        <div class="row">
          <label>服务主机
            <input id="host" value="192.168.0.234" />
          </label>
          <label>百炼模型
            <input id="model" value="qwen-plus" />
          </label>
        </div>
        <label>TTS Provider
          <select id="ttsProvider">
            <option value="local" selected>Local RK3588 TTS</option>
            <option value="aliyun">Aliyun qwen3-tts-flash</option>
          </select>
        </label>
        <div class="row">
          <label>Aliyun TTS Model
            <input id="aliyunTtsModel" value="qwen3-tts-flash" />
          </label>
          <label>Aliyun Voice
            <input id="aliyunTtsVoice" value="Ethan" />
          </label>
        </div>
        <div class="buttons">
          <button id="startBtn" class="primary">Start Listening</button>
          <button id="stopBtn" class="danger" disabled>Stop Listening</button>
        </div>
        <div class="meter"><div id="meterFill"></div></div>
        <div id="recordMode" class="status">Browser mic</div>
        <audio id="playback" controls></audio>
      </section>

      <section class="results">
        <div class="timeline">
          <div id="stepAsr" class="step wait"><b>ASR</b><span>-</span></div>
          <div id="stepFilter" class="step wait"><b>Filter</b><span>-</span></div>
          <div id="stepLlm" class="step wait"><b>LLM</b><span>-</span></div>
          <div id="stepTts" class="step wait"><b>TTS</b><span>-</span></div>
        </div>
        <div class="content">
          <div id="errorBox" class="block error"></div>
          <div class="block">
            <h2>Transcript</h2>
            <div id="transcript" class="text"></div>
          </div>
          <div class="block">
            <h2>Filter</h2>
            <div id="filter" class="text"></div>
          </div>
          <div class="block">
            <h2>Reply</h2>
            <div id="reply" class="text"></div>
          </div>
          <div class="block">
            <h2>Live Events</h2>
            <div id="events" class="text"></div>
          </div>
        </div>
      </section>
    </div>
  </main>

  <script>
    const els = {
      status: document.getElementById("status"),
      apiKey: document.getElementById("apiKey"),
      host: document.getElementById("host"),
      model: document.getElementById("model"),
      ttsProvider: document.getElementById("ttsProvider"),
      aliyunTtsModel: document.getElementById("aliyunTtsModel"),
      aliyunTtsVoice: document.getElementById("aliyunTtsVoice"),
      start: document.getElementById("startBtn"),
      stop: document.getElementById("stopBtn"),
      meter: document.getElementById("meterFill"),
      playback: document.getElementById("playback"),
      recordMode: document.getElementById("recordMode"),
      transcript: document.getElementById("transcript"),
      filter: document.getElementById("filter"),
      reply: document.getElementById("reply"),
      events: document.getElementById("events"),
      error: document.getElementById("errorBox"),
      steps: {
        asr: document.getElementById("stepAsr"),
        filter: document.getElementById("stepFilter"),
        llm: document.getElementById("stepLlm"),
        tts: document.getElementById("stepTts")
      }
    };

    let audioContext = null;
    let stream = null;
    let processor = null;
    let source = null;
    let chunks = [];
    let recordMode = "browser";
    let pollTimer = null;
    let ttsTimer = null;
    let ttsStartedAt = null;
    let eventCursor = 0;

    function setStatus(text, kind = "") {
      els.status.textContent = text;
      els.status.className = `status ${kind}`;
    }

    function setStep(name, text, kind = "") {
      const el = els.steps[name];
      el.className = `step ${kind || "wait"}`;
      el.querySelector("span").textContent = text;
    }

    function startTtsTimer() {
      stopTtsTimer();
      ttsStartedAt = Date.now();
      ttsTimer = setInterval(() => {
        const elapsed = (Date.now() - ttsStartedAt) / 1000;
        setStep("tts", `synthesizing ${elapsed.toFixed(1)}s`, "busy");
      }, 250);
    }

    function stopTtsTimer() {
      if (ttsTimer) clearInterval(ttsTimer);
      ttsTimer = null;
      ttsStartedAt = null;
    }

    function resetResults() {
      els.error.classList.remove("show");
      els.error.textContent = "";
      els.transcript.textContent = "";
      els.filter.textContent = "";
      els.reply.textContent = "";
      els.events.textContent = "";
      for (const name of Object.keys(els.steps)) setStep(name, "-", "");
      els.playback.removeAttribute("src");
      els.meter.style.width = "0%";
      els.recordMode.textContent = "Browser mic";
      stopTtsTimer();
    }

    function downsampleTo16k(float32, sourceRate) {
      if (sourceRate === 16000) return float32;
      const ratio = sourceRate / 16000;
      const out = new Float32Array(Math.floor(float32.length / ratio));
      for (let i = 0; i < out.length; i++) {
        const start = Math.floor(i * ratio);
        const end = Math.min(Math.floor((i + 1) * ratio), float32.length);
        let sum = 0;
        for (let j = start; j < end; j++) sum += float32[j];
        out[i] = sum / Math.max(1, end - start);
      }
      return out;
    }

    function floatsToPcm16Base64(parts, sourceRate) {
      const total = parts.reduce((sum, part) => sum + part.length, 0);
      const merged = new Float32Array(total);
      let offset = 0;
      for (const part of parts) {
        merged.set(part, offset);
        offset += part.length;
      }
      const samples = downsampleTo16k(merged, sourceRate);
      const bytes = new Uint8Array(samples.length * 2);
      const view = new DataView(bytes.buffer);
      for (let i = 0; i < samples.length; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      }
      let binary = "";
      const step = 0x8000;
      for (let i = 0; i < bytes.length; i += step) {
        binary += String.fromCharCode(...bytes.subarray(i, i + step));
      }
      return btoa(binary);
    }

    function base64ToBlobUrl(base64, type) {
      const binary = atob(base64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return URL.createObjectURL(new Blob([bytes], { type }));
    }

    async function startRecording() {
      resetResults();
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
        audioContext = new AudioContext();
        source = audioContext.createMediaStreamSource(stream);
        processor = audioContext.createScriptProcessor(4096, 1, 1);
        chunks = [];
        processor.onaudioprocess = (event) => {
          const input = event.inputBuffer.getChannelData(0);
          chunks.push(new Float32Array(input));
          let peak = 0;
          for (let i = 0; i < input.length; i++) peak = Math.max(peak, Math.abs(input[i]));
          els.meter.style.width = `${Math.min(100, Math.round(peak * 140))}%`;
        };
        source.connect(processor);
        processor.connect(audioContext.destination);
        recordMode = "browser";
        els.recordMode.textContent = "Browser mic";
      } catch (err) {
        const res = await fetch("/api/record/start", { method: "POST" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || err.message || String(err));
        recordMode = "server";
        els.recordMode.textContent = "Backend mic";
      }
      els.start.disabled = true;
      els.stop.disabled = false;
      setStatus("Recording", "live");
    }

    async function stopRecording() {
      els.stop.disabled = true;
      setStatus("Processing", "busy");

      setStep("asr", "running", "busy");
      setStep("filter", "queued", "");
      setStep("llm", "queued", "");
      setStep("tts", "queued", "");

      const payload = {
        api_key: els.apiKey.value.trim(),
        host: els.host.value.trim(),
        model: els.model.value.trim()
      };

      try {
        let res;
        if (recordMode === "server") {
          res = await fetch("/api/record/stop-and-roundtrip", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
          });
        } else {
          const sampleRate = audioContext.sampleRate;
          processor.disconnect();
          source.disconnect();
          stream.getTracks().forEach(track => track.stop());
          await audioContext.close();
          payload.audio_base64 = floatsToPcm16Base64(chunks, sampleRate);
          res = await fetch("/api/roundtrip", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
          });
        }
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);

        els.transcript.textContent = data.transcript || "";
        els.filter.textContent = `${data.engaged}\n${data.filter_raw || ""}`;
        els.reply.textContent = data.reply || "";
        setStep("asr", `${data.timings.asr.toFixed(1)}s`, "done");
        setStep("filter", `${data.timings.filter.toFixed(1)}s`, "done");
        setStep("llm", `${data.timings.llm.toFixed(1)}s`, "done");
        setStep("tts", `${data.timings.tts.toFixed(1)}s`, "done");
        if (data.audio_wav_base64) {
          els.playback.src = base64ToBlobUrl(data.audio_wav_base64, "audio/wav");
          await els.playback.play();
        }
        setStatus("Done", "live");
      } catch (err) {
        showError(err.message || String(err));
        setStatus("Error", "err");
      } finally {
        els.start.disabled = false;
      }
    }

    function showError(message) {
      els.error.textContent = message;
      els.error.classList.add("show");
    }

    async function startContinuous() {
      resetResults();
      const payload = {
        api_key: els.apiKey.value.trim(),
        host: els.host.value.trim(),
        model: els.model.value.trim(),
        tts_provider: els.ttsProvider.value,
        aliyun_tts_model: els.aliyunTtsModel.value.trim(),
        aliyun_tts_voice: els.aliyunTtsVoice.value.trim(),
        chunk_seconds: 4
      };
      const res = await fetch("/api/continuous/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      eventCursor = 0;
      els.start.disabled = true;
      els.stop.disabled = false;
      els.recordMode.textContent = "Backend continuous mic";
      setStatus("Listening", "live");
      pollTimer = setInterval(pollEvents, 1000);
      await pollEvents();
    }

    async function stopContinuous() {
      els.stop.disabled = true;
      await fetch("/api/continuous/stop", { method: "POST" });
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null;
      await pollEvents();
      els.start.disabled = false;
      setStatus("Stopped", "");
    }

    async function pollEvents() {
      const res = await fetch(`/api/continuous/events?since=${eventCursor}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      eventCursor = data.next;
      for (const event of data.events) renderEvent(event);
      if (!data.running && pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
        els.start.disabled = false;
        els.stop.disabled = true;
      }
    }

    function appendEvent(line) {
      els.events.textContent = `${line}\n${els.events.textContent}`.slice(0, 5000);
    }

    function renderEvent(event) {
      if (event.type === "status") {
        appendEvent(`[${event.time}] ${event.message}`);
      } else if (event.type === "cycle") {
        stopTtsTimer();
        setStatus("Listening", "live");
        setStep("asr", "waiting", "wait");
        setStep("filter", "waiting", "wait");
        setStep("llm", "waiting", "wait");
        setStep("tts", "waiting", "wait");
        appendEvent(`[${event.time}] ${event.message}`);
      } else if (event.type === "stage") {
        appendEvent(`[${event.time}] ${event.message}`);
        if (event.stage === "asr") setStep("asr", "running", "busy");
        if (event.stage === "llm") setStep("llm", "running", "busy");
        if (event.stage === "tts") {
          setStep("tts", "synthesizing 0.0s", "busy");
          startTtsTimer();
        }
      } else if (event.type === "asr") {
        els.transcript.textContent = event.transcript || "<空>";
        setStep("asr", `${event.elapsed.toFixed(1)}s`, "done");
        appendEvent(`[${event.time}] ASR: ${event.transcript || "<空>"} peak=${event.peak}`);
      } else if (event.type === "filter") {
        els.filter.textContent = `${event.engaged}\n${event.raw || ""}`;
        setStep("filter", `${event.elapsed.toFixed(1)}s ${event.engaged ? "pass" : "block"}`, event.engaged ? "done" : "blocked");
        if (!event.engaged) {
          setStep("llm", "blocked", "blocked");
          setStep("tts", "blocked", "blocked");
        }
        appendEvent(`[${event.time}] Filter: ${event.engaged ? "PASS" : "BLOCK"} ${event.raw || ""}`);
      } else if (event.type === "llm") {
        els.reply.textContent = event.reply || "";
        setStep("llm", `${event.elapsed.toFixed(1)}s`, "done");
        appendEvent(`[${event.time}] LLM: ${event.reply || ""}`);
      } else if (event.type === "tts") {
        stopTtsTimer();
        setStep("tts", `${event.elapsed.toFixed(1)}s`, "done");
        appendEvent(`[${event.time}] TTS ${event.provider || "local"} done, synth ${event.elapsed.toFixed(1)}s, audio ${Number(event.audio_duration || 0).toFixed(1)}s`);
        if (event.audio_wav_base64) {
          els.playback.src = base64ToBlobUrl(event.audio_wav_base64, "audio/wav");
          els.playback.play().catch(() => {});
        }
      } else if (event.type === "cooldown") {
        setStatus("Playback guard", "busy");
        appendEvent(`[${event.time}] ${event.message}`);
      } else if (event.type === "error") {
        showError(event.message);
        appendEvent(`[${event.time}] ERROR: ${event.message}`);
        setStatus("Error", "err");
      }
    }

    els.start.onclick = () => startContinuous().catch(err => showError(err.message || String(err)));
    els.stop.onclick = () => stopContinuous().catch(err => showError(err.message || String(err)));
  </script>
</body>
</html>
"""


def wav_bytes_from_path(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read()


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav_file:
        frames = wav_file.getnframes()
        rate = wav_file.getframerate()
        return frames / rate if rate else 0.0


def write_wav(path: Path, sample_rate: int, samples: np.ndarray) -> None:
    audio = np.asarray(samples, dtype=np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio.tobytes())


def dashscope_tts_request(api_key: str, model: str, voice: str, text: str) -> tuple[int, np.ndarray]:
    payload = {
        "model": model,
        "input": {
            "text": text,
            "voice": voice,
        },
    }
    request_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(
        ALIYUN_TTS_GENERATION_URL,
        data=request_data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=30) as response:
        response_body = response.read().decode("utf-8", errors="replace")
    result = json.loads(response_body)
    audio = result.get("output", {}).get("audio") if isinstance(result, dict) else None
    audio_url = audio.get("url") if isinstance(audio, dict) else None
    if not audio_url:
        message = result.get("message") if isinstance(result, dict) else response_body
        raise RuntimeError(f"Aliyun TTS did not return an audio URL: {message}")

    with urlrequest.urlopen(audio_url, timeout=30) as audio_response:
        audio_data = audio_response.read()
    with wave.open(BytesIO(audio_data), "rb") as wav_file:
        channels = wav_file.getnchannels()
        width = wav_file.getsampwidth()
        rate = wav_file.getframerate()
        raw = wav_file.readframes(wav_file.getnframes())
    if width != 2:
        raise RuntimeError(f"Aliyun TTS returned unsupported sample width={width}")
    samples = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels)[:, 0]
    return rate, samples


class VoiceRoundtripHandler(BaseHTTPRequestHandler):
    server_version = "VoiceRoundtripUI/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/continuous/events":
            query = urlparse(self.path).query
            since = 0
            for part in query.split("&"):
                if part.startswith("since="):
                    try:
                        since = int(part.split("=", 1)[1])
                    except ValueError:
                        since = 0
            self.send_json(200, CONTINUOUS.snapshot(since))
            return
        if path not in ("/", "/index.html"):
            self.send_error(404)
            return
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/continuous/start":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                CONTINUOUS.start(payload)
                self.send_json(200, {"ok": True})
            except Exception as exc:
                self.send_json(500, {"error": str(exc)})
            return

        if path == "/api/continuous/stop":
            CONTINUOUS.stop()
            self.send_json(200, {"ok": True})
            return

        if path == "/api/record/start":
            try:
                SERVER_RECORDER.start()
                self.send_json(200, {"ok": True})
            except Exception as exc:
                self.send_json(500, {"error": str(exc)})
            return

        if path not in ("/api/roundtrip", "/api/record/stop-and-roundtrip"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if path == "/api/record/stop-and-roundtrip":
                audio = SERVER_RECORDER.stop()
                payload["audio_base64"] = base64.b64encode(audio).decode("ascii")
            result = asyncio.run(self.handle_roundtrip(payload))
            self.send_json(200, result)
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    async def handle_roundtrip(self, payload: dict) -> dict:
        host = str(payload.get("host") or DEFAULT_HOST)
        model = str(payload.get("model") or DEFAULT_MODEL)
        api_key = str(payload.get("api_key") or os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("BAILIAN_API_KEY") or "")
        audio_base64 = str(payload.get("audio_base64") or "")
        if not api_key:
            raise RuntimeError("missing api_key")
        if not audio_base64:
            raise RuntimeError("missing audio")

        timings: dict[str, float] = {}
        audio = base64.b64decode(audio_base64)

        t0 = time.time()
        asr_result = await transcribe(host, audio)
        timings["asr"] = time.time() - t0
        transcript = extract_transcript(asr_result)
        if not transcript:
            return {
                "transcript": "",
                "engaged": False,
                "filter_raw": "empty transcript",
                "reply": "",
                "audio_wav_base64": "",
                "timings": {**timings, "filter": 0.0, "llm": 0.0, "tts": 0.0},
            }

        t0 = time.time()
        engaged, filter_raw = local_filter(host, transcript)
        timings["filter"] = time.time() - t0
        if not engaged:
            return {
                "transcript": transcript,
                "engaged": False,
                "filter_raw": filter_raw,
                "reply": "",
                "audio_wav_base64": "",
                "timings": {**timings, "llm": 0.0, "tts": 0.0},
            }

        t0 = time.time()
        reply = bailian_reply(api_key, model, transcript)
        timings["llm"] = time.time() - t0

        t0 = time.time()
        with NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            await synthesize(host, reply, wav_path)
            wav_base64 = base64.b64encode(wav_bytes_from_path(wav_path)).decode("ascii")
        finally:
            try:
                wav_path.unlink()
            except OSError:
                pass
        timings["tts"] = time.time() - t0

        return {
            "transcript": transcript,
            "engaged": engaged,
            "filter_raw": filter_raw,
            "reply": reply,
            "audio_wav_base64": wav_base64,
            "timings": timings,
        }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), VoiceRoundtripHandler)
    print(f"Voice Roundtrip UI: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
