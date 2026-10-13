"""AI and voice API keys managed from the app (Settings > API keys).

Keys saved from the app are encrypted at rest and take priority over the ones in .env.
Deleting a saved key falls back to the .env value, if there is one. Every change is applied
to the running server straight away, with no restart.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from pathlib import Path

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select

from .config import Settings
from .db import ProviderKey
from .providers.base import _error_message
from .providers.llm import build_llm
from .providers.sarvam import SarvamClient

log = logging.getLogger("pom.keys")

PROVIDERS = {
    "anthropic": {"label": "Claude (Anthropic)", "field": "anthropic_api_key", "model": "anthropic_model"},
    "openai": {"label": "OpenAI", "field": "openai_api_key", "model": "openai_model"},
    "sarvam": {"label": "Sarvam AI", "field": "sarvam_api_key", "model": "sarvam_tts_model"},
}


def _fernet(settings: Settings) -> Fernet:
    raw = settings.key_encryption_secret.get_secret_value().strip() if settings.key_encryption_secret else ""
    if not raw:  # no secret configured: use (or create) a random one in secret_file
        path = Path(settings.secret_file)
        if not path.exists():
            path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        raw = path.read_text(encoding="utf-8").strip()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def encrypt(settings: Settings, value: str) -> str:
    return _fernet(settings).encrypt(value.encode()).decode()


def current_key(settings: Settings, provider: str) -> str:
    val = getattr(settings, PROVIDERS[provider]["field"])
    return val.get_secret_value() if val else ""


def role(settings: Settings, provider: str) -> str:
    if provider == "sarvam":
        return "Voice"
    if settings.llm_provider == provider:
        return "Main brain"
    if settings.llm_fallback_provider == provider:
        return "Backup brain"
    return "Not used"


async def apply_saved_keys(app: FastAPI) -> None:
    """Rebuild the live settings and clients from .env plus the keys saved in the database."""
    base: Settings = app.state.base_settings
    async with app.state.db.sessionmaker() as s:
        rows = (await s.execute(select(ProviderKey))).scalars().all()
    update, saved = {}, {}
    if rows:
        f = _fernet(base)
        for r in rows:
            if r.provider not in PROVIDERS:
                continue
            try:
                update[PROVIDERS[r.provider]["field"]] = SecretStr(f.decrypt(r.secret.encode()).decode())
                saved[r.provider] = r
            except InvalidToken:
                log.error("saved key cannot be decrypted (encryption secret changed?)", extra={"provider": r.provider})
    settings = base.model_copy(update=update)
    app.state.settings = settings
    app.state.saved_keys = saved
    app.state.llm = build_llm(settings, app.state.http)
    app.state.sarvam = SarvamClient(settings, app.state.http)


def _login_token_hint(provider: str, key: str) -> str:
    if provider == "anthropic" and key.startswith("sk-ant-") and not key.startswith("sk-ant-api"):
        return (" This looks like a Claude.ai sign-in token, not an API key. Create an API key at"
                " console.anthropic.com > Settings > API keys (it starts with sk-ant-api).")
    return ""


async def verify(settings: Settings, client: httpx.AsyncClient, provider: str, key: str) -> tuple[bool, str]:
    """Check a key with a tiny real request (about one token), so missing credit or a model the
    key can't use is caught now rather than on the first chat.

    Returns (accepted, message). Raises httpx.TransportError if the provider can't be reached.
    """
    label = PROVIDERS[provider]["label"]
    if provider == "anthropic":
        resp = await client.post(settings.anthropic_base_url.rstrip("/") + "/v1/messages", timeout=20,
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                                 json={"model": settings.anthropic_model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]})
    elif provider == "openai":
        resp = await client.post(settings.openai_base_url.rstrip("/") + "/chat/completions", timeout=20,
                                 headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
                                 json={"model": settings.openai_model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]})
    else:
        # An empty request: Sarvam checks the key first, so 401/403 means a bad key and a
        # validation error means the key was accepted. Nothing is generated or billed.
        resp = await client.post(settings.sarvam_base_url.rstrip("/") + "/text-to-speech", timeout=15,
                                 headers={"api-subscription-key": key, "content-type": "application/json"}, json={})
    if resp.status_code < 400 or (provider == "sarvam" and resp.status_code not in (401, 403)):
        return True, f"{label} accepted the key."
    detail = _error_message(resp.content)
    log.warning("key check failed", extra={"provider": provider, "status": resp.status_code, "detail": detail})
    if resp.status_code in (401, 403):
        msg = f"{label} rejected this key ({detail})." + (_login_token_hint(provider, key) or
              " Check that you copied the whole key and that it is still active.")
    elif resp.status_code == 404:
        model = getattr(settings, PROVIDERS[provider]["model"])
        msg = f"The key works, but the model {model} isn't available to it ({detail})."
    elif "credit" in detail.lower() or "quota" in detail.lower() or "billing" in detail.lower():
        msg = f"The key is valid, but the {label} account has no credit: {detail}"
    else:
        msg = f"{label} returned an error ({resp.status_code}): {detail}"
    return False, msg
