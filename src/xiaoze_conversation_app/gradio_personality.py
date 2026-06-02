"""Simple UI: Chatbot + ASR/LLM/TTS config panels."""

from __future__ import annotations
import os
from typing import Any
from pathlib import Path

import gradio as gr

from xiaoze_conversation_app.config import (
    config,
    get_default_voice_for_backend,
    get_available_voices_for_backend,
    refresh_runtime_config_from_env,
)


class SimpleConfigUI:
    """Minimal Gradio UI: voice/text chat + ASR/LLM/TTS config."""

    CARD_CSS = """
.gradio-container { max-width: 900px !important; margin: auto !important; }
.save-btn { border-radius: 8px; }
.section-header { font-size: 1.05em; font-weight: 600; margin: 4px 0; padding: 4px 0; color: #666; border-bottom: 1px solid #ddd; }
"""

    def __init__(self) -> None:
        # Chat components
        self.chatbot: gr.Chatbot | None = None
        self.text_input: gr.Textbox | None = None

        # ASR config
        self.asr_provider: gr.Dropdown | None = None
        self.asr_url_tb: gr.Textbox | None = None
        self.asr_model_tb: gr.Textbox | None = None
        self.asr_lang_tb: gr.Textbox | None = None

        # LLM config
        self.llm_provider: gr.Dropdown | None = None
        self.llm_url_tb: gr.Textbox | None = None
        self.llm_model_tb: gr.Textbox | None = None

        # TTS config
        self.tts_provider: gr.Dropdown | None = None
        self.tts_url_tb: gr.Textbox | None = None
        self.tts_model_tb: gr.Textbox | None = None
        self.tts_voice_tb: gr.Textbox | None = None

        # Shared
        self.api_key_tb: gr.Textbox | None = None

        # Headers (rendered as gr.HTML within additional_inputs)
        self.api_key_header: gr.HTML | None = None
        self.asr_header: gr.HTML | None = None
        self.llm_header: gr.HTML | None = None
        self.tts_header: gr.HTML | None = None

        # Actions (rendered in Blocks context)
        self.save_btn: gr.Button | None = None
        self.status_md: gr.Markdown | None = None

    def build_inputs(self) -> list:
        """Create all component instances including section headers.

        Components are registered when passed to Stream's additional_inputs.
        """
        # Title
        gr.HTML("<h2 style='text-align:center'>小泽智能对话</h2>")

        # Chat area
        self.chatbot = gr.Chatbot(
            label="对话记录",
            type="messages",
            resizable=True,
        )
        self.text_input = gr.Textbox(
            label="输入文字",
            placeholder="输入消息，回车发送...",
            lines=2,
        )

        # API Key section
        self.api_key_header = gr.HTML("<h3 class='section-header'>API Key</h3>")
        self.api_key_tb = gr.Textbox(
            label="API Key",
            type="password",
            value=(config.OPENAI_COMPATIBLE_API_KEY or config.OPENAI_API_KEY or ""),
        )

        # ASR section
        self.asr_header = gr.HTML("<h3 class='section-header'>ASR 语音识别</h3>")
        self.asr_provider = gr.Dropdown(
            label="Provider",
            choices=["openai", "openai_compatible"],
            value=config.ASR_PROVIDER or "openai",
        )
        self.asr_url_tb = gr.Textbox(label="Base URL", value=config.ASR_BASE_URL or "")
        self.asr_model_tb = gr.Textbox(label="Model", value=config.ASR_MODEL or "gpt-4o-transcribe")
        self.asr_lang_tb = gr.Textbox(label="Language (如 zh)", value=config.ASR_LANGUAGE or "")

        # LLM section
        self.llm_header = gr.HTML("<h3 class='section-header'>LLM 对话</h3>")
        self.llm_provider = gr.Dropdown(
            label="Provider",
            choices=["openai", "openai_compatible", "gemini"],
            value=config.LLM_PROVIDER or "openai",
        )
        self.llm_url_tb = gr.Textbox(label="Base URL", value=config.LLM_BASE_URL or "")
        self.llm_model_tb = gr.Textbox(label="Model", value=config.LLM_MODEL or "gpt-4o")

        # TTS section
        self.tts_header = gr.HTML("<h3 class='section-header'>TTS 语音合成</h3>")
        self.tts_provider = gr.Dropdown(
            label="Provider",
            choices=["openai", "openai_compatible"],
            value=config.TTS_PROVIDER or "openai",
        )
        self.tts_url_tb = gr.Textbox(label="Base URL", value=config.TTS_BASE_URL or "")
        self.tts_model_tb = gr.Textbox(label="Model", value=config.TTS_MODEL or "tts-1")
        self.tts_voice_tb = gr.Textbox(label="Voice", value=config.TTS_VOICE or get_default_voice_for_backend())
        return self.all_config_inputs()

    def render_decorations(self) -> None:
        """Add save button and status inside a Blocks context.

        Must be called inside `with stream_manager:` after build_inputs().
        """
        self.save_btn = gr.Button("保存配置", variant="primary", elem_classes="save-btn")
        self.status_md = gr.Markdown()

    def render(self) -> list:
        """Build inputs and return them for Stream additional_inputs."""
        return self.build_inputs()

    def create_components(self) -> list:
        """Legacy alias for render()."""
        return self.render()

    def _persist_env_values(self, updates: dict[str, str], instance_path: str | None) -> None:
        """Write config values to os.environ and .env file."""
        normalized = {k: (v or "").strip() for k, v in updates.items()}
        normalized = {k: v for k, v in normalized.items() if v}
        if not normalized:
            return

        for env_name, value in normalized.items():
            os.environ[env_name] = value
        refresh_runtime_config_from_env()

        if not instance_path:
            return

        inst = Path(instance_path)
        inst.mkdir(parents=True, exist_ok=True)
        env_path = inst / ".env"
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []

        for env_name, value in normalized.items():
            replaced = False
            for i, line in enumerate(lines):
                if line.strip().startswith(f"{env_name}="):
                    lines[i] = f"{env_name}={value}"
                    replaced = True
                    break
            if not replaced:
                lines.append(f"{env_name}={value}")

        env_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def wire_events(self, instance_path: str | None = None) -> None:
        """Wire the save button to persist config."""

        def _save_all(
            api_key: str,
            asr_prov: str,
            asr_url: str,
            asr_model: str,
            asr_lang: str,
            llm_prov: str,
            llm_url: str,
            llm_model: str,
            tts_prov: str,
            tts_url: str,
            tts_model: str,
            tts_voice: str,
        ) -> str:
            api_key = (api_key or "").strip()
            updates: dict[str, str] = {
                "ASR_PROVIDER": asr_prov,
                "ASR_BASE_URL": asr_url,
                "ASR_MODEL": asr_model,
                "ASR_LANGUAGE": asr_lang,
                "LLM_PROVIDER": llm_prov,
                "LLM_BASE_URL": llm_url,
                "LLM_MODEL": llm_model,
                "TTS_PROVIDER": tts_prov,
                "TTS_BASE_URL": tts_url,
                "TTS_MODEL": tts_model,
                "TTS_VOICE": tts_voice,
            }
            if api_key:
                updates["OPENAI_COMPATIBLE_API_KEY"] = api_key

            self._persist_env_values(updates, instance_path)
            return "配置已保存。重新进入语音连接后生效。"

        self.save_btn.click(
            fn=_save_all,
            inputs=[
                self.api_key_tb,
                self.asr_provider,
                self.asr_url_tb,
                self.asr_model_tb,
                self.asr_lang_tb,
                self.llm_provider,
                self.llm_url_tb,
                self.llm_model_tb,
                self.tts_provider,
                self.tts_url_tb,
                self.tts_model_tb,
                self.tts_voice_tb,
            ],
            outputs=[self.status_md],
        )

    def all_config_inputs(self) -> list:
        """Return all components for Stream additional_inputs (including headers)."""
        inputs = []
        # Title and chat
        if hasattr(self, 'chatbot') and self.chatbot is not None:
            inputs.append(self.chatbot)
        if hasattr(self, 'text_input') and self.text_input is not None:
            inputs.append(self.text_input)
        # API Key section
        if self.api_key_header is not None:
            inputs.append(self.api_key_header)
        if self.api_key_tb is not None:
            inputs.append(self.api_key_tb)
        # ASR section
        if self.asr_header is not None:
            inputs.append(self.asr_header)
        if self.asr_provider is not None:
            inputs.append(self.asr_provider)
        if self.asr_url_tb is not None:
            inputs.append(self.asr_url_tb)
        if self.asr_model_tb is not None:
            inputs.append(self.asr_model_tb)
        if self.asr_lang_tb is not None:
            inputs.append(self.asr_lang_tb)
        # LLM section
        if self.llm_header is not None:
            inputs.append(self.llm_header)
        if self.llm_provider is not None:
            inputs.append(self.llm_provider)
        if self.llm_url_tb is not None:
            inputs.append(self.llm_url_tb)
        if self.llm_model_tb is not None:
            inputs.append(self.llm_model_tb)
        # TTS section
        if self.tts_header is not None:
            inputs.append(self.tts_header)
        if self.tts_provider is not None:
            inputs.append(self.tts_provider)
        if self.tts_url_tb is not None:
            inputs.append(self.tts_url_tb)
        if self.tts_model_tb is not None:
            inputs.append(self.tts_model_tb)
        if self.tts_voice_tb is not None:
            inputs.append(self.tts_voice_tb)
        return inputs
