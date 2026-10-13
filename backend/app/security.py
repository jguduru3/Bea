"""Device-token and admin authentication."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, app_settings
from .db import ApiToken, utcnow

TOKEN_PREFIX = "pom_"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


async def get_session(request: Request):
    async for s in request.app.state.db.session():
        yield s


async def create_token(session: AsyncSession, name: str, token: str | None = None) -> tuple[ApiToken, str]:
    raw = token or generate_token()
    row = ApiToken(name=name[:120], prefix=raw[:12], token_hash=hash_token(raw))
    session.add(row)
    await session.commit()
    return row, raw


async def require_device(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> ApiToken:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    raw = authorization.split(" ", 1)[1].strip()
    row = (await session.execute(select(ApiToken).where(ApiToken.token_hash == hash_token(raw)))).scalar_one_or_none()
    if row is None or row.revoked:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked token", headers={"WWW-Authenticate": "Bearer"})
    now = utcnow()
    last = row.last_used_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=now.tzinfo)
    if last is None or now - last > timedelta(minutes=5):
        row.last_used_at = now
        await session.commit()
    request.state.token_id = row.id
    return row


def require_admin(x_admin_key: str | None = Header(default=None), settings: Settings = Depends(app_settings)) -> None:
    expected = settings.admin_api_key.get_secret_value() if settings.admin_api_key else ""
    if not expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin API is disabled. Set POM_ADMIN_API_KEY to enable it.")
    if not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid admin key")
