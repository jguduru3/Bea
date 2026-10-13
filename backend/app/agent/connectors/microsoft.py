"""Microsoft 365 (Outlook mail and calendar) through Microsoft Graph.

Sign-in uses the OAuth 2.0 device code flow: the person types a short code at
microsoft.com/devicelogin, so no redirect URI is needed. Tokens are stored encrypted per
device token and refreshed automatically.

This module also holds small helpers shared by the other connectors (account picking,
HTML to text, error messages, date formatting).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import quote

import httpx

from ..registry import ToolContext, ToolError, obj, register_group, tool, untrusted
from . import store

log = logging.getLogger("pom.connectors.microsoft")

PROVIDER = "microsoft"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
RECONNECT = "reconnect Microsoft 365 in Bea Hub → Connections"


# ---------------------------------------------------------------- shared helpers
class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Plain text from an HTML email body (drops scripts/styles, keeps paragraph breaks)."""
    p = _TextExtractor()
    try:
        p.feed(html or "")
        p.close()
    except Exception:  # noqa: BLE001
        return re.sub(r"<[^>]+>", " ", html or "")
    text = "".join(p.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n…[cut, {len(text) - limit} more characters]"


async def pick_account(ctx: ToolContext, provider: str, label: str, account: str | None):
    """The enabled account named `account`, or the first enabled one."""
    rows = await store.list_accounts(ctx.sessionmaker, ctx.token_id, provider, enabled_only=True)
    if not rows:
        raise ToolError(f"No {label} account is connected. Ask the person to connect one in Bea Hub → Connections.")
    if not account:
        return rows[0]
    for r in rows:
        if r.name.lower() == account.strip().lower():
            return r
    raise ToolError(f"No connected {label} account named {account!r}. Connected: {', '.join(r.name for r in rows)}.")


async def account_names(ctx: ToolContext, provider: str) -> list[str]:
    return [r.name for r in await store.list_accounts(ctx.sessionmaker, ctx.token_id, provider, enabled_only=True)]


def api_error(resp: httpx.Response) -> str:
    """A short error description from a JSON or text error response."""
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return (resp.text or "").strip()[:300] or f"HTTP {resp.status_code}"
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err)[:300]
    if isinstance(data, dict):
        return str(data.get("error_description") or err or data.get("message") or data)[:300]
    return str(data)[:300]


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def fmt_range(start: str, end: str) -> str:
    """'Tue 14 Oct 10:00–10:30' (raw strings when they don't parse)."""
    a, b = _parse_dt(start), _parse_dt(end)
    if not a or not b:
        return f"{start} – {end}"
    left = a.strftime("%a %d %b %H:%M")
    right = b.strftime("%H:%M") if a.date() == b.date() else b.strftime("%a %d %b %H:%M")
    return f"{left}–{right}"


def people(addrs: list[str] | None) -> str:
    addrs = [a for a in (addrs or []) if a]
    if len(addrs) > 6:
        return ", ".join(addrs[:6]) + f" and {len(addrs) - 6} more"
    return ", ".join(addrs)


def now_s() -> float:
    return time.time()


# ---------------------------------------------------------------- OAuth
def _login_base(settings) -> str:
    return f"https://login.microsoftonline.com/{settings.microsoft_tenant or 'common'}/oauth2/v2.0"


def _secret_from(tokens: dict, old: dict | None = None) -> dict:
    return {"access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token") or (old or {}).get("refresh_token", ""),
            "expires_at": now_s() + int(tokens.get("expires_in", 3600))}


async def refresh(http: httpx.AsyncClient, settings, secret: dict) -> dict:
    if not secret.get("refresh_token"):
        raise ToolError(f"Microsoft sign-in has expired and can't be renewed; {RECONNECT}.")
    try:
        resp = await http.post(_login_base(settings) + "/token", timeout=TIMEOUT, data={
            "client_id": settings.microsoft_client_id, "grant_type": "refresh_token",
            "refresh_token": secret["refresh_token"], "scope": SCOPES})
    except httpx.TransportError as exc:
        raise ToolError(f"Couldn't reach Microsoft to renew the sign-in ({exc.__class__.__name__}). Try again shortly.") from exc
    if resp.status_code != 200:
        log.warning("microsoft token refresh failed", extra={"status": resp.status_code})
        raise ToolError(f"Microsoft says the token expired and couldn't be refreshed ({api_error(resp)}); {RECONNECT}.")
    return _secret_from(resp.json(), secret)


class Graph:
    """Calls Graph for one account, refreshing the token when needed (and once on a 401)."""

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

    async def request(self, method: str, path: str, *, headers: dict | None = None, **kw) -> httpx.Response:
        url = path if path.startswith("https://") else GRAPH + path
        resp = None
        for attempt in range(2):
            token = await self._token(force=attempt == 1)
            h = {"authorization": f"Bearer {token}", **(headers or {})}
            try:
                resp = await self.http.request(method, url, headers=h, timeout=TIMEOUT, **kw)
            except httpx.TransportError as exc:
                raise ToolError(f"Couldn't reach Microsoft 365 ({exc.__class__.__name__}). Try again shortly.") from exc
            if resp.status_code != 401:
                break
        if resp.status_code == 401:
            raise ToolError(f"Microsoft rejected the sign-in ({api_error(resp)}); {RECONNECT}.")
        if resp.status_code == 403:
            raise ToolError(f"Microsoft 365 refused this ({api_error(resp)}). The account may lack the permission; {RECONNECT}.")
        if resp.status_code == 404:
            raise ToolError(f"Not found in Microsoft 365 ({api_error(resp)}). Check the id; search again to get a fresh one.")
        if resp.status_code == 429:
            raise ToolError("Microsoft 365 is rate limiting requests. Wait a minute and try again.")
        if resp.status_code >= 400:
            raise ToolError(f"Microsoft 365 returned an error ({resp.status_code}): {api_error(resp)}")
        return resp

    async def json(self, method: str, path: str, **kw) -> dict:
        resp = await self.request(method, path, **kw)
        return resp.json() if resp.content else {}


async def _graph(ctx: ToolContext, account: str | None) -> Graph:
    row = await pick_account(ctx, PROVIDER, "Microsoft 365", account)
    return Graph(ctx.http, ctx.settings, ctx.sessionmaker, row)


# ---------------------------------------------------------------- device code flow
# flow_id -> {"token_id", "status": pending|connected|failed|expired, "account", "error", "created"}
FLOWS: dict[str, dict] = {}
_TASKS: set[asyncio.Task] = set()
_sleep = asyncio.sleep  # tests replace this
FLOW_TTL = 3600


def _prune() -> None:
    cutoff = now_s() - FLOW_TTL
    for k in [k for k, v in FLOWS.items() if v["created"] < cutoff]:
        FLOWS.pop(k, None)


async def save_from_tokens(app, token_id: str, tokens: dict, http: httpx.AsyncClient | None = None):
    """Look up the signed-in user and store the account."""
    settings = app.state.settings
    http = http or app.state.http
    secret = _secret_from(tokens)
    resp = await http.get(GRAPH + "/me", timeout=TIMEOUT, params={"$select": "displayName,mail,userPrincipalName"},
                          headers={"authorization": f"Bearer {secret['access_token']}"})
    if resp.status_code != 200:
        raise ToolError(f"Signed in, but Microsoft wouldn't return the profile ({api_error(resp)}).")
    me = resp.json()
    name = me.get("mail") or me.get("userPrincipalName") or "Microsoft account"
    meta = {"display_name": me.get("displayName") or "", "email": name, "scopes": tokens.get("scope", SCOPES)}
    return await store.save_account(app.state.db.sessionmaker, settings, token_id, PROVIDER, name, secret, meta)


async def start_device_flow(app, token_id: str) -> dict:
    settings = app.state.settings
    if not settings.microsoft_client_id:
        raise ToolError("Microsoft 365 isn't set up on this server (POM_MICROSOFT_CLIENT_ID is empty).")
    try:
        resp = await app.state.http.post(_login_base(settings) + "/devicecode", timeout=TIMEOUT,
                                         data={"client_id": settings.microsoft_client_id, "scope": SCOPES})
    except httpx.TransportError as exc:
        raise ToolError(f"Couldn't reach Microsoft ({exc.__class__.__name__}). Check the internet connection.") from exc
    if resp.status_code != 200:
        raise ToolError(f"Microsoft refused to start sign-in: {api_error(resp)}")
    data = resp.json()
    _prune()
    flow_id = secrets.token_urlsafe(16)
    FLOWS[flow_id] = {"token_id": token_id, "status": "pending", "account": None, "error": None, "created": now_s()}
    task = asyncio.create_task(_poll(app, flow_id, data))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"user_code": data["user_code"], "verification_uri": data.get("verification_uri", "https://microsoft.com/devicelogin"),
            "expires_in": int(data.get("expires_in", 900)), "message": data.get("message", ""), "flow_id": flow_id}


async def _poll(app, flow_id: str, device: dict) -> None:
    flow = FLOWS[flow_id]
    settings = app.state.settings
    interval = max(1, int(device.get("interval", 5)))
    deadline = now_s() + int(device.get("expires_in", 900))
    try:
        while True:
            await _sleep(interval)
            if now_s() > deadline:
                flow.update(status="expired", error="The code expired before sign-in finished. Start again.")
                return
            try:
                resp = await app.state.http.post(_login_base(settings) + "/token", timeout=TIMEOUT, data={
                    "client_id": settings.microsoft_client_id, "device_code": device["device_code"],
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            except httpx.TransportError:
                continue  # transient; keep polling until the code expires
            if resp.status_code == 200:
                row = await save_from_tokens(app, flow["token_id"], resp.json())
                flow.update(status="connected", account=store.public(row))
                return
            err = ""
            try:
                err = resp.json().get("error", "")
            except Exception:  # noqa: BLE001
                pass
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            if err in ("expired_token", "code_expired"):
                flow.update(status="expired", error="The code expired before sign-in finished. Start again.")
            elif err in ("authorization_declined", "access_denied"):
                flow.update(status="failed", error="Sign-in was declined.")
            else:
                flow.update(status="failed", error=f"Microsoft sign-in failed: {api_error(resp)}")
            return
    except ToolError as exc:
        flow.update(status="failed", error=str(exc))
    except Exception:  # noqa: BLE001
        log.exception("microsoft device flow failed")
        flow.update(status="failed", error="Something went wrong while finishing the sign-in. Try again.")


def flow_status(flow_id: str, token_id: str) -> dict | None:
    flow = FLOWS.get(flow_id)
    if not flow or flow["token_id"] != token_id:
        return None
    out = {"status": flow["status"]}
    if flow["account"]:
        out["account"] = flow["account"]
    if flow["error"]:
        out["error"] = flow["error"]
    return out


async def check_account(app, row) -> tuple[bool, str]:
    g = Graph(app.state.http, app.state.settings, app.state.db.sessionmaker, row)
    try:
        me = await g.json("GET", "/me", params={"$select": "mail,userPrincipalName"})
    except ToolError as exc:
        return False, str(exc)
    return True, f"Connected as {me.get('mail') or me.get('userPrincipalName')}."


# ---------------------------------------------------------------- tools
async def _available(ctx: ToolContext) -> bool:
    return bool(await account_names(ctx, PROVIDER))


async def _note(ctx: ToolContext) -> str:
    names = await account_names(ctx, PROVIDER)
    if not names:
        return ""
    return (f"Microsoft 365 (Outlook mail and calendar) is connected: {', '.join(names)}. "
            f"Tools take an optional `account` (default {names[0]}). Draft emails freely; sending a draft and "
            "creating events ask the person first.")


register_group(PROVIDER, available=_available, note=_note)

_ACCOUNT = {"type": "string", "description": "Which connected Microsoft account (email). Default: the first one."}
_EMAILS = {"type": "array", "items": {"type": "string"}}


def _addr(r: dict) -> str:
    e = (r or {}).get("emailAddress") or {}
    name, addr = e.get("name") or "", e.get("address") or ""
    return f"{name} <{addr}>" if name and name != addr else addr


def _recips(addrs: list[str] | None) -> list[dict]:
    return [{"emailAddress": {"address": a.strip()}} for a in (addrs or []) if a and a.strip()]


@tool("outlook_search_mail",
      "Search or list Outlook email. With no query, lists the newest messages in the folder. `query` uses Outlook "
      "search (words, from:name, subject:word). Returns ids for outlook_read_mail.",
      obj({"query": {"type": "string"},
           "folder": {"type": "string", "description": "inbox (default), sentitems, drafts, archive, deleteditems, junkemail, or a folder id"},
           "unread_only": {"type": "boolean"},
           "top": {"type": "integer", "minimum": 1, "maximum": 50},
           "account": _ACCOUNT}),
      group=PROVIDER, label="Searching Outlook mail")
async def outlook_search_mail(ctx: ToolContext, args: dict) -> str:
    g = await _graph(ctx, args.get("account"))
    top = int(args.get("top") or 10)
    folder = quote(args.get("folder") or "inbox", safe="")
    params = {"$top": str(top), "$select": "id,from,subject,receivedDateTime,bodyPreview,isRead"}
    q = (args.get("query") or "").strip()
    if q:
        params["$search"] = '"' + q.replace('"', "'") + '"'
        if args.get("unread_only"):
            params["$top"] = str(min(50, top * 3))
    else:
        params["$orderby"] = "receivedDateTime desc"
        if args.get("unread_only"):
            params["$filter"] = "isRead eq false"
    data = await g.json("GET", f"/me/mailFolders/{folder}/messages", params=params)
    items = [{"id": m.get("id"), "from": _addr(m.get("from")), "subject": m.get("subject") or "",
              "received": m.get("receivedDateTime"), "preview": (m.get("bodyPreview") or "")[:200], "isRead": m.get("isRead")}
             for m in data.get("value", [])]
    if q and args.get("unread_only"):
        items = [m for m in items if not m["isRead"]]
    items = items[:top]
    if not items:
        return "No messages found."
    return untrusted(f"outlook search ({g.row.name})", json.dumps(items, ensure_ascii=False, indent=1))


@tool("outlook_read_mail", "Read one Outlook email in full (body as plain text).",
      obj({"message_id": {"type": "string"}, "account": _ACCOUNT}, ["message_id"]),
      group=PROVIDER, label="Reading an email")
async def outlook_read_mail(ctx: ToolContext, args: dict) -> str:
    g = await _graph(ctx, args.get("account"))
    m = await g.json("GET", f"/me/messages/{quote(args['message_id'], safe='')}",
                     params={"$select": "subject,from,toRecipients,ccRecipients,receivedDateTime,body,hasAttachments,conversationId,webLink,isDraft"},
                     headers={"Prefer": 'outlook.body-content-type="text"'})
    body = (m.get("body") or {})
    text = body.get("content") or ""
    if (body.get("contentType") or "").lower() == "html":
        text = html_to_text(text)
    head = [f"From: {_addr(m.get('from'))}",
            f"To: {', '.join(_addr(r) for r in m.get('toRecipients') or [])}",
            f"Cc: {', '.join(_addr(r) for r in m.get('ccRecipients') or [])}" if m.get("ccRecipients") else "",
            f"Date: {m.get('receivedDateTime')}", f"Subject: {m.get('subject') or ''}",
            "Has attachments: yes" if m.get("hasAttachments") else "",
            f"Link: {m.get('webLink')}" if m.get("webLink") else ""]
    content = "\n".join(h for h in head if h) + "\n\n" + clip(text, 20000)
    return untrusted(f"email {args['message_id'][:24]} ({g.row.name})", content)


@tool("outlook_create_draft",
      "Create an Outlook email draft (nothing is sent). With reply_to_message_id it drafts a reply in that thread "
      "(your body goes above the quoted message). Send it later with outlook_send_draft.",
      obj({"to": _EMAILS, "subject": {"type": "string"}, "body": {"type": "string", "description": "Plain text"},
           "cc": _EMAILS, "reply_to_message_id": {"type": "string"}, "account": _ACCOUNT}, ["to", "subject", "body"]),
      group=PROVIDER, label="Drafting an email")
async def outlook_create_draft(ctx: ToolContext, args: dict) -> str:
    g = await _graph(ctx, args.get("account"))
    to, cc = _recips(args.get("to")), _recips(args.get("cc"))
    if args.get("reply_to_message_id"):
        msg: dict = {}
        if to:
            msg["toRecipients"] = to
        if cc:
            msg["ccRecipients"] = cc
        d = await g.json("POST", f"/me/messages/{quote(args['reply_to_message_id'], safe='')}/createReply",
                         json={"message": msg, "comment": args["body"]})
    else:
        if not to:
            raise ToolError("Give at least one recipient in `to`.")
        d = await g.json("POST", "/me/messages", json={
            "subject": args["subject"], "body": {"contentType": "Text", "content": args["body"]},
            "toRecipients": to, "ccRecipients": cc})
    return json.dumps({"draft_id": d.get("id"), "subject": d.get("subject"), "web_link": d.get("webLink"),
                       "note": "Draft saved, not sent. Use outlook_send_draft to send it (the person will be asked)."})


@tool("outlook_send_draft", "Send an Outlook draft. The person is shown the real recipients and subject and must approve.",
      obj({"draft_id": {"type": "string"}, "account": _ACCOUNT}, ["draft_id"]),
      group=PROVIDER, approval="dynamic", label="Sending an email",
      summarize=lambda a: f"Send Outlook draft {a.get('draft_id', '')[:16]}…")
async def outlook_send_draft(ctx: ToolContext, args: dict) -> str:
    g = await _graph(ctx, args.get("account"))
    mid = quote(args["draft_id"], safe="")
    d = await g.json("GET", f"/me/messages/{mid}",
                     params={"$select": "subject,toRecipients,ccRecipients,bccRecipients,isDraft,bodyPreview"})
    if not d.get("isDraft", True):
        raise ToolError("That message isn't a draft (it was already sent or received).")
    to = [_addr(r) for r in d.get("toRecipients") or []]
    cc = [_addr(r) for r in d.get("ccRecipients") or []]
    bcc = [_addr(r) for r in d.get("bccRecipients") or []]
    if not (to or cc or bcc):
        raise ToolError("The draft has no recipients. Create a new draft with recipients.")
    subject = d.get("subject") or "(no subject)"
    summary = f'Send email "{subject}" to {people(to) or "(none)"}' + (f" (cc {people(cc)})" if cc else "") + \
              (f" (bcc {people(bcc)})" if bcc else "") + f" from {g.row.name}"
    await ctx.approve_or_raise("outlook_send_draft", summary, {
        "account": g.row.name, "to": to, "cc": cc, "bcc": bcc, "subject": subject, "preview": (d.get("bodyPreview") or "")[:500]})
    await g.request("POST", f"/me/messages/{mid}/send")
    return f'Sent "{subject}" to {people(to + cc + bcc)}.'


@tool("outlook_list_events",
      "List Outlook calendar events between two ISO datetimes (include a UTC offset, e.g. 2026-10-14T00:00:00+05:30; "
      "without one, UTC is assumed). Recurring events are expanded.",
      obj({"start": {"type": "string"}, "end": {"type": "string"},
           "timezone": {"type": "string", "description": "Return times in this zone, e.g. 'India Standard Time' or 'Asia/Kolkata'"},
           "account": _ACCOUNT}, ["start", "end"]),
      group=PROVIDER, label="Checking the calendar")
async def outlook_list_events(ctx: ToolContext, args: dict) -> str:
    g = await _graph(ctx, args.get("account"))
    headers = {"Prefer": f'outlook.timezone="{args["timezone"]}"'} if args.get("timezone") else None
    data = await g.json("GET", "/me/calendarView", headers=headers, params={
        "startDateTime": args["start"], "endDateTime": args["end"], "$top": "50", "$orderby": "start/dateTime",
        "$select": "id,subject,start,end,location,attendees,organizer,isAllDay,isOnlineMeeting,onlineMeeting,webLink,bodyPreview,showAs"})
    events = []
    for e in data.get("value", []):
        events.append({
            "id": e.get("id"), "subject": e.get("subject"),
            "start": f"{(e.get('start') or {}).get('dateTime')} {(e.get('start') or {}).get('timeZone', '')}".strip(),
            "end": f"{(e.get('end') or {}).get('dateTime')} {(e.get('end') or {}).get('timeZone', '')}".strip(),
            "all_day": e.get("isAllDay"), "location": (e.get("location") or {}).get("displayName") or "",
            "organizer": _addr(e.get("organizer")), "attendees": [_addr(a) for a in e.get("attendees") or []][:15],
            "show_as": e.get("showAs"), "online_join": ((e.get("onlineMeeting") or {}).get("joinUrl")),
            "preview": (e.get("bodyPreview") or "")[:200]})
    if not events:
        return "No events in that range."
    return untrusted(f"outlook calendar ({g.row.name})", json.dumps(events, ensure_ascii=False, indent=1))


def _when(value: str, tz: str | None) -> dict:
    """Graph wants a local dateTime plus a timeZone name."""
    dt = _parse_dt(value)
    if dt is None:
        raise ToolError(f"Couldn't read the date/time {value!r}. Use ISO format, e.g. 2026-10-14T10:00:00.")
    if dt.tzinfo is not None and not tz:
        offset = dt.utcoffset()
        dt = (dt - offset).replace(tzinfo=None)
        return {"dateTime": dt.isoformat(timespec="seconds"), "timeZone": "UTC"}
    if dt.tzinfo is None and not tz:
        raise ToolError("Give a `timezone` (e.g. 'Asia/Kolkata') or include a UTC offset in start/end.")
    return {"dateTime": dt.replace(tzinfo=None).isoformat(timespec="seconds"), "timeZone": tz}


@tool("outlook_create_event",
      "Create an Outlook calendar event. With attendees, invitations are sent. The person is asked to approve first.",
      obj({"subject": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
           "attendees": _EMAILS, "location": {"type": "string"}, "body": {"type": "string"},
           "timezone": {"type": "string", "description": "IANA or Windows zone name; needed when start/end have no offset"},
           "online_meeting": {"type": "boolean", "description": "Add a Teams link"},
           "account": _ACCOUNT}, ["subject", "start", "end"]),
      group=PROVIDER, approval="dynamic", label="Creating an event",
      summarize=lambda a: f'Create "{a.get("subject")}" {fmt_range(a.get("start", ""), a.get("end", ""))}')
async def outlook_create_event(ctx: ToolContext, args: dict) -> str:
    g = await _graph(ctx, args.get("account"))
    tz = args.get("timezone")
    start, end = _when(args["start"], tz), _when(args["end"], tz)
    attendees = [a.strip() for a in args.get("attendees") or [] if a and a.strip()]
    event = {"subject": args["subject"], "start": start, "end": end,
             "attendees": [{"emailAddress": {"address": a}, "type": "required"} for a in attendees]}
    if args.get("location"):
        event["location"] = {"displayName": args["location"]}
    if args.get("body"):
        event["body"] = {"contentType": "Text", "content": args["body"]}
    if args.get("online_meeting"):
        event.update(isOnlineMeeting=True, onlineMeetingProvider="teamsForBusiness")
    when = fmt_range(args["start"], args["end"]) + (f" ({tz})" if tz else "")
    summary = f'Create "{args["subject"]}" {when}' + (f" with {people(attendees)} (invitations will be sent)" if attendees
                                                      else " (private, no invitations)") + f" in {g.row.name}'s calendar"
    await ctx.approve_or_raise("outlook_create_event", summary, {"account": g.row.name, **event})
    e = await g.json("POST", "/me/events", json=event)
    return json.dumps({"event_id": e.get("id"), "web_link": e.get("webLink"),
                       "status": "created" + (", invitations sent" if attendees else "")})


@tool("outlook_find_free_time",
      "Suggest meeting times when the person (and optional attendees in the same organization) are free, "
      "within a window of ISO datetimes.",
      obj({"start": {"type": "string"}, "end": {"type": "string"},
           "duration_minutes": {"type": "integer", "minimum": 5, "maximum": 1440},
           "attendees": _EMAILS, "timezone": {"type": "string"}, "account": _ACCOUNT}, ["start", "end", "duration_minutes"]),
      group=PROVIDER, label="Finding free time")
async def outlook_find_free_time(ctx: ToolContext, args: dict) -> str:
    g = await _graph(ctx, args.get("account"))
    tz = args.get("timezone")
    start, end = _when(args["start"], tz), _when(args["end"], tz)
    body = {"attendees": [{"emailAddress": {"address": a}, "type": "required"} for a in args.get("attendees") or []],
            "timeConstraint": {"activityDomain": "work", "timeSlots": [{"start": start, "end": end}]},
            "meetingDuration": f"PT{int(args['duration_minutes'])}M", "maxCandidates": 10,
            "isOrganizerOptional": False, "returnSuggestionReasons": True, "minimumAttendeePercentage": 100}
    headers = {"Prefer": f'outlook.timezone="{tz}"'} if tz else None
    data = await g.json("POST", "/me/findMeetingTimes", json=body, headers=headers)
    slots = [{"start": (s.get("meetingTimeSlot") or {}).get("start", {}).get("dateTime"),
              "end": (s.get("meetingTimeSlot") or {}).get("end", {}).get("dateTime"),
              "time_zone": (s.get("meetingTimeSlot") or {}).get("start", {}).get("timeZone"),
              "confidence": s.get("confidence")}
             for s in data.get("meetingTimeSuggestions", [])]
    if not slots:
        return f"No free slot found ({data.get('emptySuggestionsReason') or 'everyone is busy'})."
    return json.dumps(slots, indent=1)
