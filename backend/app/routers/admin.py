from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import ApiToken, UsageEvent, utcnow
from ..security import create_token, get_session, require_admin

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120, examples=["Rahul's laptop"])


class TokenOut(BaseModel):
    id: str
    name: str
    prefix: str
    created_at: str
    last_used_at: str | None
    revoked: bool


class TokenCreated(TokenOut):
    token: str = Field(description="Shown once. Paste it into Pom's settings.")


def _out(t: ApiToken) -> dict:
    return {"id": t.id, "name": t.name, "prefix": t.prefix, "created_at": t.created_at.isoformat(),
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None, "revoked": t.revoked}


@router.post("/tokens", response_model=TokenCreated, status_code=201)
async def create(body: TokenCreate, session: AsyncSession = Depends(get_session)):
    row, raw = await create_token(session, body.name)
    return {**_out(row), "token": raw}


@router.get("/tokens", response_model=list[TokenOut])
async def list_tokens(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ApiToken).order_by(ApiToken.created_at.desc()))).scalars()
    return [_out(r) for r in rows]


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke(token_id: str, session: AsyncSession = Depends(get_session)):
    row = await session.get(ApiToken, token_id)
    if row is None:
        raise HTTPException(404, "Token not found")
    row.revoked = True
    await session.commit()


@router.get("/usage")
async def usage(days: int = Query(7, ge=1, le=365), session: AsyncSession = Depends(get_session)):
    since = utcnow() - timedelta(days=days)
    q = (
        select(UsageEvent.kind, UsageEvent.provider, UsageEvent.model,
               func.count().label("calls"), func.sum(UsageEvent.units_in).label("units_in"),
               func.sum(UsageEvent.units_out).label("units_out"),
               func.sum(case((UsageEvent.ok.is_(False), 1), else_=0)).label("failures"))
        .where(UsageEvent.created_at >= since)
        .group_by(UsageEvent.kind, UsageEvent.provider, UsageEvent.model)
    )
    rows = (await session.execute(q)).all()
    return {
        "since": since.isoformat(),
        "by_kind": [
            {"kind": r.kind, "provider": r.provider, "model": r.model, "calls": r.calls,
             "units_in": int(r.units_in or 0), "units_out": int(r.units_out or 0), "failures": int(r.failures or 0)}
            for r in rows
        ],
        "units": {"chat": "tokens", "tts": "characters", "stt": "audio bytes"},
    }
