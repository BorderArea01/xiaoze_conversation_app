from __future__ import annotations
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import JSONResponse

from xiaoze_conversation_app.platform_agent import _extract_text
from xiaoze_conversation_app.platform_client import PlatformClient, PlatformClientConfig


class PlatformChatPayload(BaseModel):
    """Typed text chat payload for the platform terminal websocket."""

    query: str
    timeout_s: float = 8.0


async def send_platform_chat(query: str, timeout_s: float = 8.0) -> dict[str, Any]:
    """Send a typed query to the platform agent and return merged stream text."""
    clean_query = (query or "").strip()
    if not clean_query:
        return {"ok": False, "error": "empty_query", "text": "", "responses": []}

    client = PlatformClient(PlatformClientConfig.from_env())
    responses = await client.send_asr_signal(clean_query, response_timeout_s=max(1.0, timeout_s))
    chunks: list[str] = []
    for response in responses:
        chunk = _extract_text(response).strip()
        if chunk:
            chunks.append(chunk)
    return {
        "ok": True,
        "query": clean_query,
        "text": "".join(chunks).strip(),
        "responses": responses,
    }


def mount_platform_chat_routes(app: FastAPI) -> None:
    """Mount text-chat routes onto the app settings server."""

    @app.post("/platform_chat")
    async def _platform_chat(payload: PlatformChatPayload) -> JSONResponse:
        result = await send_platform_chat(payload.query, payload.timeout_s)
        status_code = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=status_code)

