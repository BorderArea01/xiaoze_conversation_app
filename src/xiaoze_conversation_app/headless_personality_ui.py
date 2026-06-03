"""Settings UI routes for headless personality management.

Exposes REST endpoints on the provided FastAPI settings app. The
implementation schedules backend actions (apply personality, fetch voices)
onto the running LocalStream asyncio loop using the supplied get_loop
callable to avoid cross-thread issues.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Callable, Optional

from fastapi import Query, FastAPI, Request

from .config import (
    LOCKED_PROFILE,
    config,
    get_default_voice_for_backend,
    get_available_voices_for_backend,
)
from .conversation_handler import ConversationHandler
from .headless_personality import (
    DEFAULT_OPTION,
    SCENARIO_PRESETS,
    _sanitize_name,
    _write_profile,
    read_tools_for,
    list_personalities,
    available_tools_for,
    resolve_profile_dir,
    read_instructions_for,
)


logger = logging.getLogger(__name__)


def mount_personality_routes(
    app: FastAPI,
    handler: ConversationHandler,
    get_loop: Callable[[], asyncio.AbstractEventLoop | None],
    *,
    persist_personality: Callable[[Optional[str], Optional[str]], None] | None = None,
    get_persisted_personality: Callable[[], Optional[str]] | None = None,
) -> None:
    """Register personality management endpoints on a FastAPI app."""
    try:
        from pydantic import BaseModel
        from fastapi.responses import JSONResponse
    except Exception:  # pragma: no cover - only when settings app not available
        return

    class ApplyPayload(BaseModel):
        name: str
        persist: Optional[bool] = False

    def _choices() -> list[str]:
        return list_personalities()

    def _fallback_choice() -> str:
        choices = _choices()
        return choices[0] if choices else ""

    def _normalize_choice(value: str | None) -> str:
        if value and value in _choices():
            return value
        return _fallback_choice()

    def _startup_choice() -> Any:
        """Return the persisted startup personality or default."""
        try:
            if get_persisted_personality is not None:
                stored = get_persisted_personality()
                if stored:
                    return _normalize_choice(stored)
            env_val = getattr(config, "REACHY_MINI_CUSTOM_PROFILE", None)
            if env_val:
                return _normalize_choice(env_val)
        except Exception:
            pass
        return _fallback_choice()

    def _current_choice() -> str:
        try:
            cur = getattr(config, "REACHY_MINI_CUSTOM_PROFILE", None)
            return _normalize_choice(cur)
        except Exception:
            return _fallback_choice()

    @app.get("/personalities")
    def _list() -> dict:  # type: ignore
        choices = _choices()
        return {
            "choices": choices,
            "current": _current_choice(),
            "startup": _startup_choice(),
            "locked": LOCKED_PROFILE is not None,
            "locked_to": LOCKED_PROFILE,
        }

    @app.get("/personalities/load")
    def _load(name: str) -> dict:  # type: ignore
        instr = read_instructions_for(name)
        tools_txt = read_tools_for(name)
        voice = get_default_voice_for_backend()
        uses_default_voice = True
        if name != DEFAULT_OPTION:
            pdir = resolve_profile_dir(name)
            vf = pdir / "voice.txt"
            if vf.exists():
                v = vf.read_text(encoding="utf-8").strip()
                voice = v or get_default_voice_for_backend()
                uses_default_voice = not bool(v)
        avail = available_tools_for(name)
        enabled = [ln.strip() for ln in tools_txt.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        return {
            "instructions": instr,
            "tools_text": tools_txt,
            "voice": voice,
            "uses_default_voice": uses_default_voice,
            "available_tools": avail,
            "enabled_tools": enabled,
        }

    @app.post("/personalities/save")
    async def _save(request: Request) -> dict:  # type: ignore
        # Accept raw JSON only to avoid validation-related 422s
        try:
            raw = await request.json()
        except Exception:
            raw = {}
        name = str(raw.get("name", ""))
        instructions = str(raw.get("instructions", ""))
        tools_text = str(raw.get("tools_text", ""))
        voice = (
            str(raw.get("voice", get_default_voice_for_backend()))
            if raw.get("voice") is not None
            else get_default_voice_for_backend()
        )

        name_s = _sanitize_name(name) or "自定义个性"
        try:
            logger.info(
                "Headless save: name=%r voice=%r instr_len=%d tools_len=%d",
                name_s,
                voice,
                len(instructions),
                len(tools_text),
            )
            _write_profile(name_s, instructions, tools_text, voice or get_default_voice_for_backend())
            value = f"user_personalities/{name_s}"
            choices = _choices()
            return {"ok": True, "value": value, "choices": choices}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)  # type: ignore

    @app.post("/personalities/save_raw")
    async def _save_raw(
        request: Request,
        name: Optional[str] = None,
        instructions: Optional[str] = None,
        tools_text: Optional[str] = None,
        voice: Optional[str] = None,
    ) -> dict:  # type: ignore
        # Accept query params, form-encoded, or raw JSON
        data = {"name": name, "instructions": instructions, "tools_text": tools_text, "voice": voice}
        # Prefer form if present
        try:
            form = await request.form()
            for k in ("name", "instructions", "tools_text", "voice"):
                if k in form and form[k] is not None:
                    data[k] = str(form[k])
        except Exception:
            pass
        # Try JSON
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                for k in ("name", "instructions", "tools_text", "voice"):
                    if raw.get(k) is not None:
                        data[k] = str(raw.get(k))
        except Exception:
            pass

        name_s = _sanitize_name(str(data.get("name") or "")) or "自定义个性"
        instr = str(data.get("instructions") or "")
        tools = str(data.get("tools_text") or "")
        v = str(data.get("voice") or get_default_voice_for_backend())
        try:
            logger.info(
                "Headless save_raw: name=%r voice=%r instr_len=%d tools_len=%d", name_s, v, len(instr), len(tools)
            )
            _write_profile(name_s, instr, tools, v)
            value = f"user_personalities/{name_s}"
            choices = _choices()
            return {"ok": True, "value": value, "choices": choices}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)  # type: ignore

    @app.get("/personalities/save_raw")
    async def _save_raw_get(name: str, instructions: str = "", tools_text: str = "", voice: str | None = None) -> dict:  # type: ignore
        name_s = _sanitize_name(name) or "自定义个性"
        try:
            normalized_voice = voice or get_default_voice_for_backend()
            logger.info(
                "Headless save_raw(GET): name=%r voice=%r instr_len=%d tools_len=%d",
                name_s,
                normalized_voice,
                len(instructions),
                len(tools_text),
            )
            _write_profile(name_s, instructions, tools_text, normalized_voice)
            value = f"user_personalities/{name_s}"
            choices = _choices()
            return {"ok": True, "value": value, "choices": choices}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)  # type: ignore

    @app.post("/personalities/apply")
    async def _apply(
        request: Request,
        payload: ApplyPayload | None = None,
        name: str | None = None,
        persist: Optional[bool] = None,
    ) -> dict:  # type: ignore
        if LOCKED_PROFILE is not None:
            return JSONResponse(
                {"ok": False, "error": "profile_locked", "locked_to": LOCKED_PROFILE},
                status_code=403,
            )  # type: ignore
        # Accept both JSON payload and query param for convenience
        sel_name: Optional[str] = None
        persist_flag = bool(persist) if persist is not None else False
        if payload and getattr(payload, "name", None):
            sel_name = payload.name
            persist_flag = bool(getattr(payload, "persist", False))
        elif name:
            sel_name = name
        else:
            try:
                body = await request.json()
                if isinstance(body, dict) and body.get("name"):
                    sel_name = str(body.get("name"))
                if isinstance(body, dict) and "persist" in body:
                    persist_flag = bool(body.get("persist"))
            except Exception:
                sel_name = None
        try:
            q_persist = request.query_params.get("persist")
            if q_persist is not None:
                persist_flag = str(q_persist).lower() in {"1", "true", "yes", "on"}
        except Exception:
            pass
        if not sel_name:
            sel_name = _fallback_choice()
        if sel_name not in _choices():
            return JSONResponse({"ok": False, "error": "unknown_profile"}, status_code=404)  # type: ignore

        try:
            logger.info("Headless apply: requested name=%r", sel_name)
            get_current_voice = getattr(handler, "get_current_voice", None)
            voice_override = get_current_voice() if callable(get_current_voice) else None
            persisted_choice = _startup_choice()
            if persist_flag and persist_personality is not None:
                try:
                    persist_personality(sel_name, voice_override)
                    persisted_choice = _startup_choice()
                except Exception as e:
                    logger.warning("Failed to persist startup personality: %s", e)
            return {
                "ok": True,
                "status": f"已保存个性「{sel_name}」，重启应用后生效。",
                "startup": persisted_choice,
                "requires_restart": True,
            }
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)  # type: ignore

    @app.get("/voices")
    async def _voices() -> list[str]:
        return get_available_voices_for_backend()

    @app.get("/voices/current")
    async def _current_voice() -> dict[str, str]:
        fallback_voice = get_default_voice_for_backend()
        try:
            return {"voice": handler.get_current_voice()}
        except Exception:
            return {"voice": fallback_voice}

    @app.post("/voices/apply")
    async def _apply_voice(request: Request, voice: str | None = Query(None)) -> dict:  # type: ignore
        voice = str(voice or "")
        if not voice:
            try:
                raw = await request.json()
            except Exception:
                raw = {}
            voice = str(raw.get("voice", "") or "")
        if not voice:
            return JSONResponse({"ok": False, "error": "missing_voice"}, status_code=400)  # type: ignore
        try:
            if persist_personality is not None:
                current_profile = _current_choice()
                persist_personality(current_profile or None, voice)
            return {
                "ok": True,
                "status": f"已保存音色「{voice}」，重启应用后生效。",
                "requires_restart": True,
            }
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)  # type: ignore

    @app.post('/personalities/delete')
    async def _delete(name: str | None = None) -> dict:  # type: ignore
        """Delete a user personality profile."""
        if LOCKED_PROFILE is not None:
            return JSONResponse(
                {'ok': False, 'error': 'profile_locked', 'locked_to': LOCKED_PROFILE},
                status_code=403,
            )  # type: ignore
        if not name:
            return JSONResponse({'ok': False, 'error': 'missing_name'}, status_code=400)  # type: ignore
        name_s = _sanitize_name(name)
        if not name_s:
            return JSONResponse({'ok': False, 'error': 'invalid_name'}, status_code=400)  # type: ignore
        try:
            import shutil
            pdir = resolve_profile_dir(name_s)
            if not pdir.exists():
                return JSONResponse({'ok': False, 'error': 'not_found'}, status_code=404)  # type: ignore
            if name_s in SCENARIO_PRESETS:
                return JSONResponse({'ok': False, 'error': 'builtin_profile'}, status_code=403)  # type: ignore
            shutil.rmtree(str(pdir))
            logger.info('Headless delete: name=%r', name_s)
            choices = _choices()
            return {'ok': True, 'choices': choices}
        except Exception as e:
            return JSONResponse({'ok': False, 'error': str(e)}, status_code=500)  # type: ignore
