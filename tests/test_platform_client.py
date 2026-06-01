import json

from xiaoze_conversation_app.platform_agent import _extract_text
from xiaoze_conversation_app.platform_client import (
    PlatformClient,
    PlatformClientConfig,
    _is_terminal_response_end,
)


def test_platform_config_from_env_builds_terminal_url(monkeypatch) -> None:
    """Config should derive terminal URL from the configured device id."""
    monkeypatch.setenv("REACHY_PLATFORM_ENABLED", "1")
    monkeypatch.setenv("REACHY_PLATFORM_DEVICE_ID", "reachy-mini-test")
    monkeypatch.setenv("REACHY_PLATFORM_TOKEN", "token-123")
    monkeypatch.delenv("REACHY_PLATFORM_TERMINAL_WS_URL", raising=False)

    config = PlatformClientConfig.from_env()

    assert config.enabled is True
    assert config.device_id == "reachy-mini-test"
    assert config.token == "token-123"
    assert config.terminal_ws_url == "ws://192.168.2.236:8088/websocket/terminal/reachy-mini-test"


def test_platform_telemetry_payload_contains_reachy_fields() -> None:
    """Telemetry payload should use the platform-confirmed Reachy Mini identifiers."""
    client = PlatformClient(PlatformClientConfig())

    payload = client.build_telemetry_payload(
        sequence=7,
        robot_status={"simulation_enabled": True, "mockup_sim_enabled": False},
        extra_metrics={"backendProvider": "platform_agent"},
    )

    assert payload["deviceId"] == "reachy-mini-001"
    assert payload["deviceName"] == "Reachy Mini Wireless"
    assert payload["deviceInfo"]["cpuUsage"] == 0.0
    assert payload["metrics"]["sequence"] == 7
    assert payload["metrics"]["simulationEnabled"] is True
    assert payload["metrics"]["backendProvider"] == "platform_agent"
    json.dumps(payload, ensure_ascii=False)


def test_platform_asr_signal_uses_agent_schema() -> None:
    """ASR_SIGNAL should match the current platform agent schema."""
    client = PlatformClient(
        PlatformClientConfig(
            user_id="1991",
            session_id="session-20260519-001",
        )
    )

    message = client.build_asr_signal("\u4f60\u597d")

    assert message == {
        "type": "ASR_SIGNAL",
        "data": {
            "user_id": "1991",
            "session_id": "session-20260519-001",
            "query": "\u4f60\u597d",
        },
    }


def test_platform_stream_extracts_only_content_chunks() -> None:
    """Platform agent extraction should skip status chunks and keep content chunks."""
    status = json.dumps({"type": "TTS_MESSAGE", "data": {"type": "2", "text": "开始推理"}}, ensure_ascii=False)
    chunk = json.dumps({"type": "TTS_MESSAGE", "data": {"type": "0", "text": "你好"}}, ensure_ascii=False)

    assert _extract_text(status) == ""
    assert _extract_text(chunk) == "你好"


def test_terminal_response_end_detection() -> None:
    """Terminal websocket streaming should stop on the final reasoning status."""
    final = json.dumps(
        {"type": "TTS_MESSAGE", "data": {"type": "2", "text": "推理结束", "is_end": True}},
        ensure_ascii=False,
    )
    content = json.dumps(
        {"type": "TTS_MESSAGE", "data": {"type": "0", "text": "你好", "is_end": False}},
        ensure_ascii=False,
    )

    assert _is_terminal_response_end(final) is True
    assert _is_terminal_response_end(content) is False
