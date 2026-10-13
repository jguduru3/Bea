"""Google (Gmail and Google Calendar).

Sign-in uses the OAuth 2.0 authorization code flow with PKCE and a redirect back to this
server ({public_base_url}/v1/connectors/google/callback). Google doesn't allow Gmail scopes
in the device flow, so the person signs in through their browser instead.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
from email.message import EmailMessage
from urllib.parse import quote, urlencode

import httpx

from ..registry import ToolContext, ToolError, obj, register_group, tool, untrusted
from . import store
from .microsoft import account_names, api_error, clip, fmt_range, html_to_text, now_s, people, pick_account

log = logging.getLogger("pom.connectors.google")

PROVIDER = "google"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
GCAL = "https://www.googleapis.com/calendar/v3"
SCOPES = "openid email https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/calendar"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
RECONNECT = "reconnect Google in Bea Hub → Connections"
FLOW_TTL = 600  # a sign-in link is valid for 10 minutes


def redirect_uri(settings) -> str:
    return settings.public_base_url.rstrip("/") + "/v1/connectors/google/callback"


def _client_secret(settings) -> str:
    return settings.google_client_secret.get_secret_value() if settings.google_client_secret else ""


# ---------------------------------------------------------------- OAuth (PKCE)
# flow_id -> {"token_id", "state", "verifier", "created", "status", "account", "error"}
FLOWS: dict[str, dict] = {}
_STATES: dict[str, str] = {}  # state -> flow_id


def _prune() -> None:
    cutoff = now_s() - FLOW_TTL
    for fid, f in list(FLOWS.items()):
        if f["created"] < cutoff:
            _STATES.pop(f["state"], None)
            if f["status"] == "pending":
                f["status"] = "expired"
            if f["created"] < cutoff - 3600:
                FLOWS.pop(fid, None)


def start_auth(settings, token_id: str) -> dict:
    if not settings.google_client_id:
        raise ToolError("Google isn't set up on this server (POM_GOOGLE_CLIENT_ID is empty).")
    _prune()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state, flow_id = secrets.token_urlsafe(24), secrets.token_urlsafe(16)
    FLOWS[flow_id] = {"token_id": token_id, "state": state, "verifier": verifier, "created": now_s(),
                      "status": "pending", "account": None, "error": None}
    _STATES[state] = flow_id
    url = AUTH_URL + "?" + urlencode({
        "client_id": settings.google_client_id, "redirect_uri": redirect_uri(settings), "response_type": "code",
        "scope": SCOPES, "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"})
    return {"auth_url": url, "flow_id": flow_id}


def _secret_from(tokens: dict, old: dict | None = None) -> dict:
    return {"access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token") or (old or {}).get("refresh_token", ""),
            "expires_at": now_s() + int(tokens.get("expires_in", 3600))}


async def finish_auth(app, state: str, code: str | None, error: str | None = None) -> dict:
    """Handle the redirect. Returns the flow; raises ToolError for an unknown or expired state."""
    _prune()
    flow_id = _STATES.pop(state or "", None)
    flow = FLOWS.get(flow_id) if flow_id else None
    if not flow or flow["status"] != "pending":
        raise ToolError("This sign-in link is unknown or has expired. Start again from Bea Hub → Connections.")
    if error or not code:
        flow.update(status="failed", error="Google sign-in was cancelled." if error == "access_denied"
                    else f"Google sign-in failed ({error or 'no code returned'}).")
        return flow
    settings, http = app.state.settings, app.state.http
    try:
        resp = await http.post(TOKEN_URL, timeout=TIMEOUT, data={
            "client_id": settings.google_client_id, "client_secret": _client_secret(settings), "code": code,
            "code_verifier": flow["verifier"], "grant_type": "authorization_code", "redirect_uri": redirect_uri(settings)})
        if resp.status_code != 200:
            flow.update(status="failed", error=f"Google wouldn't finish the sign-in: {api_error(resp)}")
            return flow
        tokens = resp.json()
        info = await http.get(USERINFO_URL, timeout=TIMEOUT, headers={"authorization": f"Bearer {tokens['access_token']}"})
        if info.status_code != 200:
            flow.update(status="failed", error=f"Signed in, but Google wouldn't return the email address ({api_error(info)}).")
            return flow
        email = info.json().get("email") or "Google account"
        old = None
        for r in await store.list_accounts(app.state.db.sessionmaker, flow["token_id"], PROVIDER):
            if r.name == email:
                old = await store.load_secret(settings, r)
        granted = tokens.get("scope", SCOPES)
        meta = {"email": email, "scopes": granted,
                "missing_scopes": [s for s in SCOPES.split() if s.startswith("https") and s not in granted.split()]}
        row = await store.save_account(app.state.db.sessionmaker, settings, flow["token_id"], PROVIDER, email,
                                       _secret_from(tokens, old), meta)
        flow.update(status="connected", account=store.public(row))
    except httpx.TransportError as exc:
        flow.update(status="failed", error=f"Couldn't reach Google ({exc.__class__.__name__}). Try again.")
    except Exception:  # noqa: BLE001
        log.exception("google sign-in failed")
        flow.update(status="failed", error="Something went wrong while finishing the sign-in. Try again.")
    return flow


def flow_status(flow_id: str, token_id: str) -> dict | None:
    _prune()
    flow = FLOWS.get(flow_id)
    if not flow or flow["token_id"] != token_id:
        return None
    out = {"status": flow["status"]}
    if flow["account"]:
        out["account"] = flow["account"]
    if flow["error"]:
        out["error"] = flow["error"]
    return out


async def refresh(http: httpx.AsyncClient, settings, secret: dict) -> dict:
    if not secret.get("refresh_token"):
        raise ToolError(f"Google sign-in has expired and can't be renewed; {RECONNECT}.")
    try:
        resp = await http.post(TOKEN_URL, timeout=TIMEOUT, data={
            "client_id": settings.google_client_id, "client_secret": _client_secret(settings),
            "grant_type": "refresh_token", "refresh_token": secret["refresh_token"]})
    except httpx.TransportError as exc:
        raise ToolError(f"Couldn't reach Google to renew the sign-in ({exc.__class__.__name__}). Try again shortly.") from exc
    if resp.status_code != 200:
        log.warning("google token refresh failed", extra={"status": resp.status_code})
        raise ToolError(f"Google says the token expired and couldn't be refreshed ({api_error(resp)}); {RECONNECT}.")
    return _secret_from(resp.json(), secret)


async def revoke(app, row) -> None:
    """Best effort: tell Google to forget the grant."""
    try:
        secret = await store.load_secret(app.state.settings, row)
        token = secret.get("refresh_token") or secret.get("access_token")
        if token:
            await app.state.http.post(REVOKE_URL, data={"token": token}, timeout=TIMEOUT,
                                      headers={"content-type": "application/x-www-form-urlencoded"})
    except Exception:  # noqa: BLE001
        log.warning("google token revoke failed")


class GoogleApi:
    """Calls Google APIs for one account, refreshing the token when needed (and once on a 401)."""

    def __init__(self, http: httpx.AsyncClient, settings, sessionmaker, row) -> None:
        self.http, self.settings, self.sessionmaker, self.row = http, settings, sessionmaker, row
        self._secret: dict | None = None

    async def _token(self, force: bool = False) -> str:
        if self._secret is None:
            self._secret = await store.load_secret(self.settings, self.row)
        if force or self._secret.get("expires_at", 0) - 60 < now_s():
            self._secret = await refresh(self.http, self.settings, self._secret)
            await store.update_secret(self.sessionmaker, self.settings, self.row.id, self._secret)
        return self._secret["access_token"]

    async def request(self, method: str, url: str, **kw) -> httpx.Response:
        resp = None
        for attempt in range(2):
            token = await self._token(force=attempt == 1)
            try:
                resp = await self.http.request(method, url, headers={"authorization": f"Bearer {token}"}, timeout=TIMEOUT, **kw)
            except httpx.TransportError as exc:
                raise ToolError(f"Couldn't reach Google ({exc.__class__.__name__}). Try again shortly.") from exc
            if resp.status_code != 401:
                break
        if resp.status_code == 401:
            raise ToolError(f"Google rejected the sign-in ({api_error(resp)}); {RECONNECT}.")
        if resp.status_code == 403:
            raise ToolError(f"Google refused this ({api_error(resp)}). The API may not be enabled for the OAuth app, "
                            f"or the permission wasn't granted; {RECONNECT}.")
        if resp.status_code == 404:
            raise ToolError(f"Not found in Google ({api_error(resp)}). Check the id; search again to get a fresh one.")
        if resp.status_code == 429:
            raise ToolError("Google is rate limiting requests. Wait a minute and try again.")
        if resp.status_code >= 400:
            raise ToolError(f"Google returned an error ({resp.status_code}): {api_error(resp)}")
        return resp

    async def json(self, method: str, url: str, **kw) -> dict:
        resp = await self.request(method, url, **kw)
        return resp.json() if resp.content else {}


async def _api(ctx: ToolContext, account: str | None) -> GoogleApi:
    row = await pick_account(ctx, PROVIDER, "Google", account)
    return GoogleApi(ctx.http, ctx.settings, ctx.sessionmaker, row)


async def check_account(app, row) -> tuple[bool, str]:
    g = GoogleApi(app.state.http, app.state.settings, app.state.db.sessionmaker, row)
    try:
        info = await g.json("GET", USERINFO_URL)
    except ToolError as exc:
        return False, str(exc)
    return True, f"Connected as {info.get('email')}."


# ---------------------------------------------------------------- MIME helpers
def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _headers(payload: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in (payload or {}).get("headers", [])}


def _charset(part: dict) -> str:
    ct = _headers(part).get("content-type", "")
    for bit in ct.split(";"):
        bit = bit.strip()
        if bit.lower().startswith("charset="):
            return bit.split("=", 1)[1].strip('"') or "utf-8"
    return "utf-8"


def body_text(payload: dict) -> tuple[str, list[str]]:
    """(text, attachment names) from a Gmail message payload: text/plain preferred, else HTML stripped."""
    plain: list[str] = []
    html: list[str] = []
    attachments: list[str] = []

    def walk(part: dict) -> None:
        mime = (part.get("mimeType") or "").lower()
        body = part.get("body") or {}
        if part.get("filename"):
            attachments.append(part["filename"])
            return
        if mime.startswith("multipart/"):
            for p in part.get("parts") or []:
                walk(p)
            return
        data = body.get("data")
        if not data:
            return
        try:
            text = _b64d(data).decode(_charset(part), errors="replace")
        except LookupError:
            text = _b64d(data).decode("utf-8", errors="replace")
        if mime == "text/plain":
            plain.append(text)
        elif mime == "text/html":
            html.append(text)

    walk(payload or {})
    if plain:
        return "\n".join(plain).strip(), attachments
    return html_to_text("\n".join(html)), attachments


def build_raw(to: list[str], subject: str, body: str, cc: list[str] | None = None, in_reply_to: str = "",
              references: str = "", sender: str = "") -> str:
    """An RFC 2822 message, base64url encoded for the Gmail API."""
    msg = EmailMessage()
    if sender:
        msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = (references + " " + in_reply_to).strip() if in_reply_to not in references else references
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


# ---------------------------------------------------------------- tools
async def _available(ctx: ToolContext) -> bool:
    return bool(await account_names(ctx, PROVIDER))


async def _note(ctx: ToolContext) -> str:
    names = await account_names(ctx, PROVIDER)
    if not names:
        return ""
    return (f"Google (Gmail and Google Calendar) is connected: {', '.join(names)}. Tools take an optional `account` "
            f"(default {names[0]}). Draft emails freely; sending a draft and creating events ask the person first.")


register_group(PROVIDER, available=_available, note=_note)

_ACCOUNT = {"type": "string", "description": "Which connected Google account (email). Default: the first one."}
_EMAILS = {"type": "array", "items": {"type": "string"}}


@tool("gmail_search",
      "Search Gmail with Gmail search syntax (e.g. 'from:priya is:unread newer_than:7d', 'subject:invoice'). "
      "Returns ids for gmail_read.",
      obj({"query": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 25}, "account": _ACCOUNT},
          ["query"]),
      group=PROVIDER, label="Searching Gmail")
async def gmail_search(ctx: ToolContext, args: dict) -> str:
    g = await _api(ctx, args.get("account"))
    data = await g.json("GET", GMAIL + "/messages", params={"q": args["query"], "maxResults": int(args.get("max_results") or 10)})
    ids = [m["id"] for m in data.get("messages", [])]
    if not ids:
        return "No messages found."

    async def meta(mid: str) -> dict:
        m = await g.json("GET", f"{GMAIL}/messages/{quote(mid, safe='')}",
                         params=[("format", "metadata"), ("metadataHeaders", "From"), ("metadataHeaders", "Subject"),
                                 ("metadataHeaders", "Date")])
        h = _headers(m.get("payload"))
        return {"id": mid, "thread_id": m.get("threadId"), "from": h.get("from", ""), "subject": h.get("subject", ""),
                "date": h.get("date", ""), "snippet": m.get("snippet", "")[:200], "unread": "UNREAD" in (m.get("labelIds") or [])}

    items = await asyncio.gather(*(meta(i) for i in ids))
    return untrusted(f"gmail search ({g.row.name})", json.dumps(items, ensure_ascii=False, indent=1))


@tool("gmail_read", "Read one Gmail message in full (body as plain text).",
      obj({"message_id": {"type": "string"}, "account": _ACCOUNT}, ["message_id"]),
      group=PROVIDER, label="Reading an email")
async def gmail_read(ctx: ToolContext, args: dict) -> str:
    g = await _api(ctx, args.get("account"))
    m = await g.json("GET", f"{GMAIL}/messages/{quote(args['message_id'], safe='')}", params={"format": "full"})
    h = _headers(m.get("payload"))
    text, files = body_text(m.get("payload") or {})
    head = [f"From: {h.get('from', '')}", f"To: {h.get('to', '')}", f"Cc: {h['cc']}" if h.get("cc") else "",
            f"Date: {h.get('date', '')}", f"Subject: {h.get('subject', '')}", f"Thread: {m.get('threadId')}",
            f"Attachments: {', '.join(files)}" if files else ""]
    content = "\n".join(x for x in head if x) + "\n\n" + clip(text, 20000)
    return untrusted(f"email {args['message_id'][:24]} ({g.row.name})", content)


@tool("gmail_create_draft",
      "Create a Gmail draft (nothing is sent). With reply_to_message_id the draft joins that thread as a reply. "
      "Send it later with gmail_send_draft.",
      obj({"to": _EMAILS, "subject": {"type": "string"}, "body": {"type": "string", "description": "Plain text"},
           "cc": _EMAILS, "reply_to_message_id": {"type": "string"}, "account": _ACCOUNT}, ["to", "subject", "body"]),
      group=PROVIDER, label="Drafting an email")
async def gmail_create_draft(ctx: ToolContext, args: dict) -> str:
    g = await _api(ctx, args.get("account"))
    to = [a.strip() for a in args.get("to") or [] if a and a.strip()]
    cc = [a.strip() for a in args.get("cc") or [] if a and a.strip()]
    if not to:
        raise ToolError("Give at least one recipient in `to`.")
    subject, thread_id, in_reply_to, refs = args["subject"], None, "", ""
    if args.get("reply_to_message_id"):
        orig = await g.json("GET", f"{GMAIL}/messages/{quote(args['reply_to_message_id'], safe='')}",
                            params=[("format", "metadata"), ("metadataHeaders", "Message-ID"),
                                    ("metadataHeaders", "References"), ("metadataHeaders", "Subject")])
        h = _headers(orig.get("payload"))
        thread_id = orig.get("threadId")
        in_reply_to, refs = h.get("message-id", ""), h.get("references", "")
        if not subject.lower().startswith("re:"):
            subject = "Re: " + (h.get("subject") or subject)
    message: dict = {"raw": build_raw(to, subject, args["body"], cc, in_reply_to, refs)}
    if thread_id:
        message["threadId"] = thread_id
    d = await g.json("POST", GMAIL + "/drafts", json={"message": message})
    mid = (d.get("message") or {}).get("id", "")
    return json.dumps({"draft_id": d.get("id"), "subject": subject,
                       "web_link": f"https://mail.google.com/mail/?authuser={quote(g.row.name)}#drafts?compose={mid}",
                       "note": "Draft saved, not sent. Use gmail_send_draft to send it (the person will be asked)."})


@tool("gmail_send_draft", "Send a Gmail draft. The person is shown the real recipients and subject and must approve.",
      obj({"draft_id": {"type": "string"}, "account": _ACCOUNT}, ["draft_id"]),
      group=PROVIDER, approval="dynamic", label="Sending an email",
      summarize=lambda a: f"Send Gmail draft {a.get('draft_id', '')[:16]}…")
async def gmail_send_draft(ctx: ToolContext, args: dict) -> str:
    g = await _api(ctx, args.get("account"))
    did = quote(args["draft_id"], safe="")
    d = await g.json("GET", f"{GMAIL}/drafts/{did}", params={"format": "full"})
    msg = d.get("message") or {}
    h = _headers(msg.get("payload"))
    to, cc, bcc = h.get("to", ""), h.get("cc", ""), h.get("bcc", "")
    if not (to or cc or bcc):
        raise ToolError("The draft has no recipients. Create a new draft with recipients.")
    subject = h.get("subject") or "(no subject)"
    summary = f'Send email "{subject}" to {to or "(none)"}' + (f" (cc {cc})" if cc else "") + \
              (f" (bcc {bcc})" if bcc else "") + f" from {g.row.name}"
    text, files = body_text(msg.get("payload") or {})
    await ctx.approve_or_raise("gmail_send_draft", summary, {
        "account": g.row.name, "to": to, "cc": cc, "bcc": bcc, "subject": subject,
        "preview": text[:500] or msg.get("snippet", ""), "attachments": files})
    sent = await g.json("POST", GMAIL + "/drafts/send", json={"id": args["draft_id"]})
    return f'Sent "{subject}" to {people([x for x in (to, cc, bcc) if x])} (message id {sent.get("id")}).'


def _gtime(value: str) -> str:
    """Google wants RFC 3339 with an offset for timeMin/timeMax; assume UTC when none is given."""
    v = value.strip()
    if "T" in v and not (v.endswith("Z") or "+" in v[10:] or "-" in v[10:]):
        return v + "Z"
    if "T" not in v:
        return v + "T00:00:00Z"
    return v


@tool("gcal_list_events",
      "List Google Calendar events between two ISO datetimes (include a UTC offset, e.g. 2026-10-14T00:00:00+05:30; "
      "without one, UTC is assumed). Recurring events are expanded.",
      obj({"start": {"type": "string"}, "end": {"type": "string"},
           "calendar_id": {"type": "string", "description": "Default 'primary'"}, "account": _ACCOUNT}, ["start", "end"]),
      group=PROVIDER, label="Checking the calendar")
async def gcal_list_events(ctx: ToolContext, args: dict) -> str:
    g = await _api(ctx, args.get("account"))
    cal = quote(args.get("calendar_id") or "primary", safe="")
    data = await g.json("GET", f"{GCAL}/calendars/{cal}/events", params={
        "timeMin": _gtime(args["start"]), "timeMax": _gtime(args["end"]), "singleEvents": "true",
        "orderBy": "startTime", "maxResults": 50})
    events = []
    for e in data.get("items", []):
        s, en = e.get("start") or {}, e.get("end") or {}
        events.append({"id": e.get("id"), "summary": e.get("summary"), "start": s.get("dateTime") or s.get("date"),
                       "end": en.get("dateTime") or en.get("date"), "location": e.get("location", ""),
                       "organizer": (e.get("organizer") or {}).get("email"),
                       "attendees": [a.get("email") for a in e.get("attendees") or []][:15],
                       "meet": e.get("hangoutLink"), "status": e.get("status"),
                       "description": (e.get("description") or "")[:300], "link": e.get("htmlLink")})
    if not events:
        return "No events in that range."
    return untrusted(f"google calendar ({g.row.name})", json.dumps(events, ensure_ascii=False, indent=1))


def _gwhen(value: str, tz: str | None) -> dict:
    v = value.strip()
    if "T" not in v:
        return {"date": v}
    has_offset = v.endswith("Z") or "+" in v[10:] or "-" in v[10:]
    if not has_offset and not tz:
        raise ToolError("Give a `timezone` (e.g. 'Asia/Kolkata') or include a UTC offset in start/end.")
    out = {"dateTime": v}
    if tz:
        out["timeZone"] = tz
    return out


@tool("gcal_create_event",
      "Create a Google Calendar event. With attendees, invitations are emailed. The person is asked to approve first.",
      obj({"summary": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
           "attendees": _EMAILS, "location": {"type": "string"}, "description": {"type": "string"},
           "timezone": {"type": "string", "description": "IANA zone, e.g. Asia/Kolkata; needed when start/end have no offset"},
           "calendar_id": {"type": "string"}, "account": _ACCOUNT}, ["summary", "start", "end"]),
      group=PROVIDER, approval="dynamic", label="Creating an event",
      summarize=lambda a: f'Create "{a.get("summary")}" {fmt_range(a.get("start", ""), a.get("end", ""))}')
async def gcal_create_event(ctx: ToolContext, args: dict) -> str:
    g = await _api(ctx, args.get("account"))
    tz = args.get("timezone")
    attendees = [a.strip() for a in args.get("attendees") or [] if a and a.strip()]
    event = {"summary": args["summary"], "start": _gwhen(args["start"], tz), "end": _gwhen(args["end"], tz)}
    if attendees:
        event["attendees"] = [{"email": a} for a in attendees]
    if args.get("location"):
        event["location"] = args["location"]
    if args.get("description"):
        event["description"] = args["description"]
    when = fmt_range(args["start"], args["end"]) + (f" ({tz})" if tz else "")
    summary = f'Create "{args["summary"]}" {when}' + (f" with {people(attendees)} (invitations will be sent)" if attendees
                                                      else " (private, no invitations)") + f" in {g.row.name}'s calendar"
    await ctx.approve_or_raise("gcal_create_event", summary, {"account": g.row.name, **event})
    cal = quote(args.get("calendar_id") or "primary", safe="")
    e = await g.json("POST", f"{GCAL}/calendars/{cal}/events", params={"sendUpdates": "all" if attendees else "none"}, json=event)
    return json.dumps({"event_id": e.get("id"), "link": e.get("htmlLink"),
                       "status": "created" + (", invitations sent" if attendees else "")})
