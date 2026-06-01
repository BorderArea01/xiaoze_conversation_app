"""Entrypoint for the Reachy Mini conversation app."""

import os
import sys
import time
import asyncio
import argparse
import threading
from typing import Any, Dict, List, Optional
from pathlib import Path

import gradio as gr
from fastapi import FastAPI, Request
from fastrtc import Stream
from gradio.utils import get_space
from fastapi.responses import JSONResponse

from reachy_mini import ReachyMini, ReachyMiniApp
from reachy_mini_conversation_app.utils import (
    CameraVisionInitializationError,
    parse_args,
    setup_logger,
    initialize_camera_and_vision,
    log_connection_troubleshooting,
    ensure_localhost_bypasses_proxy,
)
from reachy_mini_conversation_app.platform_chat import send_platform_chat, mount_platform_chat_routes
from reachy_mini_conversation_app.text_action_bridge import (
    parse_text_actions,
    execute_text_actions,
    is_action_only_query,
    format_action_results,
)


GRADIO_LOCALIZATION_JS = """
() => {
  const translations = new Map([
    ["Chatbot", "对话记录"],
    ["Stream", "语音连接"],
    ["Click to Access Microphone", "点击开启麦克风"],
    ["Toggle Sidebar", "展开/收起侧边栏"],
    ["grant webcam access", "授权麦克风访问"],
  ]);
  const translateNode = (root) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const value = node.nodeValue.trim();
      if (translations.has(value)) node.nodeValue = node.nodeValue.replace(value, translations.get(value));
    }
    for (const el of root.querySelectorAll?.("[aria-label], [title]") || []) {
      for (const attr of ["aria-label", "title"]) {
        const value = el.getAttribute(attr);
        if (translations.has(value)) el.setAttribute(attr, translations.get(value));
      }
    }
  };
  const run = () => {
    translateNode(document.body);
  };
  new MutationObserver(run).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", run);
  run();
}
"""

def update_chatbot(chatbot: List[Dict[str, Any]], response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Update the chatbot with AdditionalOutputs."""
    chatbot.append(response)
    return chatbot


async def typed_platform_chat(query: str) -> str:
    """Send typed text to the platform websocket for manual probing."""
    result = await send_platform_chat(query)
    if not result.get("ok"):
        raise gr.Error(str(result.get("error") or "platform_chat_failed"))
    return str(result.get("text") or "(no text returned)")


def main() -> None:
    """Entrypoint for the Reachy Mini conversation app."""
    ensure_localhost_bypasses_proxy()
    args, _ = parse_args()
    run(args)


def run(
    args: argparse.Namespace,
    robot: ReachyMini = None,
    app_stop_event: Optional[threading.Event] = None,
    settings_app: Optional[FastAPI] = None,
    instance_path: Optional[str] = None,
) -> None:
    """Run the Reachy Mini conversation app."""
    ensure_localhost_bypasses_proxy()
    # Putting these dependencies here makes the dashboard faster to load when the conversation app is installed
    from reachy_mini_conversation_app.moves import MovementManager
    from reachy_mini_conversation_app.config import (
        HF_BACKEND,
        GEMINI_BACKEND,
        OPENAI_BACKEND,
        PLATFORM_AGENT_BACKEND,
        HF_LOCAL_CONNECTION_MODE,
        OPENAI_COMPATIBLE_BACKEND,
        OPENAI_COMPATIBLE_CHAT_BACKEND,
        config,
        is_gemini_model,
        get_backend_label,
        get_hf_connection_selection,
        refresh_runtime_config_from_env,
    )
    from reachy_mini_conversation_app.startup_settings import (
        StartupSettings,
        load_startup_settings_into_runtime,
    )

    logger = setup_logger(args.debug)
    logger.info("Starting Reachy Mini Conversation App")
    startup_settings = StartupSettings()

    if instance_path is not None:
        try:
            from dotenv import load_dotenv

            env_path = Path(instance_path) / ".env"
            if env_path.exists():
                load_dotenv(dotenv_path=str(env_path), override=True)
                refresh_runtime_config_from_env()
                logger.info("Loaded instance configuration from %s", env_path)
        except Exception as e:
            logger.warning("Failed to load instance configuration: %s", e)

        try:
            startup_settings = load_startup_settings_into_runtime(instance_path)
        except Exception as e:
            logger.warning("Failed to load startup settings: %s", e)

    if config.BACKEND_PROVIDER == HF_BACKEND:
        logger.info(
            "Configured backend provider: %s (%s), connection mode: %s",
            config.BACKEND_PROVIDER,
            get_backend_label(config.BACKEND_PROVIDER),
            get_hf_connection_selection().mode,
        )
    else:
        logger.info(
            "Configured backend provider: %s (%s), model: %s",
            config.BACKEND_PROVIDER,
            get_backend_label(config.BACKEND_PROVIDER),
            config.MODEL_NAME,
        )

    from reachy_mini_conversation_app.console import LocalStream
    from reachy_mini_conversation_app.platform_client import (
        PlatformClient,
        PlatformService,
        PlatformClientConfig,
    )
    from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
    from reachy_mini_conversation_app.audio.head_wobbler import HeadWobbler

    if args.no_camera and args.head_tracker is not None:
        logger.warning("Head tracking disabled: --no-camera flag is set. Remove --no-camera to enable head tracking.")

    if robot is None:
        try:
            robot_kwargs = {}
            if args.robot_name is not None:
                robot_kwargs["robot_name"] = args.robot_name

            logger.info("Initializing ReachyMini (SDK will auto-detect appropriate backend)")
            robot = ReachyMini(**robot_kwargs)

        except TimeoutError as e:
            logger.error(f"Connection timeout: Failed to connect to Reachy Mini daemon. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except ConnectionError as e:
            logger.error(f"Connection failed: Unable to establish connection to Reachy Mini. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except Exception as e:
            logger.error(f"Unexpected error during robot initialization: {type(e).__name__}: {e}")
            logger.error("Please check your configuration and try again.")
            sys.exit(1)

    # Auto-enable Gradio in simulation mode (both MuJoCo for daemon and mockup-sim for desktop app)
    status = robot.client.get_status()
    if isinstance(status, dict):
        simulation_enabled = status.get("simulation_enabled", False)
        mockup_sim_enabled = status.get("mockup_sim_enabled", False)
    else:
        simulation_enabled = getattr(status, "simulation_enabled", False)
        mockup_sim_enabled = getattr(status, "mockup_sim_enabled", False)

    is_simulation = simulation_enabled or mockup_sim_enabled

    if is_simulation and not args.gradio:
        logger.info("Simulation mode detected. Automatically enabling gradio flag.")
        args.gradio = True

    try:
        camera_worker, vision_processor = initialize_camera_and_vision(args, robot)
    except CameraVisionInitializationError as e:
        logger.error("Failed to initialize camera/vision: %s", e)
        sys.exit(1)

    movement_manager = MovementManager(
        current_robot=robot,
        camera_worker=camera_worker,
    )

    head_wobbler = HeadWobbler(set_speech_offsets=movement_manager.set_speech_offsets)
    platform_config = PlatformClientConfig.from_env()
    platform_service = PlatformService(
        PlatformClient(platform_config),
        robot_status_getter=robot.client.get_status,
        metrics_getter=lambda: {
            "appName": "reachy_mini_conversation_app",
            "backendProvider": config.BACKEND_PROVIDER,
            "modelName": config.MODEL_NAME,
            "cameraEnabled": camera_worker is not None,
            "headTracker": args.head_tracker or "none",
            "gradioEnabled": bool(args.gradio),
            "simulationEnabled": bool(is_simulation),
        },
    )

    deps = ToolDependencies(
        reachy_mini=robot,
        movement_manager=movement_manager,
        camera_worker=camera_worker,
        vision_processor=vision_processor,
        head_wobbler=head_wobbler,
    )
    current_file_path = os.path.dirname(os.path.abspath(__file__))
    logger.debug(f"Current file absolute path: {current_file_path}")
    chatbot = gr.Chatbot(
        label="对话记录",
        type="messages",
        resizable=True,
        avatar_images=(
            os.path.join(current_file_path, "images", "user_avatar.png"),
            os.path.join(current_file_path, "images", "reachymini_avatar.png"),
        ),
    )
    logger.debug(f"Chatbot avatar images: {chatbot.avatar_images}")

    if is_gemini_model():
        from reachy_mini_conversation_app.gemini_live import GeminiLiveHandler

        logger.info(
            "Using %s via GeminiLiveHandler",
            get_backend_label(config.BACKEND_PROVIDER),
        )
        handler = GeminiLiveHandler(
            deps,
            gradio_mode=args.gradio,
            instance_path=instance_path,
            startup_voice=startup_settings.voice,
        )
    elif config.BACKEND_PROVIDER == HF_BACKEND:
        from reachy_mini_conversation_app.huggingface_realtime import HuggingFaceRealtimeHandler

        hf_connection_selection = get_hf_connection_selection()
        transport_label = (
            "Hugging Face direct websocket"
            if hf_connection_selection.mode == HF_LOCAL_CONNECTION_MODE and hf_connection_selection.has_target
            else "Hugging Face session proxy"
        )
        logger.info(
            "Using %s via Hugging Face realtime handler (%s)",
            get_backend_label(config.BACKEND_PROVIDER),
            transport_label,
        )
        handler = HuggingFaceRealtimeHandler(
            deps,
            gradio_mode=args.gradio,
            instance_path=instance_path,
            startup_voice=startup_settings.voice,
        )  # type: ignore[assignment]
    elif config.BACKEND_PROVIDER == OPENAI_COMPATIBLE_BACKEND:
        from reachy_mini_conversation_app.openai_compatible_realtime import OpenAICompatibleRealtimeHandler

        logger.info(
            "Using %s via OpenAI-compatible realtime handler",
            get_backend_label(config.BACKEND_PROVIDER),
        )
        handler = OpenAICompatibleRealtimeHandler(
            deps,
            gradio_mode=args.gradio,
            instance_path=instance_path,
            startup_voice=startup_settings.voice,
        )  # type: ignore[assignment]
    elif config.BACKEND_PROVIDER == OPENAI_COMPATIBLE_CHAT_BACKEND:
        from reachy_mini_conversation_app.openai_compatible_chat import OpenAICompatibleChatHandler

        logger.info(
            "Using %s via ASR + chat + TTS bridge",
            get_backend_label(config.BACKEND_PROVIDER),
        )
        handler = OpenAICompatibleChatHandler(
            deps,
            gradio_mode=args.gradio,
            instance_path=instance_path,
            startup_voice=startup_settings.voice,
        )  # type: ignore[assignment]
    elif config.BACKEND_PROVIDER == PLATFORM_AGENT_BACKEND:
        from reachy_mini_conversation_app.platform_agent import PlatformAgentRealtimeHandler

        logger.info(
            "Using %s via platform terminal websocket with Hugging Face realtime ASR/TTS",
            get_backend_label(config.BACKEND_PROVIDER),
        )
        handler = PlatformAgentRealtimeHandler(
            deps,
            gradio_mode=args.gradio,
            instance_path=instance_path,
            startup_voice=startup_settings.voice,
        )  # type: ignore[assignment]
    else:
        from reachy_mini_conversation_app.openai_realtime import OpenaiRealtimeHandler

        logger.info(
            "Using %s via OpenAI realtime handler (OpenAI Realtime API)",
            get_backend_label(config.BACKEND_PROVIDER),
        )
        handler = OpenaiRealtimeHandler(
            deps,
            gradio_mode=args.gradio,
            instance_path=instance_path,
            startup_voice=startup_settings.voice,
        )  # type: ignore[assignment]

    stream_manager: gr.Blocks | LocalStream | None = None

    if args.gradio:
        from reachy_mini_conversation_app.gradio_personality import PersonalityUI

        personality_ui = PersonalityUI()
        personality_ui.create_components()
        additional_inputs: list[Any] = [chatbot, *personality_ui.additional_inputs_ordered()]

        if config.BACKEND_PROVIDER in {OPENAI_BACKEND, GEMINI_BACKEND}:
            uses_gemini_backend = is_gemini_model()
            api_key_textbox = gr.Textbox(
                label="GEMINI_API_KEY" if uses_gemini_backend else "OPENAI API Key",
                type="password",
                value=(os.getenv("GEMINI_API_KEY") if uses_gemini_backend else os.getenv("OPENAI_API_KEY"))
                if not get_space()
                else "",
            )
            additional_inputs.insert(1, api_key_textbox)

        stream = Stream(
            handler=handler,
            mode="send-receive",
            modality="audio",
            additional_inputs=additional_inputs,
            additional_outputs=[chatbot],
            additional_outputs_handler=update_chatbot,
            ui_args={"title": "小泽机器人对话"},
        )
        stream_manager = stream.ui

        async def typed_platform_chat_with_actions(query: str) -> str:
            """Send typed text to the platform websocket and run local robot actions."""
            actions = parse_text_actions(query)
            action_results = await execute_text_actions(query, deps)
            action_text = format_action_results(action_results)
            if is_action_only_query(query, actions):
                return action_text or "没有识别到可执行的本地动作。"
            result = await send_platform_chat(query)
            if not result.get("ok"):
                raise gr.Error(str(result.get("error") or "platform_chat_failed"))
            parts = [action_text, str(result.get("text") or "(平台没有返回文本)")]
            return "\n\n".join(part for part in parts if part.strip())

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

        def save_platform_config_gradio(device_id: str, user_id: str, session_id: str, terminal_ws_url: str) -> str:
            """Persist the small set of platform values users edit from the Gradio app."""
            device_id = (device_id or "").strip()
            user_id = (user_id or "").strip()
            session_id = (session_id or "").strip()
            terminal_ws_url = (terminal_ws_url or "").strip()
            if not device_id or not user_id or not session_id or not terminal_ws_url:
                raise gr.Error("设备号、用户 ID、会话 ID 和平台 WS 都需要填写。")
            _persist_env_values(
                {
                    "BACKEND_PROVIDER": PLATFORM_AGENT_BACKEND,
                    "REACHY_PLATFORM_DEVICE_ID": device_id,
                    "REACHY_PLATFORM_USER_ID": user_id,
                    "REACHY_PLATFORM_SESSION_ID": session_id,
                    "REACHY_PLATFORM_TERMINAL_WS_URL": terminal_ws_url,
                }
            )
            return "已保存。文字对话会立即使用新配置；语音连接建议重新进入应用后再测试。"

        current_platform_config = PlatformClientConfig.from_env()
        with stream_manager:
            with gr.Accordion("设备配置", open=False):
                platform_device_id = gr.Textbox(label="设备号", value=current_platform_config.device_id)
                platform_user_id = gr.Textbox(label="用户 ID", value=current_platform_config.user_id)
                platform_session_id = gr.Textbox(label="会话 ID", value=current_platform_config.session_id)
                platform_terminal_ws = gr.Textbox(label="平台 WS", value=current_platform_config.terminal_ws_url)
                platform_save_status = gr.Markdown()
                platform_save = gr.Button("保存设备配置")
                platform_save.click(
                    save_platform_config_gradio,
                    inputs=[platform_device_id, platform_user_id, platform_session_id, platform_terminal_ws],
                    outputs=platform_save_status,
                    api_name="save_device_config",
                )
            with gr.Accordion("平台文字对话", open=False):
                typed_query = gr.Textbox(
                    label="输入文字",
                    lines=3,
                    placeholder="帮我查询今天的访客登记情况",
                )
                typed_answer = gr.Textbox(label="平台回复", lines=6)
                typed_send = gr.Button("发送")
                typed_send.click(
                    typed_platform_chat_with_actions,
                    inputs=typed_query,
                    outputs=typed_answer,
                    api_name="platform_chat",
                )
            stream_manager.load(fn=None, js=GRADIO_LOCALIZATION_JS)
        if not settings_app:
            app = FastAPI()
        else:
            app = settings_app

        mount_platform_chat_routes(app)

        def _gradio_status_payload() -> dict[str, Any]:
            current_platform = PlatformClientConfig.from_env()
            return {
                "active_backend": config.BACKEND_PROVIDER,
                "backend_provider": config.BACKEND_PROVIDER,
                "has_key": True,
                "has_platform_agent_key": True,
                "has_platform_asr_tts_key": bool(config.OPENAI_COMPATIBLE_API_KEY),
                "can_proceed": True,
                "can_proceed_with_platform_agent": True,
                "requires_restart": False,
                "platform": {
                    "token_configured": bool(current_platform.token),
                    "enabled": current_platform.enabled,
                    "activate_on_start": current_platform.activate_on_start,
                    "http_base_url": current_platform.http_base_url,
                    "telemetry_ws_url": current_platform.telemetry_ws_url,
                    "terminal_ws_url": current_platform.terminal_ws_url,
                    "device_id": current_platform.device_id,
                    "device_name": current_platform.device_name,
                    "device_type_name": current_platform.device_type_name,
                    "user_id": current_platform.user_id,
                    "session_id": current_platform.session_id,
                    "activation_code": current_platform.activation_code,
                },
            }

        @app.get("/status")
        def _status() -> JSONResponse:
            return JSONResponse(_gradio_status_payload())

        @app.post("/backend_config")
        async def _set_backend_config(request: Request) -> JSONResponse:
            payload = await request.json()
            backend = str(payload.get("backend") or PLATFORM_AGENT_BACKEND).strip().lower()
            if backend != PLATFORM_AGENT_BACKEND:
                return JSONResponse({"ok": False, "error": "unsupported_backend_in_gradio_mode"}, status_code=400)

            updates = {
                "BACKEND_PROVIDER": PLATFORM_AGENT_BACKEND,
                "MODEL_NAME": "",
                "REACHY_PLATFORM_TOKEN": payload.get("platform_token") or "",
                "REACHY_PLATFORM_DEVICE_ID": payload.get("platform_device_id") or "",
                "REACHY_PLATFORM_USER_ID": payload.get("platform_user_id") or "",
                "REACHY_PLATFORM_SESSION_ID": payload.get("platform_session_id") or "",
                "REACHY_PLATFORM_TERMINAL_WS_URL": payload.get("platform_terminal_ws_url") or "",
                "REACHY_PLATFORM_TELEMETRY_WS_URL": payload.get("platform_telemetry_ws_url") or "",
                "REACHY_PLATFORM_HTTP_BASE_URL": payload.get("platform_http_base_url") or "",
                "REACHY_PLATFORM_DEVICE_NAME": payload.get("platform_device_name") or "",
                "REACHY_PLATFORM_DEVICE_TYPE_NAME": payload.get("platform_device_type_name") or "",
                "REACHY_PLATFORM_ACTIVATION_CODE": payload.get("platform_activation_code") or "",
            }
            api_key = str(payload.get("api_key") or "").strip()
            if api_key:
                updates["OPENAI_COMPATIBLE_API_KEY"] = api_key
            if payload.get("platform_enabled") is not None:
                updates["REACHY_PLATFORM_ENABLED"] = "1" if payload.get("platform_enabled") else "0"
            if payload.get("platform_activate_on_start") is not None:
                updates["REACHY_PLATFORM_ACTIVATE_ON_START"] = "1" if payload.get("platform_activate_on_start") else "0"

            _persist_env_values(updates)
            return JSONResponse(
                {
                    "ok": True,
                    "message": "Device configuration saved.",
                    **_gradio_status_payload(),
                }
            )

        personality_ui.wire_events(handler, stream_manager)

        app = gr.mount_gradio_app(app, stream.ui, path="/")
    else:
        # In headless mode, wire settings_app + instance_path to console LocalStream
        stream_manager = LocalStream(
            handler,
            robot,
            settings_app=settings_app,
            instance_path=instance_path,
        )

    # Each async service gets its own thread/loop.
    movement_manager.start()
    head_wobbler.start()
    platform_service.start()
    if camera_worker:
        camera_worker.start()

    def poll_stop_event() -> None:
        """Poll the stop event to allow graceful shutdown."""
        if app_stop_event is not None:
            app_stop_event.wait()

        logger.info("App stop event detected, shutting down...")
        try:
            stream_manager.close()
        except Exception as e:
            logger.error(f"Error while closing stream manager: {e}")

    if app_stop_event:
        threading.Thread(target=poll_stop_event, daemon=True).start()

    try:
        stream_manager.launch()
    except KeyboardInterrupt:
        logger.info("Keyboard interruption in main thread... closing server.")
    finally:
        movement_manager.stop()
        head_wobbler.stop()
        platform_service.stop()
        if camera_worker:
            camera_worker.stop()

        # Ensure media is explicitly closed before disconnecting
        try:
            robot.media.close()
        except Exception as e:
            logger.debug(f"Error closing media during shutdown: {e}")

        # prevent connection to keep alive some threads
        robot.client.disconnect()
        time.sleep(1)
        logger.info("Shutdown complete.")


class ReachyMiniConversationApp(ReachyMiniApp):  # type: ignore[misc]
    """Reachy Mini Apps entry point for the conversation app."""

    custom_app_url = "http://0.0.0.0:7860/"
    dont_start_webserver = False

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        """Run the Reachy Mini conversation app."""
        ensure_localhost_bypasses_proxy()
        asyncio.set_event_loop(asyncio.new_event_loop())

        args, _ = parse_args()

        instance_path = self._get_instance_path().parent
        run(
            args,
            robot=reachy_mini,
            app_stop_event=stop_event,
            settings_app=self.settings_app,
            instance_path=instance_path,
        )


if __name__ == "__main__":
    app = ReachyMiniConversationApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
