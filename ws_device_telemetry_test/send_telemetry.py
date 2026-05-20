from __future__ import annotations
import json
import time
import asyncio
import argparse
from typing import Any
from pathlib import Path

import websockets


DEFAULT_URL = "ws://192.168.2.236:8085/ws/device/telemetry"
DEFAULT_PAYLOAD = Path(__file__).parent / "payloads" / "reachy_mini_wireless.json"


def load_payload(path: Path) -> dict[str, Any]:
    """Load a JSON payload from disk."""
    return json.loads(path.read_text(encoding="utf-8"))


def update_runtime_fields(payload: dict[str, Any], sequence: int) -> dict[str, Any]:
    """Refresh fields that should change on every send."""
    updated = dict(payload)
    updated["timestamp"] = int(time.time() * 1000)
    metrics = dict(updated.get("metrics") or {})
    metrics["sequence"] = sequence
    metrics["message"] = "Reachy Mini telemetry heartbeat"
    updated["metrics"] = metrics
    return updated


async def send_messages(url: str, payload_path: Path, count: int, interval: float) -> None:
    """Connect to the telemetry websocket and send JSON text messages."""
    payload = load_payload(payload_path)
    async with websockets.connect(url) as websocket:
        print(f"connected: {url}")
        for sequence in range(1, count + 1):
            message = update_runtime_fields(payload, sequence)
            text = json.dumps(message, ensure_ascii=False)
            await websocket.send(text)
            print(f"sent #{sequence}: {text}")
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=5)
                print(f"received #{sequence}: {response}")
            except asyncio.TimeoutError:
                print(f"received #{sequence}: <timeout>")
            if sequence < count:
                await asyncio.sleep(interval)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Send Reachy Mini telemetry over WebSocket.")
    parser.add_argument("--url", default=DEFAULT_URL, help="WebSocket URL.")
    parser.add_argument("--payload", type=Path, default=DEFAULT_PAYLOAD, help="JSON payload file.")
    parser.add_argument("--count", type=int, default=1, help="Number of messages to send.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between messages.")
    return parser.parse_args()


def main() -> None:
    """Run the sender."""
    args = parse_args()
    asyncio.run(send_messages(args.url, args.payload, max(1, args.count), max(0.0, args.interval)))


if __name__ == "__main__":
    main()
