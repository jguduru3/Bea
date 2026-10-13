from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, app_settings
from ..db import ApiToken, ProviderKey
from ..keys import PROVIDERS, apply_saved_keys, current_key, encrypt, role, verify
from ..ratelimit import rate_limited
from ..security import get_session, require_device

log = logging.getLogger("pom.keys")


def require_key_management(settings: Settings = Depends(app_settings)) -> None:
    if not settings.allow_device_key_management:
        raise HTTPException(403, "Managing API keys from the app is turned off on this server. "
                                 "Set POM_ALLOW_DEVICE_KEY_MANAGEMENT=true in the server's .env, or ask your admin.")


router = APIRouter(prefix="/v1/keys", tags=["keys"],
                   dependencies=[Depends(require_device), Depends(require_key_management), Depends(rate_limited())])


class KeyIn(BaseModel):
    key: str = Field(min_length=8, max_length=500)


class KeyOut(BaseModel):
    provider: str
    label: str
    role: str
    model: str
    configured: bool
    source: str | None = Field(description="'app' when saved from the app, 'server' when it comes from .env")
    hint: str | None = Field(description="Last four characters, never the whole key")
    updated_at: str | None


class KeyCheck(BaseModel):
    ok: bool
    message: str


def _provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise HTTPException(404, f"Unknown provider. Use one of: {', '.join(PROVIDERS)}")
    return provider


def _item(request: Request, provider: str) -> dict:
    settings: Settings = request.app.state.settings
    saved = request.app.state.saved_keys.get(provider)
    key = current_key(settings, provider)
    return {
        "provider": provider, "label": PROVIDERS[provider]["label"], "role": role(settings, provider),
        "model": getattr(settings, PROVIDERS[provider]["model"]), "configured": bool(key),
        "source": "app" if saved else ("server" if key else None),
        "hint": ("…" + key[-4:]) if key else None,
        "updated_at": saved.updated_at.isoformat() if saved else None,
    }


async def _check(request: Request, provider: str, key: str) -> tuple[bool, str]:
    try:
        return await verify(request.app.state.settings, request.app.state.http, provider, key)
    except httpx.TransportError as exc:
        raise HTTPException(502, f"Couldn't reach {PROVIDERS[provider]['label']} to check the key "
                                 f"({exc.__class__.__name__}). Check the internet connection and try again.") from exc


@router.get("", response_model=list[KeyOut], summary="List the AI and voice keys (masked)")
async def list_keys(request: Request):
    return [_item(request, p) for p in PROVIDERS]


@router.put("/{provider}", response_model=KeyOut, summary="Check a key with the provider, then save it")
async def save_key(provider: str, body: KeyIn, request: Request, token: ApiToken = Depends(require_device),
                   session: AsyncSession = Depends(get_session)):
    provider = _provider(provider)
    key = body.key.strip()
    if any(c.isspace() for c in key):
        raise HTTPException(400, "The key contains spaces or line breaks. Copy it again without them.")
    ok, message = await _check(request, provider, key)
    if not ok:
        raise HTTPException(400, message)
    row = await session.get(ProviderKey, provider)
    if row is None:
        row = ProviderKey(provider=provider)
        session.add(row)
    row.secret, row.last4 = encrypt(request.app.state.base_settings, key), key[-4:]
    await session.commit()
    await apply_saved_keys(request.app)
    log.info("api key saved", extra={"provider": provider, "token_id": token.id})
    return _item(request, provider)


@router.post("/{provider}/test", response_model=KeyCheck, summary="Check the key currently in use")
async def test_key(provider: str, request: Request):
    provider = _provider(provider)
    key = current_key(request.app.state.settings, provider)
    if not key:
        raise HTTPException(404, f"No {PROVIDERS[provider]['label']} key is set.")
    ok, message = await _check(request, provider, key)
    return {"ok": ok, "message": message}


@router.delete("/{provider}", response_model=KeyOut, summary="Delete a key saved from the app")
async def delete_key(provider: str, request: Request, token: ApiToken = Depends(require_device),
                     session: AsyncSession = Depends(get_session)):
    provider = _provider(provider)
    row = await session.get(ProviderKey, provider)
    if row is None:
        if current_key(request.app.state.settings, provider):
            raise HTTPException(409, "This key comes from the server's .env file, so it can't be deleted from the app. "
                                     "Remove it from .env and restart the server.")
        raise HTTPException(404, f"No {PROVIDERS[provider]['label']} key is saved.")
    await session.delete(row)
    await session.commit()
    await apply_saved_keys(request.app)
    log.info("api key deleted", extra={"provider": provider, "token_id": token.id})
    return _item(request, provider)
