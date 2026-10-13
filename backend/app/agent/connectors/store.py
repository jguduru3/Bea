"""Encrypted storage for connected accounts (OAuth tokens, API keys).

Secrets are encrypted with the same key as the AI provider keys (app/keys.py) and are never
returned to the desktop app; only names and non-secret metadata are.
"""
from __future__ import annotations

import json

from sqlalchemy import select

from ...db import ConnectorAccount, utcnow
from ...keys import _fernet


def _enc(settings, data: dict) -> str:
    return _fernet(settings).encrypt(json.dumps(data).encode()).decode()


def _dec(settings, blob: str) -> dict:
    return json.loads(_fernet(settings).decrypt(blob.encode()).decode())


async def save_account(sessionmaker, settings, token_id: str, provider: str, name: str, secret: dict,
                       meta: dict | None = None, account_id: str | None = None) -> ConnectorAccount:
    """Create, or replace the account with the same provider and name (or id)."""
    async with sessionmaker() as s:
        q = select(ConnectorAccount).where(ConnectorAccount.token_id == token_id, ConnectorAccount.provider == provider)
        q = q.where(ConnectorAccount.id == account_id) if account_id else q.where(ConnectorAccount.name == name)
        row = (await s.execute(q)).scalar_one_or_none()
        if row is None:
            row = ConnectorAccount(token_id=token_id, provider=provider, name=name)
            s.add(row)
        row.name = name
        row.secret = _enc(settings, secret)
        row.meta = json.dumps(meta or {})
        row.enabled = True
        row.updated_at = utcnow()
        await s.commit()
        return row


async def list_accounts(sessionmaker, token_id: str, provider: str | None = None, enabled_only: bool = False) -> list[ConnectorAccount]:
    async with sessionmaker() as s:
        q = select(ConnectorAccount).where(ConnectorAccount.token_id == token_id)
        if provider:
            q = q.where(ConnectorAccount.provider == provider)
        if enabled_only:
            q = q.where(ConnectorAccount.enabled.is_(True))
        return list((await s.execute(q.order_by(ConnectorAccount.created_at))).scalars())


async def load_secret(settings, row: ConnectorAccount) -> dict:
    return _dec(settings, row.secret)


async def update_secret(sessionmaker, settings, account_id: str, secret: dict) -> None:
    async with sessionmaker() as s:
        row = await s.get(ConnectorAccount, account_id)
        if row:
            row.secret = _enc(settings, secret)
            row.updated_at = utcnow()
            await s.commit()


async def delete_account(sessionmaker, token_id: str, account_id: str) -> bool:
    async with sessionmaker() as s:
        row = await s.get(ConnectorAccount, account_id)
        if row is None or row.token_id != token_id:
            return False
        await s.delete(row)
        await s.commit()
        return True


def public(row: ConnectorAccount) -> dict:
    """What the app may see about an account: never the secret."""
    meta = json.loads(row.meta or "{}")
    return {"id": row.id, "provider": row.provider, "name": row.name, "enabled": row.enabled, "meta": meta,
            "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}
