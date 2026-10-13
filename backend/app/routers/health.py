from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from ..config import Settings, app_settings
from ..db import ApiToken
from ..emotion import ACTIONS, EMOTIONS
from ..persona import LANGUAGES
from ..security import require_device

router = APIRouter(tags=["ops"])


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response, settings: Settings = Depends(app_settings)):
    db_ok = True
    try:
        async with request.app.state.db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    if not db_ok:
        response.status_code = 503
    return {"database": db_ok, "chat_configured": settings.llm_configured, "voice_configured": settings.voice_configured}


@router.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/v1/config", tags=["chat"], summary="What this server offers, for the desktop app")
async def client_config(token: ApiToken = Depends(require_device), settings: Settings = Depends(app_settings)):
    return {
        "assistant_name": settings.assistant_name,
        "device_name": token.name,
        "chat": settings.llm_configured,
        "voice": settings.voice_configured,
        "manage_keys": settings.allow_device_key_management,
        "emotions": list(EMOTIONS),
        "actions": list(ACTIONS),
        "languages": [{"code": k, "name": v} for k, v in LANGUAGES.items()],
        "default_language": settings.default_language,
        "speaker": settings.sarvam_tts_speaker,
        "max_tts_chars": settings.max_tts_chars,
    }
