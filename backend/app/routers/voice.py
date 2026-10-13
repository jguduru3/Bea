from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, app_settings
from ..db import ApiToken, UsageEvent
from ..emotion import strip_tags
from ..providers.base import UpstreamError
from ..ratelimit import rate_limited
from ..security import get_session, require_device

router = APIRouter(prefix="/v1", tags=["voice"])

AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/wav", "audio/x-wav", "audio/wave", "audio/mpeg", "audio/mp3",
               "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/aac", "audio/flac", "video/webm", "application/octet-stream"}


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1)
    language_code: str | None = None
    speaker: str | None = None
    pace: float | None = Field(default=None, ge=0.5, le=2.0)


@router.post("/tts", summary="Speak text with Sarvam Bulbul", response_class=Response,
             responses={200: {"content": {"audio/mpeg": {}, "audio/wav": {}}}})
async def tts(
    body: SpeakRequest,
    request: Request,
    token: ApiToken = Depends(require_device),
    _rl: None = Depends(rate_limited(0.5)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(app_settings),
):
    text = strip_tags(body.text)
    if not text:
        raise HTTPException(422, "Nothing to say")
    if len(text) > settings.max_tts_chars:
        raise HTTPException(413, f"Text is too long for one clip (limit {settings.max_tts_chars} characters)")
    sarvam = request.app.state.sarvam
    ok = True
    try:
        result = await sarvam.speak(text, body.language_code, body.speaker, body.pace)
    except UpstreamError as exc:
        ok = False
        raise HTTPException(502 if exc.status != 503 else 503, f"Voice service error: {exc.message}") from exc
    finally:
        session.add(UsageEvent(token_id=token.id, kind="tts", provider="sarvam", model=settings.sarvam_tts_model,
                               units_in=len(text), units_out=0, latency_ms=0, ok=ok))
        await session.commit()
    return Response(result.audio, media_type=result.media_type, headers={"Cache-Control": "no-store", "X-Latency-Ms": str(result.latency_ms)})


class TranscriptOut(BaseModel):
    transcript: str
    language_code: str | None


@router.post("/stt", response_model=TranscriptOut, summary="Transcribe speech with Sarvam Saaras")
async def stt(
    request: Request,
    file: UploadFile = File(...),
    language_code: str | None = Form(default=None),
    token: ApiToken = Depends(require_device),
    _rl: None = Depends(rate_limited(0.5)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(app_settings),
):
    ctype = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if ctype not in AUDIO_TYPES:
        raise HTTPException(415, f"Unsupported audio type: {ctype}")
    audio = await file.read(settings.max_audio_bytes + 1)
    if len(audio) > settings.max_audio_bytes:
        raise HTTPException(413, "Recording is too long")
    if len(audio) < 800:
        raise HTTPException(422, "Recording is empty")
    ok = True
    try:
        result = await request.app.state.sarvam.transcribe(audio, file.filename or "speech.webm", ctype, language_code)
    except UpstreamError as exc:
        ok = False
        raise HTTPException(502 if exc.status != 503 else 503, f"Voice service error: {exc.message}") from exc
    finally:
        session.add(UsageEvent(token_id=token.id, kind="stt", provider="sarvam", model=settings.sarvam_stt_model,
                               units_in=len(audio), units_out=0, latency_ms=0, ok=ok))
        await session.commit()
    return TranscriptOut(transcript=result.text, language_code=result.language_code)
