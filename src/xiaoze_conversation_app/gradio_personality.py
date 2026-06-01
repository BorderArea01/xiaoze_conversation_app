"""Simplified Gradio personality UI + ASR/LLM/TTS config panels."""

from __future__ import annotations
from typing import Any
from pathlib import Path

import gradio as gr

from xiaoze_conversation_app.config import (
    LOCKED_PROFILE,
    DEFAULT_PROFILES_DIRECTORY,
    config,
    get_default_voice_for_backend,
    get_available_voices_for_backend,
)


class PersonalityUI:
    """Simplified personality + config UI components."""

    def __init__(self) -> None:
        self.DEFAULT_OPTION = "(built-in default)"
        self._profiles_root = DEFAULT_PROFILES_DIRECTORY
        self._prompts_dir = Path(__file__).parent / "prompts"

        self.personalities_dropdown: gr.Dropdown
        self.apply_btn: gr.Button
        self.status_md: gr.Markdown
        self.preview_md: gr.Markdown
        self.voice_dropdown: gr.Dropdown

        # Config accordion components
        self.asr_url_tb: gr.Textbox
        self.asr_model_tb: gr.Textbox
        self.asr_lang_tb: gr.Textbox
        self.asr_status_md: gr.Markdown
        self.asr_save_btn: gr.Button

        self.llm_url_tb: gr.Textbox
        self.llm_model_tb: gr.Textbox
        self.llm_status_md: gr.Markdown
        self.llm_save_btn: gr.Button

        self.tts_url_tb: gr.Textbox
        self.tts_model_tb: gr.Textbox
        self.tts_voice_tb: gr.Textbox
        self.tts_status_md: gr.Markdown
        self.tts_save_btn: gr.Button

        self.api_key_tb: gr.Textbox
        self.key_status_md: gr.Markdown
        self.key_save_btn: gr.Button

    def _list_personalities(self) -> list[str]:
        names: list[str] = []
        try:
            if self._profiles_root.exists():
                for p in sorted(self._profiles_root.iterdir()):
                    if p.name == "user_personalities":
                        continue
                    if p.is_dir() and (p / "instructions.txt").exists():
                        names.append(p.name)
                user_dir = self._profiles_root / "user_personalities"
                if user_dir.exists():
                    for p in sorted(user_dir.iterdir()):
                        if p.is_dir() and (p / "instructions.txt").exists():
                            names.append(f"user_personalities/{p.name}")
        except Exception:
            pass
        return names

    def _read_instructions_for(self, name: str) -> str:
        try:
            if name == self.DEFAULT_OPTION:
                default_file = self._prompts_dir / "default_prompt.txt"
                if default_file.exists():
                    return default_file.read_text(encoding="utf-8").strip()
                return ""
            target = self._profiles_root / name / "instructions.txt"
            if target.exists():
                return target.read_text(encoding="utf-8").strip()
            return ""
        except Exception as e:
            return f"Could not load instructions: {e}"

    def create_components(self) -> None:
        """Instantiate simplified Gradio components."""
        is_locked = LOCKED_PROFILE is not None
        current_value = LOCKED_PROFILE if is_locked else (config.REACHY_MINI_CUSTOM_PROFILE or self.DEFAULT_OPTION)
        dropdown_label = "个性 (已锁定)" if is_locked else "切换个性"
        dropdown_choices = [LOCKED_PROFILE] if is_locked else [self.DEFAULT_OPTION, *(self._list_personalities())]

        self.personalities_dropdown = gr.Dropdown(
            label=dropdown_label,
            choices=dropdown_choices,
            value=current_value,
            interactive=not is_locked,
        )
        self.apply_btn = gr.Button("应用", interactive=not is_locked)
        self.status_md = gr.Markdown(visible=True)
        self.preview_md = gr.Markdown(value=self._read_instructions_for(current_value))
        self.voice_dropdown = gr.Dropdown(
            label="语音",
            choices=get_available_voices_for_backend(),
            value=get_default_voice_for_backend(),
            interactive=not is_locked,
        )

    def additional_inputs_ordered(self) -> list[Any]:
        return [
            self.personalities_dropdown,
            self.apply_btn,
            self.status_md,
            self.preview_md,
            self.voice_dropdown,
        ]

    def wire_events(self, handler: Any, blocks: gr.Blocks) -> None:
        async def _apply_personality(selected: str) -> tuple[str, str]:
            if LOCKED_PROFILE is not None and selected != LOCKED_PROFILE:
                return (
                    f"Profile is locked to '{LOCKED_PROFILE}'. Cannot change personality.",
                    self._read_instructions_for(LOCKED_PROFILE),
                )
            profile = None if selected == self.DEFAULT_OPTION else selected
            status = await handler.apply_personality(profile)
            preview = self._read_instructions_for(selected)
            return status, preview

        def _read_voice_for(name: str) -> str:
            default_voice = get_default_voice_for_backend()
            try:
                if name == self.DEFAULT_OPTION:
                    return default_voice
                vf = self._profiles_root / name / "voice.txt"
                if vf.exists():
                    v = vf.read_text(encoding="utf-8").strip()
                    return v or default_voice
            except Exception:
                pass
            return default_voice

        async def _fetch_voices(selected: str) -> dict[str, Any]:
            try:
                voices = await handler.get_available_voices()
                current = _read_voice_for(selected)
                if current not in voices:
                    current = get_default_voice_for_backend()
                return gr.update(choices=voices, value=current)
            except Exception:
                return gr.update(
                    choices=get_available_voices_for_backend(),
                    value=get_default_voice_for_backend(),
                )

        def _load_profile_preview(selected: str) -> str:
            return self._read_instructions_for(selected)

        with blocks:
            self.apply_btn.click(
                fn=_apply_personality,
                inputs=[self.personalities_dropdown],
                outputs=[self.status_md, self.preview_md],
            )
            self.personalities_dropdown.change(
                fn=_load_profile_preview,
                inputs=[self.personalities_dropdown],
                outputs=[self.preview_md],
            )
            blocks.load(
                fn=_fetch_voices,
                inputs=[self.personalities_dropdown],
                outputs=[self.voice_dropdown],
            )

    def create_config_accordions(self, stream_manager: gr.Blocks, instance_path: str | None = None) -> None:
        """Create ASR/LLM/TTS/API Key configuration accordions."""
        from xiaoze_conversation_app.config import refresh_runtime_config_from_env

        def _read_env_lines(env_path: Path) -> list[str]:
            try:
                return env_path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                return []

        def _persist_env_values(updates: dict[str, str]) -> None:
            normalized_updates = {name: (value or "").strip() for name, value in updates.items()}
            normalized_updates = {name: value for name, value in normalized_updates.items() if value}
            if not normalized_updates:
                return

            for env_name, value in normalized_updates.items():
                import os
                os.environ[env_name] = value
            refresh_runtime_config_from_env()

            if not instance_path:
                return

            inst = Path(instance_path)
            inst.mkdir(parents=True, exist_ok=True)
            env_path = inst / ".env"
            lines = _read_env_lines(env_path)
            for env_name, value in normalized_updates.items():
                replaced = False
                for index, line in enumerate(lines):
                    if line.strip().startswith(f"{env_name}="):
                        lines[index] = f"{env_name}={value}"
                        replaced = True
                        break
                if not replaced:
                    lines.append(f"{env_name}={value}")
            env_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

        current_api_key = (config.OPENAI_COMPATIBLE_API_KEY or config.OPENAI_API_KEY or "")

        with stream_manager:
            with gr.Accordion("API Key", open=False):
                self.api_key_tb = gr.Textbox(label="API Key", type="password", value=current_api_key)
                self.key_status_md = gr.Markdown()
                self.key_save_btn = gr.Button("保存 API Key")

            with gr.Accordion("ASR 配置 (语音识别)", open=False):
                self.asr_url_tb = gr.Textbox(label="ASR Base URL", value=config.ASR_BASE_URL or "")
                self.asr_model_tb = gr.Textbox(label="ASR Model", value=config.ASR_MODEL)
                self.asr_lang_tb = gr.Textbox(label="ASR Language (可选, 如 zh)", value=config.ASR_LANGUAGE or "")
                self.asr_status_md = gr.Markdown()
                self.asr_save_btn = gr.Button("保存 ASR 配置")

            with gr.Accordion("LLM 配置 (对话)", open=False):
                self.llm_url_tb = gr.Textbox(label="LLM Base URL", value=config.LLM_BASE_URL or "")
                self.llm_model_tb = gr.Textbox(label="LLM Model", value=config.LLM_MODEL)
                self.llm_status_md = gr.Markdown()
                self.llm_save_btn = gr.Button("保存 LLM 配置")

            with gr.Accordion("TTS 配置 (语音合成)", open=False):
                self.tts_url_tb = gr.Textbox(label="TTS Base URL", value=config.TTS_BASE_URL or "")
                self.tts_model_tb = gr.Textbox(label="TTS Model", value=config.TTS_MODEL)
                self.tts_voice_tb = gr.Textbox(label="TTS Voice", value=config.TTS_VOICE or "alloy")
                self.tts_status_md = gr.Markdown()
                self.tts_save_btn = gr.Button("保存 TTS 配置")

            def _save_asr(url: str, model: str, lang: str) -> str:
                _persist_env_values({
                    "ASR_BASE_URL": url,
                    "ASR_MODEL": model,
                    "ASR_LANGUAGE": lang,
                })
                return "ASR 配置已保存。语音连接建议重新进入后生效。"

            def _save_llm(url: str, model: str) -> str:
                _persist_env_values({
                    "LLM_BASE_URL": url,
                    "LLM_MODEL": model,
                })
                return "LLM 配置已保存。语音连接建议重新进入后生效。"

            def _save_tts(url: str, model: str, voice: str) -> str:
                _persist_env_values({
                    "TTS_BASE_URL": url,
                    "TTS_MODEL": model,
                    "TTS_VOICE": voice,
                })
                return "TTS 配置已保存。语音连接建议重新进入后生效。"

            def _save_key(key: str) -> str:
                key = (key or "").strip()
                if not key:
                    return "API Key 不能为空。"
                _persist_env_values({"OPENAI_COMPATIBLE_API_KEY": key})
                return "API Key 已保存。语音连接建议重新进入后生效。"

            self.asr_save_btn.click(
                fn=_save_asr,
                inputs=[self.asr_url_tb, self.asr_model_tb, self.asr_lang_tb],
                outputs=[self.asr_status_md],
            )
            self.llm_save_btn.click(
                fn=_save_llm,
                inputs=[self.llm_url_tb, self.llm_model_tb],
                outputs=[self.llm_status_md],
            )
            self.tts_save_btn.click(
                fn=_save_tts,
                inputs=[self.tts_url_tb, self.tts_model_tb, self.tts_voice_tb],
                outputs=[self.tts_status_md],
            )
            self.key_save_btn.click(
                fn=_save_key,
                inputs=[self.api_key_tb],
                outputs=[self.key_status_md],
            )
