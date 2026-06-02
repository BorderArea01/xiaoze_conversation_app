from __future__ import annotations
import os
import sys
import json
import asyncio

import websockets

from xiaoze_conversation_app.platform_client import _is_terminal_response_end


TERMINAL_ID = "reachy-mini-001"
USER_ID = "1991"
SESSION_ID = "session-20260519-001"
URL = f"ws://192.168.2.236:8088/websocket/terminal/{TERMINAL_ID}"
TOKEN = os.getenv("REACHY_PLATFORM_TOKEN", "")


QUESTIONS = [
    "你好，我是 Reachy Mini，现在能听到我说话吗？",
    "请介绍一下你自己。",
    "今天下午我有哪些会议？",
    "帮我做一个点头的动作。",
]


def safe_print(text: str) -> None:
    """Print text even on terminals with a narrow default encoding."""
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
        sys.stdout.flush()


def build_message(query: str, index: int) -> str:
    """Build an ASR_SIGNAL websocket text message."""
    _ = index
    payload = {
        "type": "ASR_SIGNAL",
        "data": {
            "user_id": USER_ID,
            "session_id": SESSION_ID,
            "query": query,
        },
    }
    return json.dumps(payload, ensure_ascii=False)


async def drain_responses(ws: websockets.ClientConnection, label: str, timeout: float = 6.0) -> None:
    """Read responses until a timeout."""
    count = 0
    while True:
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            if count == 0:
                safe_print(f"{label}: no response within {timeout:.0f}s")
            return
        count += 1
        safe_print(f"{label}: response #{count}: {response}")
        if _is_terminal_response_end(response):
            return


async def main() -> None:
    """Open one websocket connection and send several questions."""
    if not TOKEN:
        raise RuntimeError("Set REACHY_PLATFORM_TOKEN before running this probe.")
    async with websockets.connect(URL, additional_headers={"authorization": f"Bearer {TOKEN}"}) as ws:
        safe_print(f"connected: {URL}")
        for index, question in enumerate(QUESTIONS, start=1):
            text = build_message(question, index)
            await ws.send(text)
            safe_print(f"sent #{index}: {text}")
            await drain_responses(ws, f"question #{index}")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
