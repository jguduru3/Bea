"""Connected apps: Microsoft 365, Google, and custom/company APIs (Bea Hub → Connections)."""
from __future__ import annotations

import html
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.connectors import custom_api, google, microsoft, store
from ..agent.registry import ToolError
from ..db import ApiToken, ConnectorAccount, utcnow
from ..ratelimit import rate_limited
from ..security import get_session, require_device

log = logging.getLogger("pom.connectors")

router = APIRouter(prefix="/v1/connectors", tags=["connectors"])
AUTH = [Depends(require_device), Depends(rate_limited())]

MICROSOFT_HELP = (
    "1. Open portal.azure.com → Microsoft Entra ID → App registrations → New registration. Name it 'Bea'. "
    "Supported account types: 'Accounts in any organizational directory and personal Microsoft accounts'. "
    "No redirect URI is needed. Click Register.\n"
    "2. Authentication → Advanced settings → 'Allow public client flows' = Yes → Save.\n"
    "3. API permissions → Add a permission → Microsoft Graph → Delegated permissions: Mail.ReadWrite, Mail.Send, "
    "Calendars.ReadWrite, User.Read, offline_access → Add. (Work accounts may need an admin to grant consent.)\n"
    "4. Copy the 'Application (client) ID' from Overview into POM_MICROSOFT_CLIENT_ID in backend/.env "
    "(optionally POM_MICROSOFT_TENANT=<your tenant id> to allow only your organization; default 'common'), "
    "then restart the Bea server.")


def google_help(public_base_url: str) -> str:
    return (
        "1. Open console.cloud.google.com, create or pick a project.\n"
        "2. APIs & Services → Library: enable 'Gmail API' and 'Google Calendar API'.\n"
        "3. APIs & Services → OAuth consent screen: User type External (or Internal for Workspace), fill in the app "
        "name and your email, add the scopes gmail.modify and calendar, and add yourself under Test users.\n"
        "4. APIs & Services → Credentials → Create credentials → OAuth client ID → Application type 'Web application'. "
        f"Authorized redirect URI: {public_base_url.rstrip('/')}/v1/connectors/google/callback\n"
        "5. Put the Client ID into POM_GOOGLE_CLIENT_ID and the Client secret into POM_GOOGLE_CLIENT_SECRET in "
        "backend/.env (set POM_PUBLIC_BASE_URL if the server isn't at "
        f"{public_base_url}), then restart the Bea server.")


def _settings(request: Request):
    return request.app.state.settings


async def _own_row(session: AsyncSession, token: ApiToken, account_id: str) -> ConnectorAccount:
    row = await session.get(ConnectorAccount, account_id)
    if row is None or row.token_id != token.id:
        raise HTTPException(404, "No such connection.")
    return row


# ---------------------------------------------------------------- overview
@router.get("", dependencies=AUTH, summary="Providers and connected accounts (never secrets)")
async def list_connectors(request: Request, token: ApiToken = Depends(require_device)):
    s = _settings(request)
    rows = await store.list_accounts(request.app.state.db.sessionmaker, token.id)
    return {
        "providers": {
            "microsoft": {"configured": bool(s.microsoft_client_id), "setup_help": MICROSOFT_HELP},
            "google": {"configured": bool(s.google_client_id and s.google_client_secret
                                          and s.google_client_secret.get_secret_value()),
                       "setup_help": google_help(s.public_base_url),
                       "redirect_uri": google.redirect_uri(s)},
            "custom_api": {"configured": True},
        },
        "accounts": [store.public(r) for r in rows],
    }


# ---------------------------------------------------------------- Microsoft
@router.post("/microsoft/start", dependencies=AUTH, summary="Start Microsoft sign-in (device code)")
async def microsoft_start(request: Request, token: ApiToken = Depends(require_device)):
    if not _settings(request).microsoft_client_id:
        raise HTTPException(409, "Microsoft 365 isn't set up on this server yet. " + MICROSOFT_HELP)
    try:
        return await microsoft.start_device_flow(request.app, token.id)
    except ToolError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/microsoft/status/{flow_id}", dependencies=AUTH, summary="Microsoft sign-in progress")
async def microsoft_status(flow_id: str, token: ApiToken = Depends(require_device)):
    st = microsoft.flow_status(flow_id, token.id)
    if st is None:
        raise HTTPException(404, "Unknown sign-in. Start again.")
    return st


# ---------------------------------------------------------------- Google
@router.post("/google/start", dependencies=AUTH, summary="Start Google sign-in; open auth_url in the browser")
async def google_start(request: Request, token: ApiToken = Depends(require_device)):
    s = _settings(request)
    if not (s.google_client_id and s.google_client_secret and s.google_client_secret.get_secret_value()):
        raise HTTPException(409, "Google isn't set up on this server yet. " + google_help(s.public_base_url))
    return google.start_auth(s, token.id)


def _page(title: str, text: str, ok: bool, status: int = 200) -> HTMLResponse:
    color = "#1a7f37" if ok else "#b42318"
    body = (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            f"<title>{html.escape(title)}</title></head>"
            f"<body style='font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem;text-align:center'>"
            f"<h1 style='color:{color};font-size:1.4rem'>{html.escape(title)}</h1><p>{html.escape(text)}</p></body></html>")
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("/google/callback", include_in_schema=False)
async def google_callback(request: Request, state: str = "", code: str | None = None, error: str | None = None):
    try:
        flow = await google.finish_auth(request.app, state, code, error)
    except ToolError as exc:
        return _page("Sign-in link expired", str(exc), ok=False, status=400)
    if flow["status"] == "connected":
        return _page("Connected", f"Bea is now connected to {flow['account']['name']}. You can close this tab.", ok=True)
    return _page("Couldn't connect Google", flow.get("error") or "Sign-in failed. Try again from Bea.", ok=False, status=400)


@router.get("/google/status/{flow_id}", dependencies=AUTH, summary="Google sign-in progress")
async def google_status(flow_id: str, token: ApiToken = Depends(require_device)):
    st = google.flow_status(flow_id, token.id)
    if st is None:
        raise HTTPException(404, "Unknown sign-in. Start again.")
    return st


# ---------------------------------------------------------------- custom APIs
class CustomApiIn(BaseModel):
    name: str = Field(description="Short name Bea uses, e.g. 'projects'")
    base_url: str = Field(max_length=500)
    auth_header: str = Field(default="Authorization", max_length=100)
    auth_scheme: str = Field(default="Bearer", max_length=40)
    token: str | None = Field(default=None, max_length=8000)
    description: str = Field(default="", max_length=20000)
    allow_write: bool = False
    openapi_url: str | None = Field(default=None, max_length=500)
    health_path: str | None = Field(default=None, max_length=300)


def _validate_custom(body: CustomApiIn) -> dict:
    name = body.name.strip().lower()
    if not custom_api.NAME_RE.match(name):
        raise HTTPException(400, "Name must be 2–32 characters: lowercase letters, digits, '-' or '_' (e.g. projects).")
    base = body.base_url.strip().rstrip("/")
    u = urlsplit(base)
    if u.scheme not in ("http", "https") or not u.netloc or u.query or u.fragment:
        raise HTTPException(400, "Base URL must start with http:// or https:// and have no ?query or #fragment.")
    header = body.auth_header.strip() or "Authorization"
    if not all(c.isalnum() or c in "-_" for c in header):
        raise HTTPException(400, "Auth header must be a plain header name, e.g. Authorization or X-API-Key.")
    if body.health_path and not body.health_path.startswith("/"):
        raise HTTPException(400, "Health path must start with '/'.")
    if body.token is not None and any(c in body.token for c in "\r\n"):
        raise HTTPException(400, "The token contains line breaks. Copy it again without them.")
    openapi = (body.openapi_url or "").strip() or None
    if openapi and "://" in openapi and urlsplit(openapi).scheme not in ("http", "https"):
        raise HTTPException(400, "OpenAPI URL must be http(s) or a path on the API.")
    meta = {"base_url": base, "auth_header": header, "auth_scheme": body.auth_scheme.strip(),
            "description": body.description.strip(), "allow_write": body.allow_write, "openapi_url": openapi}
    if body.health_path:
        meta["health_path"] = body.health_path
    return {"name": name, "meta": meta}


@router.post("/custom-apis", dependencies=AUTH, status_code=201, summary="Add a company/custom API")
async def add_custom_api(body: CustomApiIn, request: Request, token: ApiToken = Depends(require_device)):
    v = _validate_custom(body)
    sm = request.app.state.db.sessionmaker
    if any(r.name == v["name"] for r in await store.list_accounts(sm, token.id, custom_api.PROVIDER)):
        raise HTTPException(409, f"An API named {v['name']!r} already exists. Edit it instead, or pick another name.")
    row = await store.save_account(sm, _settings(request), token.id, custom_api.PROVIDER, v["name"],
                                   {"token": (body.token or "").strip()}, v["meta"])
    log.info("custom api added", extra={"token_id": token.id, "api": v["name"]})
    return store.public(row)


@router.put("/custom-apis/{account_id}", dependencies=AUTH, summary="Edit a company/custom API (token optional)")
async def edit_custom_api(account_id: str, body: CustomApiIn, request: Request, token: ApiToken = Depends(require_device),
                          session: AsyncSession = Depends(get_session)):
    row = await _own_row(session, token, account_id)
    if row.provider != custom_api.PROVIDER:
        raise HTTPException(404, "No such custom API.")
    v = _validate_custom(body)
    sm = request.app.state.db.sessionmaker
    if any(r.name == v["name"] and r.id != row.id for r in await store.list_accounts(sm, token.id, custom_api.PROVIDER)):
        raise HTTPException(409, f"Another API is already named {v['name']!r}.")
    s = _settings(request)
    secret = {"token": body.token.strip()} if body.token is not None else await store.load_secret(s, row)
    enabled = row.enabled
    saved = await store.save_account(sm, s, token.id, custom_api.PROVIDER, v["name"], secret, v["meta"], account_id=row.id)
    if not enabled:  # save_account re-enables; keep the person's choice
        await _set_enabled(sm, row.id, False)
        saved.enabled = False
    return store.public(saved)


# ---------------------------------------------------------------- any account
class EnabledIn(BaseModel):
    enabled: bool


async def _set_enabled(sessionmaker, account_id: str, enabled: bool) -> None:
    async with sessionmaker() as s:
        row = await s.get(ConnectorAccount, account_id)
        if row:
            row.enabled = enabled
            row.updated_at = utcnow()
            await s.commit()


@router.post("/{account_id}/test", dependencies=AUTH, summary="Check that a connection works")
async def test_connection(account_id: str, request: Request, token: ApiToken = Depends(require_device),
                          session: AsyncSession = Depends(get_session)):
    row = await _own_row(session, token, account_id)
    checker = {microsoft.PROVIDER: microsoft.check_account, google.PROVIDER: google.check_account,
               custom_api.PROVIDER: custom_api.check_account}.get(row.provider)
    if checker is None:
        raise HTTPException(400, "This connection type can't be tested.")
    ok, message = await checker(request.app, row)
    return {"ok": ok, "message": message}


@router.patch("/{account_id}", dependencies=AUTH, summary="Turn a connection on or off")
async def set_enabled(account_id: str, body: EnabledIn, request: Request, token: ApiToken = Depends(require_device),
                      session: AsyncSession = Depends(get_session)):
    row = await _own_row(session, token, account_id)
    row.enabled = body.enabled
    row.updated_at = utcnow()
    await session.commit()
    return store.public(row)


@router.delete("/{account_id}", dependencies=AUTH, summary="Remove a connection (and revoke Google access)")
async def delete_connection(account_id: str, request: Request, token: ApiToken = Depends(require_device),
                            session: AsyncSession = Depends(get_session)):
    row = await _own_row(session, token, account_id)
    if row.provider == google.PROVIDER:
        await google.revoke(request.app, row)
    ok = await store.delete_account(request.app.state.db.sessionmaker, token.id, row.id)
    if not ok:
        raise HTTPException(404, "No such connection.")
    log.info("connector removed", extra={"token_id": token.id, "provider": row.provider})
    return {"deleted": True, "id": account_id}


