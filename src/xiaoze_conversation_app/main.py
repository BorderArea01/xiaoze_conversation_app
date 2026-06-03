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
from fastapi import HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from reachy_mini import ReachyMini, ReachyMiniApp
from xiaoze_conversation_app.utils import (
    CameraVisionInitializationError,
    parse_args,
    setup_logger,
    initialize_camera_and_vision,
    log_connection_troubleshooting,
    ensure_localhost_bypasses_proxy,
)
from xiaoze_conversation_app.text_action_bridge import (
    parse_text_actions,
    execute_text_actions,
    is_action_only_query,
    format_action_results,
)


def update_chatbot(chatbot: List[Dict[str, Any]], response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Update the chatbot with AdditionalOutputs."""
    chatbot.append(response)
    return chatbot


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

    # Configure HuggingFace mirror for environments without HF_ENDPOINT set
    # (daemon subprocess doesn't source .bashrc)
    if not os.environ.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

    from xiaoze_conversation_app.moves import MovementManager
    from xiaoze_conversation_app.config import (
        HF_BACKEND,
        ALIYUN_BACKEND,
        GEMINI_BACKEND,
        OPENAI_BACKEND,
        PLATFORM_AGENT_BACKEND,
        COMPOSED_BACKEND,
        HF_LOCAL_CONNECTION_MODE,
        OPENAI_COMPATIBLE_BACKEND,
        OPENAI_COMPATIBLE_CHAT_BACKEND,
        config,
        is_gemini_model,
        get_backend_label,
        get_hf_connection_selection,
        refresh_runtime_config_from_env,
    )
    from xiaoze_conversation_app.startup_settings import (
        StartupSettings,
        load_startup_settings_into_runtime,
        read_startup_settings,
        write_startup_settings,
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

    from xiaoze_conversation_app.console import LocalStream
    from xiaoze_conversation_app.platform_client import (
        PlatformClient,
        PlatformService,
        PlatformClientConfig,
    )
    from xiaoze_conversation_app.tools.core_tools import ToolDependencies, ensure_tools_initialized
    from xiaoze_conversation_app.audio.head_wobbler import HeadWobbler

    if args.no_camera and args.head_tracker is not None:
        logger.warning("Head tracking disabled: --no-camera flag is set. Remove --no-camera to enable head tracking.")

    if robot is None:
        try:
            robot_kwargs = {}
            if args.robot_name is not None:
                robot_kwargs["robot_name"] = args.robot_name

            logger.info("Initializing ReachyMini (SDK will auto-detect appropriate backend)")
            robot = ReachyMini(**robot_kwargs, automatic_body_yaw=False)

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

    # Auto-enable Gradio in simulation mode
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

    movement_manager: MovementManager | None = None
    head_wobbler: HeadWobbler | None = None

    if not getattr(args, "no_move", False):
        movement_manager = MovementManager(
            current_robot=robot,
            camera_worker=camera_worker,
        )
        head_wobbler = HeadWobbler(set_speech_offsets=movement_manager.set_speech_offsets)
    else:
        logger.info("Movement manager disabled (--no-move)")

    platform_config = PlatformClientConfig.from_env()
    platform_service = PlatformService(
        PlatformClient(platform_config),
        robot_status_getter=robot.client.get_status,
        metrics_getter=lambda: {
            "appName": "xiaoze_conversation_app",
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
    ensure_tools_initialized()

    # Select handler
    if config.BACKEND_PROVIDER == COMPOSED_BACKEND:
        from xiaoze_conversation_app.composed_chat import ComposedChatHandler

        logger.info(
            "Using ASR+LLM+TTS composed handler (ASR=%s LLM=%s TTS=%s)",
            config.ASR_PROVIDER,
            config.LLM_PROVIDER,
            config.TTS_PROVIDER,
        )
        handler = ComposedChatHandler(
            deps,
            gradio_mode=args.gradio,
            instance_path=instance_path,
            startup_voice=startup_settings.voice,
        )
    elif config.BACKEND_PROVIDER == ALIYUN_BACKEND:
        from xiaoze_conversation_app.aliyun_realtime import AliyunRealtimeHandler

        logger.info("Using %s via Aliyun realtime model", get_backend_label(config.BACKEND_PROVIDER))
        handler = AliyunRealtimeHandler(
            deps,
            gradio_mode=args.gradio,
            instance_path=instance_path,
            startup_voice=startup_settings.voice,
        )
    elif is_gemini_model():
        from xiaoze_conversation_app.gemini_live import GeminiLiveHandler

        logger.info("Using %s via GeminiLiveHandler", get_backend_label(config.BACKEND_PROVIDER))
        handler = GeminiLiveHandler(
            deps,
            gradio_mode=args.gradio,
            instance_path=instance_path,
            startup_voice=startup_settings.voice,
        )
    elif config.BACKEND_PROVIDER == HF_BACKEND:
        from xiaoze_conversation_app.huggingface_realtime import HuggingFaceRealtimeHandler

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
        )
    elif config.BACKEND_PROVIDER == OPENAI_COMPATIBLE_BACKEND:
        from xiaoze_conversation_app.openai_compatible_realtime import OpenAICompatibleRealtimeHandler

        logger.info("Using %s via OpenAI-compatible realtime handler", get_backend_label(config.BACKEND_PROVIDER))
        handler = OpenAICompatibleRealtimeHandler(
            deps,
            gradio_mode=args.gradio,
            instance_path=instance_path,
            startup_voice=startup_settings.voice,
        )
    elif config.BACKEND_PROVIDER == OPENAI_COMPATIBLE_CHAT_BACKEND:
        from xiaoze_conversation_app.openai_compatible_chat import OpenAICompatibleChatHandler

        logger.info("Using %s via ASR + chat + TTS bridge", get_backend_label(config.BACKEND_PROVIDER))
        handler = OpenAICompatibleChatHandler(
            deps,
            gradio_mode=args.gradio,
            instance_path=instance_path,
            startup_voice=startup_settings.voice,
        )
    elif config.BACKEND_PROVIDER == PLATFORM_AGENT_BACKEND:
        from xiaoze_conversation_app.platform_agent import PlatformAgentRealtimeHandler

        logger.info(
            "Using %s via platform terminal websocket with Hugging Face realtime ASR/TTS",
            get_backend_label(config.BACKEND_PROVIDER),
        )
        handler = PlatformAgentRealtimeHandler(
            deps,
            gradio_mode=args.gradio,
            instance_path=instance_path,
            startup_voice=startup_settings.voice,
        )
    else:
        from xiaoze_conversation_app.openai_realtime import OpenaiRealtimeHandler

        logger.info(
            "Using %s via OpenAI realtime handler (OpenAI Realtime API)",
            get_backend_label(config.BACKEND_PROVIDER),
        )
        handler = OpenaiRealtimeHandler(
            deps,
            gradio_mode=args.gradio,
            instance_path=instance_path,
            startup_voice=startup_settings.voice,
        )

    stream_manager: gr.Blocks | LocalStream | None = None
    shutdown_event = threading.Event()

    def _handle_signal(signum, frame) -> None:
        """Signal handler for SIGINT/SIGTERM — triggers graceful shutdown."""
        logger.info("Received signal %s, initiating graceful shutdown", signum)
        shutdown_event.set()

    # SPA mode: React SPA replaces Gradio UI
    static_dir = Path(__file__).parent / "static"
    # Prefer SPA if index.html exists; --no-spa overrides to fall back to Gradio
    use_spa = (static_dir / "index.html").exists() and not getattr(args, "no_spa", False)
    if use_spa:
        args.gradio = False  # Disable Gradio code path in handler & metrics

    if use_spa:
        # --- SPA mode: FastAPI + React SPA ---
        logger.info("Starting in SPA mode (React frontend)")

        if not settings_app:
            app = FastAPI()
        else:
            app = settings_app

        from pydantic import BaseModel
        from xiaoze_conversation_app.platform_chat import mount_platform_chat_routes
        mount_platform_chat_routes(app)

        _spa_local_stream = LocalStream(
            handler,
            robot,
            settings_app=app,
            instance_path=instance_path,
        )

        class RobotChatPayload(BaseModel):
            query: str
            timeout_s: float = 30.0

        @app.get("/status")
        def _status() -> JSONResponse:
            from xiaoze_conversation_app.config import (
                get_available_voices_for_backend,
                get_default_voice_for_backend,
            )
            import os
            current_platform = PlatformClientConfig.from_env()
            aliyun_key = os.getenv("ALIYUN_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
            openai_key = os.getenv("OPENAI_API_KEY") or ""
            openai_compatible_key = os.getenv("OPENAI_COMPATIBLE_API_KEY") or ""
            component_providers = {
                (os.getenv("ASR_PROVIDER") or "openai").strip().lower(),
                (os.getenv("LLM_PROVIDER") or "openai").strip().lower(),
                (os.getenv("TTS_PROVIDER") or "openai").strip().lower(),
            }
            can_proceed = True
            if config.BACKEND_PROVIDER == ALIYUN_BACKEND:
                can_proceed = bool(aliyun_key.strip())
            else:
                if "aliyun" in component_providers and not aliyun_key.strip():
                    can_proceed = False
                if "openai_compatible" in component_providers and not openai_compatible_key.strip():
                    can_proceed = False
                if "openai" in component_providers and not openai_key.strip():
                    can_proceed = False
            current_voice = get_default_voice_for_backend()
            try:
                handler_voice = getattr(handler, "get_current_voice", None)
                if callable(handler_voice):
                    current_voice = str(handler_voice())
            except Exception:
                pass

            return JSONResponse({
                "active_backend": config.BACKEND_PROVIDER,
                "backend_provider": config.BACKEND_PROVIDER,
                "has_key": can_proceed,
                "has_aliyun_key": bool(aliyun_key.strip()),
                "has_openai_compatible_key": bool(openai_compatible_key.strip()),
                "can_proceed": can_proceed,
                "current_voice": current_voice,
                "available_voices": get_available_voices_for_backend(),
                "realtime": {
                    "provider": config.BACKEND_PROVIDER,
                    "model": config.MODEL_NAME,
                },
                "composed": {
                    "asr_provider": os.getenv("ASR_PROVIDER", "openai"),
                    "asr_base_url": os.getenv("ASR_BASE_URL", ""),
                    "asr_model": os.getenv("ASR_MODEL", "gpt-4o-transcribe"),
                    "asr_language": os.getenv("ASR_LANGUAGE", ""),
                    "llm_provider": os.getenv("LLM_PROVIDER", "openai"),
                    "llm_base_url": os.getenv("LLM_BASE_URL", ""),
                    "llm_model": os.getenv("LLM_MODEL", "gpt-4o"),
                    "tts_provider": os.getenv("TTS_PROVIDER", "openai"),
                    "tts_base_url": os.getenv("TTS_BASE_URL", ""),
                    "tts_model": os.getenv("TTS_MODEL", "tts-1"),
                    "tts_voice": config.TTS_VOICE or get_default_voice_for_backend(),
                },
                "platform": {
                    "device_id": current_platform.device_id,
                    "user_id": current_platform.user_id,
                    "session_id": current_platform.session_id,
                    "terminal_ws_url": current_platform.terminal_ws_url,
                },
                "robot_conversation": _spa_local_stream.get_monitor_state(),
            })

        @app.get("/ready")
        def _ready() -> JSONResponse:
            return JSONResponse({"ready": True})

        @app.get("/conversation/status")
        def _conversation_status() -> JSONResponse:
            return JSONResponse(_spa_local_stream.get_monitor_state())

        @app.get("/conversation/messages")
        def _conversation_messages() -> JSONResponse:
            state = _spa_local_stream.get_monitor_state()
            return JSONResponse({"messages": state["messages"]})

        @app.post("/conversation/text")
        def _conversation_text(payload: RobotChatPayload) -> JSONResponse:
            query = payload.query.strip()
            if not query:
                return JSONResponse({"ok": False, "error": "empty_query"}, status_code=400)
            try:
                reply = _spa_local_stream.submit_text_turn_threadsafe(query, timeout_s=max(1.0, payload.timeout_s))
                return JSONResponse({"ok": True, "query": query, "text": reply})
            except Exception as e:
                logger.exception("Typed robot chat failed: %s", e)
                error_text = f"对话失败：{type(e).__name__}: {e}"
                _spa_local_stream.record_monitor_message(
                    "assistant",
                    error_text,
                    {"status": "error"},
                )
                return JSONResponse(
                    {"ok": False, "error": error_text},
                    status_code=500,
                )

        # Mount WebRTC stream without Gradio UI
        stream = Stream(
            handler=handler,
            mode="send-receive",
            modality="audio",
        )
        stream.mount(app)

        # Register personality/voices routes BEFORE SPA fallback
        # (catch-all /{path} would shadow routes registered after it)
        try:
            from xiaoze_conversation_app.headless_personality_ui import mount_personality_routes

            def _persist_spa_personality(profile: str | None, voice_override: str | None = None) -> None:
                if instance_path is None:
                    return
                try:
                    from xiaoze_conversation_app.config import set_custom_profile

                    set_custom_profile(profile)
                except Exception:
                    pass
                write_startup_settings(instance_path, profile=profile, voice=voice_override)

            def _read_spa_personality() -> str | None:
                return read_startup_settings(instance_path).profile if instance_path is not None else None

            mount_personality_routes(
                app,
                handler,
                lambda: asyncio.get_event_loop() if asyncio.get_event_loop().is_running() else None,
                persist_personality=_persist_spa_personality,
                get_persisted_personality=_read_spa_personality,
            )
        except Exception as e:
            logger.warning("Failed to mount personality routes: %s", e)

        # Register backend_config, validate_api_key routes via minimal LocalStream
        try:
            _spa_local_stream._init_settings_ui_if_needed()
        except Exception as e:
            logger.warning("Failed to mount settings UI routes: %s", e)

        # Serve React build products
        if static_dir.exists():
            app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
            # Vite outputs assets under /assets/* in HTML but files are in static/assets/
            assets_dir = static_dir / "assets"
            if assets_dir.exists():
                app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

            @app.get("/")
            async def _index() -> FileResponse:
                return FileResponse(str(static_dir / "index.html"))

            @app.get("/{full_path:path}")
            async def _spa_fallback(full_path: str) -> FileResponse:
                if full_path.startswith(("api/", "webrtc/", "status", "platform_chat", "conversation", "backend_config", "validate_api_key", "personalities", "voices", "ready", "openai_api_key")):
                    raise HTTPException(status_code=404)
                if (static_dir / "index.html").exists():
                    return FileResponse(str(static_dir / "index.html"))
                raise HTTPException(status_code=404)

        stream_manager = app
    elif args.gradio:
        from xiaoze_conversation_app.gradio_personality import SimpleConfigUI
        from xiaoze_conversation_app.platform_chat import send_platform_chat, mount_platform_chat_routes

        ui = SimpleConfigUI()
        ui.render()

        stream = Stream(
            handler=handler,
            mode="send-receive",
            modality="audio",
            additional_inputs=ui.all_config_inputs(),
            additional_outputs=[ui.chatbot],
            additional_outputs_handler=update_chatbot,
            ui_args={"title": "小泽机器人对话", "css": ui.CARD_CSS},
        )
        stream_manager = stream.ui

        # Wire events within the Stream's Blocks context
        with stream_manager:
            ui.render_decorations()
            ui.wire_events(instance_path)

        # Text chat for platform (keeps local action bridge)
        async def typed_platform_chat_with_actions(query: str) -> str:
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

        # FastAPI app + routes
        if not settings_app:
            app = FastAPI()
        else:
            app = settings_app

        mount_platform_chat_routes(app)

        @app.get("/status")
        def _status() -> JSONResponse:
            current_platform = PlatformClientConfig.from_env()
            return JSONResponse({
                "active_backend": config.BACKEND_PROVIDER,
                "backend_provider": config.BACKEND_PROVIDER,
                "has_key": True,
                "can_proceed": True,
                "platform": {
                    "device_id": current_platform.device_id,
                    "user_id": current_platform.user_id,
                    "session_id": current_platform.session_id,
                    "terminal_ws_url": current_platform.terminal_ws_url,
                },
            })

        app = gr.mount_gradio_app(app, stream.ui, path="/")
    else:
        # In headless mode, wire settings_app + instance_path to console LocalStream
        stream_manager = LocalStream(
            handler,
            robot,
            settings_app=settings_app,
            instance_path=instance_path,
        )

    # Start async services
    if movement_manager:
        movement_manager.start()
    if head_wobbler:
        head_wobbler.start()
    platform_service.start()
    if camera_worker:
        camera_worker.start()

    def poll_stop_event() -> None:
        """Poll the stop event to allow graceful shutdown."""
        if app_stop_event is not None:
            app_stop_event.wait()

        logger.info("App stop event detected, shutting down...")
        shutdown_event.set()
        try:
            if use_spa:
                if _spa_local_stream is not None:
                    _spa_local_stream.close()
            else:
                stream_manager.close()
        except Exception as e:
            logger.error(f"Error while closing stream manager: {e}")

    if app_stop_event:
        threading.Thread(target=poll_stop_event, daemon=True).start()

    # Install signal handlers for graceful shutdown when daemon sends SIGINT
    if os.name == "posix":
        import signal as sig_module
        original_sigint_handler = sig_module.getsignal(sig_module.SIGINT)
        sig_module.signal(sig_module.SIGINT, _handle_signal)
        sig_module.signal(sig_module.SIGTERM, _handle_signal)

    try:
        if use_spa:
            spa_stream_thread = threading.Thread(
                target=_spa_local_stream.launch,
                name="xiaoze-spa-local-audio-stream",
                daemon=True,
            )
            spa_stream_thread.start()
            import uvicorn
            uvicorn_cfg = uvicorn.Config(app, host="0.0.0.0", port=7860, log_level="info")
            server = uvicorn.Server(uvicorn_cfg)
            # Run uvicorn in a thread so we can poll shutdown_event
            uvicorn_thread = threading.Thread(target=server.run, daemon=True)
            uvicorn_thread.start()
            # Block main thread on shutdown_event (set by signal handler or stop event)
            while not shutdown_event.is_set():
                shutdown_event.wait(timeout=1.0)
            logger.info("Shutdown event detected, stopping uvicorn server...")
            server.should_exit = True
            uvicorn_thread.join(timeout=10)
        elif args.gradio:
            stream_manager.launch(server_name="0.0.0.0", server_port=7860)
        else:
            stream_manager.launch()
    except KeyboardInterrupt:
        logger.info("Keyboard interruption in main thread... closing server.")
        shutdown_event.set()
    finally:
        if movement_manager:
            movement_manager.stop()
        if head_wobbler:
            head_wobbler.stop()
        platform_service.stop()
        if camera_worker:
            camera_worker.stop()

        try:
            robot.media.close()
        except Exception as e:
            logger.debug(f"Error closing media during shutdown: {e}")

        robot.client.disconnect()
        time.sleep(1)
        logger.info("Shutdown complete.")


class XiaozeConversationApp(ReachyMiniApp):  # type: ignore[misc]
    """Reachy Mini Apps entry point for the conversation app."""

    custom_app_url = "http://0.0.0.0:7860/"
    dont_start_webserver = True

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
    app = XiaozeConversationApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
