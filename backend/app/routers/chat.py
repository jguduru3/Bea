from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, app_settings
from ..db import ApiToken, Conversation, Message, UsageEvent, utcnow
from ..emotion import EmotionStream, guess, strip_tags
from ..persona import system_prompt
from ..providers.base import UpstreamError
from ..providers.llm import Usage
from ..ratelimit import rate_limited
from ..security import get_session, require_device

router = APIRouter(prefix="/v1", tags=["chat"])
log = logging.getLogger("pom.chat")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None
    language: str | None = Field(default=None, examples=["en-IN", "hi-IN", "te-IN"])
    client_time: str | None = Field(default=None, description="User's local time, e.g. 'Saturday 10 October 2026, 14:05 IST'")
    voice: bool = False


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _conversation(session: AsyncSession, token: ApiToken, conv_id: str | None) -> Conversation:
    if conv_id:
        conv = await session.get(Conversation, conv_id)
        if conv is None or conv.token_id != token.id:
            raise HTTPException(404, "Conversation not found")
        return conv
    conv = Conversation(token_id=token.id)
    session.add(conv)
    await session.flush()
    return conv


@router.post("/chat", summary="Chat with Pom (server-sent events)")
async def chat(
    body: ChatRequest,
    request: Request,
    token: ApiToken = Depends(require_device),
    _rl: None = Depends(rate_limited()),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(app_settings),
) -> StreamingResponse:
    text = body.message.strip()
    if len(text) > settings.max_message_chars:
        raise HTTPException(413, f"Message is too long (limit {settings.max_message_chars} characters)")
    if not settings.llm_configured:
        raise HTTPException(503, "The chat model is not configured on the server yet.")

    conv = await _conversation(session, token, body.conversation_id)
    seq = (await session.execute(select(func.coalesce(func.max(Message.seq), 0)).where(Message.conversation_id == conv.id))).scalar_one()
    history_rows = (
        await session.execute(
            select(Message).where(Message.conversation_id == conv.id).order_by(Message.seq.desc()).limit(settings.history_turns * 2)
        )
    ).scalars().all()
    history = [{"role": m.role, "content": m.content} for m in reversed(history_rows)]
    # Providers need the conversation to start with a user turn.
    while history and history[0]["role"] != "user":
        history.pop(0)
    session.add(Message(conversation_id=conv.id, seq=seq + 1, role="user", content=text))
    conv.updated_at = utcnow()
    await session.commit()

    llm = request.app.state.llm
    system = system_prompt(settings, body.language, body.client_time, body.voice)
    messages = [*history, {"role": "user", "content": text}]
    token_id, conv_id = token.id, conv.id
    sessionmaker = request.app.state.db.sessionmaker

    async def events() -> AsyncIterator[str]:
        usage = Usage()
        parser = EmotionStream()
        parts: list[str] = []
        ok = True
        yield sse("meta", {"conversation_id": conv_id})
        try:
            async for chunk in llm.stream(system, messages, usage):
                if await request.is_disconnected():
                    ok = False
                    break
                for piece in parser.feed(chunk):
                    if piece.kind == "emotion":
                        yield sse("emotion", {"emotion": piece.value})
                    elif piece.kind == "action":
                        yield sse("action", {"action": piece.value})
                    else:
                        parts.append(piece.value)
                        yield sse("delta", {"text": piece.value})
            for piece in parser.flush():  # flush only ever returns text
                parts.append(piece.value)
                yield sse("delta", {"text": piece.value})
        except UpstreamError as exc:
            ok = False
            log.warning("llm failed", extra={"provider": exc.provider, "status": exc.status, "detail": exc.message})
            yield sse("error", {"code": "upstream", "status": exc.status, "message": f"{settings.assistant_name} couldn't reach the model. Please try again."})
        except Exception:  # noqa: BLE001
            ok = False
            log.exception("chat stream crashed")
            yield sse("error", {"code": "internal", "message": "Something went wrong on the server."})

        reply = strip_tags("".join(parts))
        emotion = parser.first or guess(reply)
        async with sessionmaker() as s:
            if reply:
                s.add(Message(conversation_id=conv_id, seq=seq + 2, role="assistant", content=reply, emotion=emotion))
            s.add(UsageEvent(token_id=token_id, kind="chat", provider=usage.provider or llm.name, model=usage.model or llm.model,
                             units_in=usage.input_tokens, units_out=usage.output_tokens, latency_ms=usage.latency_ms, ok=ok))
            await s.commit()
        if ok:
            yield sse("done", {"text": reply, "emotion": emotion, "actions": parser.actions, "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class MessageOut(BaseModel):
    role: str
    content: str
    emotion: str | None
    created_at: str


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(conversation_id: str, token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.token_id != token.id:
        raise HTTPException(404, "Conversation not found")
    rows = (await session.execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.seq))).scalars()
    return [MessageOut(role=m.role, content=m.content, emotion=m.emotion, created_at=m.created_at.isoformat()) for m in rows]


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.token_id != token.id:
        raise HTTPException(404, "Conversation not found")
    await session.execute(delete(Message).where(Message.conversation_id == conversation_id))
    await session.delete(conv)
    await session.commit()
