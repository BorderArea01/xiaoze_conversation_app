from __future__ import annotations
import os
import json
import asyncio

import websockets


SERVER_ID = "2056652440241684481"
TOKEN = os.getenv("REACHY_PLATFORM_TOKEN", "")


def payload() -> dict:
    """Build an ASR_SIGNAL payload."""
    return {
        "type": "ASR_SIGNAL",
        "data": {
            "user_id": "1999",
            "session_id": "session-20260519-001",
            "query": "你好，我是 Reachy Mini，现在绑定好了吗？",
        },
    }


async def try_one(label: str, url: str) -> None:
    """Connect, send one message, and print a few responses."""
    try:
        async with websockets.connect(url, additional_headers={"authorization": f"Bearer {TOKEN}"}) as ws:
            print(f"SUCCESS {label}: connected {url}")
            text = json.dumps(payload(), ensure_ascii=False)
            await ws.send(text)
            print(f"SUCCESS {label}: sent {text}")
            for index in range(5):
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=8)
                    print(f"SUCCESS {label}: received #{index + 1}: {response}")
                except asyncio.TimeoutError:
                    print(f"SUCCESS {label}: receive timeout")
                    break
    except Exception as exc:
        print(f"FAILED {label}: {type(exc).__name__}: {exc}")


async def main() -> None:
    """Run terminal websocket tests."""
    if not TOKEN:
        raise RuntimeError("Set REACHY_PLATFORM_TOKEN before running this probe.")
    await try_one("8088-MAC", "ws://192.168.2.236:8088/websocket/terminal/MAC-001")
    await try_one("8088-server-id", f"ws://192.168.2.236:8088/websocket/terminal/{SERVER_ID}")


if __name__ == "__main__":
    asyncio.run(main())
