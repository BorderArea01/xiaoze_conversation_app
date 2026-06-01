from __future__ import annotations
import os
import json
import time
import socket
import asyncio
import logging
import platform
import threading
from typing import Any, Callable
from dataclasses import dataclass

import httpx
import websockets


logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    """Parse a float environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid %s=%r; using %s", name, value, default)
        return default


def _now_ms() -> int:
    """Return current epoch milliseconds."""
    return int(time.time() * 1000)


def _local_ip() -> str:
    """Best-effort local IP address discovery without external network traffic."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except Exception:
        return "127.0.0.1"


@dataclass(frozen=True)
class PlatformClientConfig:
    """Configuration for the Reachy Mini platform integration."""

    enabled: bool = False
    http_base_url: str = "http://192.168.2.236:8088"
    telemetry_ws_url: str = "ws://192.168.2.236:8085/ws/device/telemetry"
    terminal_ws_url: str = "ws://192.168.2.236:8088/websocket/terminal/reachy-mini-001"
    token: str = ""
    activation_code: str = "zxomP9"
    activate_on_start: bool = False
    device_id: str = "reachy-mini-001"
    device_name: str = "Reachy Mini Wireless"
    device_type_name: str = "\u5c0f\u6cfd\u673a\u5668\u4eba"
    device_sn: str = "RMW-2026-001"
    enterprise_id: str = "1001"
    enterprise_name: str = "Reachy Mini Test Lab"
    tenant_id: str = "reachy-mini-demo"
    user_id: str = "1991"
    session_id: str = "session-20260519-001"
    telemetry_interval_s: float = 30.0

    @classmethod
    def from_env(cls) -> "PlatformClientConfig":
        """Build platform config from environment variables."""
        device_id = os.getenv("REACHY_PLATFORM_DEVICE_ID", cls.device_id)
        http_base_url = os.getenv("REACHY_PLATFORM_HTTP_BASE_URL", cls.http_base_url).rstrip("/")
        terminal_ws_url = os.getenv(
            "REACHY_PLATFORM_TERMINAL_WS_URL",
            f"ws://192.168.2.236:8088/websocket/terminal/{device_id}",
        )
        return cls(
            enabled=_env_flag("REACHY_PLATFORM_ENABLED", default=False),
            http_base_url=http_base_url,
            telemetry_ws_url=os.getenv("REACHY_PLATFORM_TELEMETRY_WS_URL", cls.telemetry_ws_url),
            terminal_ws_url=terminal_ws_url,
            token=os.getenv("REACHY_PLATFORM_TOKEN", ""),
            activation_code=os.getenv("REACHY_PLATFORM_ACTIVATION_CODE", cls.activation_code),
            activate_on_start=_env_flag("REACHY_PLATFORM_ACTIVATE_ON_START", default=False),
            device_id=device_id,
            device_name=os.getenv("REACHY_PLATFORM_DEVICE_NAME", cls.device_name),
            device_type_name=os.getenv("REACHY_PLATFORM_DEVICE_TYPE_NAME", cls.device_type_name),
            device_sn=os.getenv("REACHY_PLATFORM_DEVICE_SN", cls.device_sn),
            enterprise_id=os.getenv("REACHY_PLATFORM_ENTERPRISE_ID", cls.enterprise_id),
            enterprise_name=os.getenv("REACHY_PLATFORM_ENTERPRISE_NAME", cls.enterprise_name),
            tenant_id=os.getenv("REACHY_PLATFORM_TENANT_ID", cls.tenant_id),
            user_id=os.getenv("REACHY_PLATFORM_USER_ID", cls.user_id),
            session_id=os.getenv("REACHY_PLATFORM_SESSION_ID", cls.session_id),
            telemetry_interval_s=max(1.0, _env_float("REACHY_PLATFORM_TELEMETRY_INTERVAL_S", cls.telemetry_interval_s)),
        )


class PlatformClient:
    """Client for activation, telemetry, and terminal messages."""

    def __init__(self, config: PlatformClientConfig) -> None:
        """Initialize the platform client."""
        self.config = config

    @property
    def _auth_headers(self) -> dict[str, str]:
        if not self.config.token:
            return {}
        return {"authorization": f"Bearer {self.config.token}"}

    async def activate_device(self) -> dict[str, Any]:
        """Activate the device through the platform HTTP API."""
        body = {
            "activationCode": self.config.activation_code,
            "deviceName": self.config.device_name,
            "deviceTypeName": self.config.device_type_name,
            "deviceId": self.config.device_id,
        }
        url = f"{self.config.http_base_url}/system/serverConfig/deviceActivate"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, headers=self._auth_headers, json=body)
            response.raise_for_status()
            data = response.json()
            logger.info("Platform activation response: %s", data)
            return data if isinstance(data, dict) else {"data": data}

    def build_telemetry_payload(
        self,
        *,
        sequence: int,
        robot_status: Any | None = None,
        extra_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a telemetry payload shaped for the platform websocket."""
        metrics: dict[str, Any] = {
            "loadAverage": 0.0,
            "sequence": sequence,
            "message": "Reachy Mini telemetry heartbeat",
        }
        try:
            metrics["loadAverage"] = round(float(os.getloadavg()[0]), 2)
        except Exception:
            pass
        if isinstance(robot_status, dict):
            metrics["simulationEnabled"] = bool(robot_status.get("simulation_enabled", False))
            metrics["mockupSimEnabled"] = bool(robot_status.get("mockup_sim_enabled", False))
        elif robot_status is not None:
            metrics["simulationEnabled"] = bool(getattr(robot_status, "simulation_enabled", False))
            metrics["mockupSimEnabled"] = bool(getattr(robot_status, "mockup_sim_enabled", False))
        if extra_metrics:
            metrics.update(extra_metrics)

        return {
            "deviceId": self.config.device_id,
            "deviceName": self.config.device_name,
            "deviceSn": self.config.device_sn,
            "enterpriseId": self.config.enterprise_id,
            "enterpriseName": self.config.enterprise_name,
            "tenantId": self.config.tenant_id,
            "hostName": socket.gethostname(),
            "ipAddress": _local_ip(),
            "osName": f"{platform.system()} {platform.release()}",
            "architecture": platform.machine(),
            "timestamp": _now_ms(),
            "deviceInfo": {
                "cpuUsage": 0.0,
                "memoryTotalMb": 0,
                "memoryUsedMb": 0,
            },
            "metrics": metrics,
            "gpus": [],
        }

    async def send_telemetry(self, payload: dict[str, Any]) -> str | None:
        """Send one telemetry payload over websocket and return the first response."""
        text = json.dumps(payload, ensure_ascii=False)
        async with websockets.connect(self.config.telemetry_ws_url) as websocket:
            await websocket.send(text)
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=5)
            except asyncio.TimeoutError:
                return None
            return str(response)

    def build_asr_signal(self, query: str) -> dict[str, Any]:
        """Build an ASR_SIGNAL terminal message for the platform agent."""
        return {
            "type": "ASR_SIGNAL",
            "data": {
                "user_id": self.config.user_id,
                "session_id": self.config.session_id,
                "query": query,
            },
        }

    async def send_asr_signal(self, query: str, *, response_timeout_s: float = 6.0) -> list[str]:
        """Send one ASR_SIGNAL and collect responses until timeout."""
        responses: list[str] = []
        headers = self._auth_headers or None
        async with websockets.connect(self.config.terminal_ws_url, additional_headers=headers) as websocket:
            await websocket.send(json.dumps(self.build_asr_signal(query), ensure_ascii=False))
            while True:
                try:
                    response = str(await asyncio.wait_for(websocket.recv(), timeout=response_timeout_s))
                    responses.append(response)
                    if _is_terminal_response_end(response):
                        return responses
                except asyncio.TimeoutError:
                    return responses


def _is_terminal_response_end(response: str) -> bool:
    """Return whether a terminal websocket response marks the end of a streamed turn."""
    try:
        message = json.loads(response)
    except Exception:
        return False
    data = message.get("data") if isinstance(message, dict) else None
    if not isinstance(data, dict):
        return False
    return bool(data.get("is_end")) and data.get("type") == "2" and data.get("text") == "推理结束"


class PlatformService:
    """Background platform integration service for the app runtime."""

    def __init__(
        self,
        client: PlatformClient,
        *,
        robot_status_getter: Callable[[], Any | None] | None = None,
        metrics_getter: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        """Initialize background service callbacks."""
        self.client = client
        self.robot_status_getter = robot_status_getter
        self.metrics_getter = metrics_getter
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the service thread if platform integration is enabled."""
        if not self.client.config.enabled:
            logger.info("Platform integration disabled.")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_thread, name="platform-service", daemon=True)
        self._thread.start()
        logger.info("Platform integration service started.")

    def stop(self) -> None:
        """Stop the service thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run_thread(self) -> None:
        """Run the async service in its own event loop."""
        try:
            asyncio.run(self._run())
        except Exception as e:
            logger.warning("Platform service stopped after error: %s", e)

    async def _run(self) -> None:
        """Run activation and telemetry loops."""
        if self.client.config.activate_on_start:
            try:
                await self.client.activate_device()
            except Exception as e:
                logger.warning("Platform activation failed: %s", e)

        sequence = 0
        while not self._stop_event.is_set():
            sequence += 1
            try:
                robot_status = self.robot_status_getter() if self.robot_status_getter else None
                extra_metrics = self.metrics_getter() if self.metrics_getter else None
                payload = self.client.build_telemetry_payload(
                    sequence=sequence,
                    robot_status=robot_status,
                    extra_metrics=extra_metrics,
                )
                response = await self.client.send_telemetry(payload)
                logger.debug("Platform telemetry response: %s", response)
            except Exception as e:
                logger.warning("Platform telemetry send failed: %s", e)

            await self._sleep_until_next_tick()

    async def _sleep_until_next_tick(self) -> None:
        """Sleep in short chunks so stop() can interrupt promptly."""
        deadline = time.monotonic() + self.client.config.telemetry_interval_s
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(min(0.5, deadline - time.monotonic()))
