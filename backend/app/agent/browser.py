"""Bea's own web browser, operated with browser_* tools.

One persistent Chromium profile per device token, launched on first use and closed after
15 idle minutes. Every page is read through a snapshot that numbers the interactive
elements; clicks and typing refer to those numbers. Anything that submits, sends, buys or
signs in stops for the person's approval, and password / payment / OTP / ID fields are
refused outright: the person types those themselves.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .registry import ToolContext, ToolError, obj, register_group, tool, untrusted

log = logging.getLogger("pom.agent.browser")

IDLE_SECONDS = 15 * 60
VIEWPORT = {"width": 1280, "height": 900}
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
DEFAULT_TIMEOUT_MS = 20_000
MAX_TEXT = 12_000
MAX_ELEMENTS = 150

# ---------------------------------------------------------------------------------------
# URL safety


def ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified or not addr.is_global)


async def url_block_reason(url: str, allow_private: bool = False, cache: dict | None = None) -> str | None:
    """Why this URL must not be opened, or None when it is fine."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "it is not a valid URL"
    if parts.scheme not in ("http", "https"):
        return f"only http and https addresses can be opened, not {parts.scheme or 'this'}:"
    host = (parts.hostname or "").strip(".").lower()
    if not host:
        return "the address has no host"
    if allow_private:
        return None
    if host == "localhost" or host.endswith(".localhost"):
        return "it points at this computer (localhost)"
    if cache is not None and host in cache:
        return cache[host]
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except (socket.gaierror, UnicodeError, OSError):
            return f"the host {host} could not be found"
        addrs = [info[4][0] for info in infos]
    reason = None
    if not addrs or not all(ip_is_public(a) for a in addrs):
        reason = f"{host} is a private or local network address"
    if cache is not None:
        cache[host] = reason
    return reason


# ---------------------------------------------------------------------------------------
# Element classification (pure functions over the element description built in the page)

_RISKY_TEXT = re.compile(
    r"\b(submit|send|pay|payment|buy|purchase|order|checkout|check\s*out|book|booking|reserve|confirm|"
    r"delete|remove|unsubscribe|sign[\s_-]?in|log[\s_-]?in|sign[\s_-]?up|register|subscribe|post|publish|"
    r"share|transfer|donate|apply|accept|agree|upload)\b"
    r"|\bcancel\s+(my\s+|your\s+)?(subscription|membership|plan|order|booking)\b",
    re.I)
# Links: only the words that clearly mean "this URL acts" (a link to /blog/post/1 is fine).
_RISKY_HREF = re.compile(
    r"\b(pay|payment|buy|purchase|checkout|delete|unsubscribe|sign[\s_-]?in|log[\s_-]?in|sign[\s_-]?up|"
    r"register|subscribe|transfer|donate|upload|logout|log[\s_-]?out|sign[\s_-]?out)\b", re.I)

_SENSITIVE_AUTOCOMPLETE = re.compile(r"\b(cc-[a-z-]+|one-time-code|current-password|new-password)\b", re.I)
_SENSITIVE_WORDS = [
    (re.compile(r"\bpass\s*word\b|\bpasswd\b|\bpwd\b|\bpasscode\b", re.I), "a password"),
    (re.compile(r"\b(credit|debit)\s*card\b|\bcard\s*(number|no|num)\b|\bcc\s*(number|num|no|csc|cvv|exp)\b"
                r"|\bccnum\b|\bcardnumber\b|\bcard\s*holder\b", re.I), "payment card details"),
    (re.compile(r"\bcv[vc]2?\b|\bcsc\b|\bsecurity\s*code\b|\bexpiry\b|\bexp\s*(date|month|year)\b", re.I),
     "payment card details"),
    (re.compile(r"\botp\b|\bone\s*time\s*(password|code|pin)\b|\bverification\s*code\b|\bupi\s*pin\b"
                r"|\bpin\b(?!\s*code)|\bmpin\b|\btpin\b", re.I), "a one-time code or PIN"),
    (re.compile(r"\bssn\b|\bsocial\s*security\b", re.I), "a social security number"),
    (re.compile(r"\baadh?aa?r\b|\bpan\b(\s*(card|number|no))?", re.I), "an Aadhaar or PAN number"),
    (re.compile(r"\b(bank\s*)?account\s*(number|no|num)\b|\bacct\s*(number|no|num)?\b|\biban\b"
                r"|\brouting\s*number\b|\bsort\s*code\b", re.I), "bank account details"),
]


def _words(s: str) -> str:
    """'cardNumber' / 'card_number' / 'card-number' -> 'card number'."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s or "")
    return re.sub(r"[_\-\[\].:]+", " ", s).lower()


def click_risk(el: dict) -> str | None:
    """Why clicking this element needs approval, or None for harmless clicks."""
    if el.get("is_submit"):
        return "it submits a form"
    for key in ("name", "text", "aria", "label", "value_attr", "title"):
        m = _RISKY_TEXT.search(_words(str(el.get(key) or ""))[:300])
        if m:
            return f'it looks like a "{m.group(0).strip()}" action'
    href = str(el.get("href") or "")
    if href and not href.startswith("#"):
        m = _RISKY_HREF.search(_words(href))
        if m:
            return f'its link looks like a "{m.group(0).strip()}" action'
    return None


def sensitive_field(el: dict) -> str | None:
    """What kind of private data this field is for, or None for an ordinary field."""
    if str(el.get("type") or "").lower() == "password":
        return "a password"
    auto = str(el.get("autocomplete") or "")
    if _SENSITIVE_AUTOCOMPLETE.search(auto):
        return "a password" if "password" in auto else (
            "a one-time code" if "one-time" in auto else "payment card details")
    hay = " | ".join(_words(str(el.get(k) or "")) for k in
                     ("field_name", "field_id", "placeholder", "label", "aria", "name", "autocomplete"))
    for pattern, what in _SENSITIVE_WORDS:
        if pattern.search(hay):
            return what
    return None


def is_search_field(el: dict) -> bool:
    if el.get("type") == "search" or el.get("role") in ("searchbox", "combobox") or el.get("form_search"):
        return True
    hay = " ".join(_words(str(el.get(k) or "")) for k in ("field_name", "field_id", "placeholder", "aria", "name"))
    return bool(re.search(r"\b(search|query|q|find)\b", hay))


TEXT_INPUT_TYPES = {"text", "email", "search", "tel", "url", "number", "date", "datetime-local", "month",
                    "week", "time", ""}

# ---------------------------------------------------------------------------------------
# In-page JavaScript

_DESCRIBE_JS = r"""(el) => {
  const clip = (s, n) => (s || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const tag = el.tagName.toLowerCase();
  const role = (el.getAttribute('role') || '').toLowerCase();
  const type = tag === 'input' ? (el.type || 'text').toLowerCase() : (el.getAttribute('type') || '').toLowerCase();
  let label = '';
  if (el.labels && el.labels.length) label = Array.from(el.labels).map(l => l.innerText).join(' ');
  const lb = el.getAttribute('aria-labelledby');
  if (lb) label += ' ' + lb.split(/\s+/).map(id => { const n = document.getElementById(id); return n ? n.innerText : ''; }).join(' ');
  const aria = el.getAttribute('aria-label') || '';
  const text = ['input', 'select', 'textarea'].includes(tag) ? '' : (el.innerText || el.textContent || '');
  const img = !clip(text, 5) && el.querySelector ? el.querySelector('img[alt]') : null;
  const inputButton = tag === 'input' && ['submit', 'button', 'reset', 'image'].includes(type);
  const valueAttr = inputButton ? (el.value || (type === 'submit' ? 'Submit' : '')) : '';
  const name = clip(aria || label || text || valueAttr || el.getAttribute('title') || (img && img.alt) ||
                    el.getAttribute('alt') || el.getAttribute('placeholder') || el.getAttribute('name'), 100);
  const form = el.form || el.closest('form');
  const isSubmit = !!el.form && ((tag === 'button' && el.type === 'submit') ||
                                 (tag === 'input' && (type === 'submit' || type === 'image')));
  let value = '';
  if ((tag === 'input' && !inputButton && type !== 'password') || tag === 'textarea') value = el.value || '';
  else if (el.isContentEditable) value = el.innerText || '';
  let options = null, selected = '';
  if (tag === 'select') {
    options = Array.from(el.options).slice(0, 15).map(o => clip(o.label || o.text, 40));
    selected = el.selectedOptions.length ? clip(el.selectedOptions[0].label, 40) : '';
  }
  return {
    tag, type, role, name,
    text: clip(text, 300), aria: clip(aria, 200), label: clip(label, 200), value_attr: clip(valueAttr, 100),
    title: clip(el.getAttribute('title'), 100), href: tag === 'a' ? (el.getAttribute('href') || '') : '',
    placeholder: clip(el.getAttribute('placeholder'), 100), field_name: el.getAttribute('name') || '',
    field_id: el.id || '', autocomplete: el.getAttribute('autocomplete') || '',
    is_submit: isSubmit, in_form: !!form,
    form_search: !!form && (form.getAttribute('role') === 'search' || !!form.closest('[role=search]')),
    contenteditable: !!el.isContentEditable, disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
    checked: (type === 'checkbox' || type === 'radio') ? !!el.checked : null,
    value: clip(value, 60), options, selected,
  };
}"""

_SNAPSHOT_JS = r"""(maxEls) => {
  const describe = __DESCRIBE__;
  document.querySelectorAll('[data-bea-id]').forEach(e => e.removeAttribute('data-bea-id'));
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=link], ' +
    '[role=checkbox], [role=radio], [role=tab], [role=menuitem], [role=option], [role=switch], [role=searchbox], ' +
    '[role=textbox], [role=combobox], [contenteditable=""], [contenteditable=true]';
  const vh = window.innerHeight;
  const out = [];
  let more = 0, id = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || r.bottom < -vh || r.top > vh * 3) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    if (out.length >= maxEls) { more++; continue; }
    id += 1;
    el.setAttribute('data-bea-id', String(id));
    const d = describe(el);
    d.id = id;
    d.in_view = r.bottom > 0 && r.top < vh;
    out.push(d);
  }
  const body = document.body ? (document.body.innerText || '') : '';
  let text = body;
  const main = document.querySelector('main, [role=main], article');
  if (main) {
    const mt = main.innerText || '';
    if (mt.trim().length > 200) {
      const rest = body.replace(mt, '').trim();
      text = rest ? mt + '\n\n[Rest of the page]\n' + rest : mt;
    }
  }
  return {title: document.title || '', url: location.href, text, elements: out, more};
}""".replace("__DESCRIBE__", _DESCRIBE_JS)

# ---------------------------------------------------------------------------------------
# Session manager


@dataclass
class _Session:
    token_id: str
    context: object  # playwright BrowserContext
    page: object  # playwright Page
    allow_private: bool
    last_used: float = field(default_factory=time.monotonic)
    known_ids: set[int] = field(default_factory=set)
    dns_cache: dict = field(default_factory=dict)
    blocked: list[str] = field(default_factory=list)


_playwright = None
_pw_lock: asyncio.Lock | None = None
_sessions: dict[str, _Session] = {}
_locks: dict[str, asyncio.Lock] = {}
_reaper: asyncio.Task | None = None


def _lock_for(token_id: str) -> asyncio.Lock:
    lock = _locks.get(token_id)
    if lock is None:
        lock = _locks[token_id] = asyncio.Lock()
    return lock


async def _get_playwright():
    global _playwright, _pw_lock
    if _pw_lock is None:
        _pw_lock = asyncio.Lock()
    async with _pw_lock:
        if _playwright is None:
            from playwright.async_api import async_playwright
            _playwright = await async_playwright().start()
    return _playwright


async def _launch(ctx: ToolContext) -> _Session:
    settings = ctx.settings
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", ctx.token_id)[:80] or "default"
    profile = Path(settings.data_dir) / "browser" / safe_id
    profile.mkdir(parents=True, exist_ok=True)
    try:
        pw = await _get_playwright()
        context = await pw.chromium.launch_persistent_context(
            str(profile), headless=settings.browser_headless, viewport=VIEWPORT, user_agent=USER_AGENT,
            accept_downloads=False, service_workers="block", locale="en-IN")
    except Exception as exc:  # noqa: BLE001
        log.warning("browser launch failed: %s", exc)
        raise ToolError(f"The browser could not start ({str(exc).splitlines()[0][:200]}).") from exc
    context.set_default_timeout(DEFAULT_TIMEOUT_MS)
    page = context.pages[0] if context.pages else await context.new_page()
    session = _Session(ctx.token_id, context, page, settings.browser_allow_private_networks)

    async def guard(route):
        req = route.request
        scheme = urlsplit(req.url).scheme
        if scheme not in ("http", "https") and req.resource_type != "document":
            await route.continue_()
            return
        reason = await url_block_reason(req.url, session.allow_private, session.dns_cache)
        if reason:
            if req.is_navigation_request():
                session.blocked.append(f"{req.url[:200]} ({reason})")
            await route.abort("blockedbyclient")
        else:
            await route.continue_()

    def on_page(p):  # a link that opens a new tab: follow it
        session.page = p

    await context.route("**/*", guard)
    context.on("page", on_page)
    _start_reaper()
    return session


async def _session(ctx: ToolContext, *, create: bool = True) -> _Session | None:
    s = _sessions.get(ctx.token_id)
    if s is None:
        if not create:
            return None
        s = _sessions[ctx.token_id] = await _launch(ctx)
    if s.page.is_closed():
        open_pages = [p for p in s.context.pages if not p.is_closed()]
        s.page = open_pages[-1] if open_pages else await s.context.new_page()
    s.last_used = time.monotonic()
    return s


async def _close(token_id: str) -> None:
    s = _sessions.pop(token_id, None)
    if s is not None:
        try:
            await s.context.close()
        except Exception as exc:  # noqa: BLE001
            log.debug("closing browser for %s: %s", token_id, exc)


def _start_reaper() -> None:
    global _reaper
    if _reaper is None or _reaper.done():
        _reaper = asyncio.create_task(_reap_idle())


async def _reap_idle() -> None:
    while _sessions:
        await asyncio.sleep(60)
        now = time.monotonic()
        for token_id, s in list(_sessions.items()):
            lock = _lock_for(token_id)
            if now - s.last_used > IDLE_SECONDS and not lock.locked():
                async with lock:
                    await _close(token_id)


async def shutdown_browsers() -> None:
    """Close every browser session and stop Playwright (call on app shutdown)."""
    global _playwright, _reaper, _pw_lock
    if _reaper is not None:
        _reaper.cancel()
        _reaper = None
    for token_id in list(_sessions):
        await _close(token_id)
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception as exc:  # noqa: BLE001
            log.debug("stopping playwright: %s", exc)
        _playwright = None
    _pw_lock = None
    _locks.clear()


# ---------------------------------------------------------------------------------------
# Snapshots


def _host(url: str) -> str:
    host = urlsplit(url).hostname or url
    return host[4:] if host.startswith("www.") else host


def _defang(text: str) -> str:
    """Stop page text from closing the untrusted wrapper early."""
    return re.sub(r"(?i)<(/?)untrusted_content", r"<\1untrusted-content", text)


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t \r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def format_element(el: dict) -> str:
    tag, typ, role = el.get("tag", ""), el.get("type", ""), el.get("role", "")
    name = (el.get("name") or "").replace('"', "'")
    if tag == "a" or role == "link":
        kind = "link"
    elif tag == "button" or role == "button" or (tag == "input" and typ in ("submit", "button", "reset", "image")):
        kind = "button"
    elif tag == "input":
        kind = f"input[type={typ}]"
    elif tag in ("select", "textarea"):
        kind = tag
    elif el.get("contenteditable"):
        kind = "textbox"
    else:
        kind = role or tag
    line = f'[{el["id"]}] {kind} "{name}"'
    href = el.get("href") or ""
    if kind == "link" and href and not href.startswith("javascript:"):
        line += f" -> {href[:100]}"
    extras = []
    if el.get("is_submit"):
        extras.append("submits form")
    if el.get("checked") is not None:
        extras.append("checked" if el["checked"] else "unchecked")
    if el.get("value") and kind != "button":
        extras.append(f"value: {el['value']}")
    if tag == "select":
        extras.append(f"selected: {el.get('selected') or '-'}; options: {', '.join(el.get('options') or [])}")
    if el.get("disabled"):
        extras.append("disabled")
    if not el.get("in_view"):
        extras.append("off screen")
    if extras:
        line += f" ({'; '.join(extras)})"
    return line


async def _snapshot(s: _Session, note: str = "") -> str:
    page = s.page
    try:
        data = await page.evaluate(_SNAPSHOT_JS, MAX_ELEMENTS)
    except Exception as exc:  # noqa: BLE001  (page navigating, crashed, or a non-HTML document)
        await asyncio.sleep(0.5)
        try:
            data = await page.evaluate(_SNAPSHOT_JS, MAX_ELEMENTS)
        except Exception:  # noqa: BLE001
            raise ToolError(f"Could not read the page ({str(exc).splitlines()[0][:200]}). "
                            "Try browser_snapshot again in a moment.") from exc
    s.known_ids = {el["id"] for el in data["elements"]}
    url = data.get("url") or page.url
    text = _clean_text(data.get("text") or "")
    truncated = len(text) > MAX_TEXT
    if truncated:
        text = text[:MAX_TEXT] + f"\n[... page text truncated at {MAX_TEXT} characters; scroll or open a more specific page]"
    lines = [format_element(el) for el in data["elements"]]
    if data.get("more"):
        lines.append(f"[... {data['more']} more elements not listed; scroll to see them]")
    body = (f"Title: {data.get('title') or '(none)'}\nURL: {url}\n\nPage text:\n{text or '(no text)'}\n\n"
            f"Interactive elements:\n" + ("\n".join(lines) if lines else "(none)"))
    head = []
    if note:
        head.append(note)
    if s.blocked:
        head.append("Blocked navigation (not allowed for safety): " + "; ".join(s.blocked[-3:]))
        s.blocked.clear()
    source = f"web page {url[:200]}".replace('"', "%22")
    out = untrusted(source, _defang(body))
    out += ("\nUse the [number] of an element with browser_click, browser_type or browser_select. "
            "Numbers change with every snapshot.")
    return ("\n".join(head) + "\n\n" + out) if head else out


async def _settle(page) -> None:
    """Give a click / submit a moment to navigate or update; never fail if the page stays busy."""
    await asyncio.sleep(0.3)
    for state, timeout in (("load", 5000), ("networkidle", 2500)):
        try:
            await page.wait_for_load_state(state, timeout=timeout)
        except Exception:  # noqa: BLE001
            pass


async def _element(s: _Session, element_id: int):
    if element_id not in s.known_ids:
        raise ToolError(f"Element [{element_id}] is not in the latest snapshot. Take a new browser_snapshot "
                        "and use a number from it.")
    loc = s.page.locator(f'[data-bea-id="{element_id}"]').first
    try:
        info = await loc.evaluate(_DESCRIBE_JS, timeout=3000)
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"Element [{element_id}] is gone (the page changed). Take a new browser_snapshot.") from exc
    return loc, info


def _label(info: dict) -> str:
    return (info.get("name") or info.get("placeholder") or info.get("tag") or "element")[:80]


# ---------------------------------------------------------------------------------------
# Tools

_ID = {"type": "integer", "minimum": 1, "description": "The [number] of the element in the latest snapshot."}


@tool("browser_open",
      "Open a web page in your own browser and read it. Returns the page text and numbered interactive "
      "elements. Use for sites that need interaction (forms, logged-in pages, dynamic apps); for plain "
      "reading prefer web_fetch.",
      obj({"url": {"type": "string", "description": "Full http(s) URL."}}, ["url"]),
      group="browser", label="Opening a web page", summarize=lambda d: f"Open {d.get('url', '')}")
async def browser_open(ctx: ToolContext, data: dict) -> str:
    url = data["url"].strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I):
        url = "https://" + url
    reason = await url_block_reason(url, ctx.settings.browser_allow_private_networks)
    if reason:
        raise ToolError(f"Cannot open {url}: {reason}.")
    async with _lock_for(ctx.token_id):
        s = await _session(ctx)
        try:
            await s.page.goto(url, wait_until="load")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).splitlines()[0][:200]
            if "Timeout" not in msg:
                raise ToolError(f"Could not open {url}: {msg}") from exc
        await _settle(s.page)
        return await _snapshot(s)


@tool("browser_snapshot", "Read the current page in your browser again (text and numbered elements).",
      obj({}), group="browser", label="Reading the page")
async def browser_snapshot(ctx: ToolContext, data: dict) -> str:
    async with _lock_for(ctx.token_id):
        s = await _session(ctx, create=False)
        if s is None:
            raise ToolError("The browser is not open. Use browser_open first.")
        return await _snapshot(s)


@tool("browser_click",
      "Click an element from the latest snapshot. Clicks that submit, send, buy, book, sign in, post or "
      "delete wait for the person's approval.",
      obj({"element_id": _ID}, ["element_id"]),
      group="browser", approval="dynamic", label="Clicking on a page",
      summarize=lambda d: f"Click element [{d.get('element_id')}]")
async def browser_click(ctx: ToolContext, data: dict) -> str:
    async with _lock_for(ctx.token_id):
        s = await _session(ctx, create=False)
        if s is None:
            raise ToolError("The browser is not open. Use browser_open first.")
        loc, info = await _element(s, data["element_id"])
        reason = click_risk(info)
        if reason:
            url = s.page.url
            await ctx.approve_or_raise(
                "browser_click", f'Click "{_label(info)}" on {_host(url)}',
                {"url": url, "element": _label(info), "role": format_element({**info, "id": data["element_id"],
                                                                              "in_view": True}),
                 "why": reason})
        try:
            await loc.click(timeout=10_000)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Could not click [{data['element_id']}]: {str(exc).splitlines()[0][:200]}") from exc
        await _settle(s.page)
        return await _snapshot(s, f'Clicked "{_label(info)}".')


@tool("browser_type",
      "Type text into a field from the latest snapshot (replaces what is there). Set submit=true to press "
      "Enter afterwards; submitting a form other than a search waits for approval. Password, card, OTP, "
      "Aadhaar/PAN and bank fields are refused: the person must type those themselves.",
      obj({"element_id": _ID, "text": {"type": "string"},
           "submit": {"type": "boolean", "description": "Press Enter after typing.", "default": False}},
          ["element_id", "text"]),
      group="browser", approval="dynamic", label="Typing on a page",
      summarize=lambda d: f"Type into element [{d.get('element_id')}]" + (" and submit" if d.get("submit") else ""))
async def browser_type(ctx: ToolContext, data: dict) -> str:
    async with _lock_for(ctx.token_id):
        s = await _session(ctx, create=False)
        if s is None:
            raise ToolError("The browser is not open. Use browser_open first.")
        loc, info = await _element(s, data["element_id"])
        what = sensitive_field(info)
        if what:
            raise ToolError(f"This field is for {what}. You must never type that: ask the person to enter it "
                            "themselves in the browser, then continue.")
        typable = (info.get("tag") == "textarea" or info.get("contenteditable")
                   or info.get("role") in ("textbox", "searchbox", "combobox")
                   or (info.get("tag") == "input" and info.get("type") in TEXT_INPUT_TYPES))
        if not typable:
            raise ToolError(f"Element [{data['element_id']}] is not a text field. Use browser_click or "
                            "browser_select for it.")
        if info.get("disabled"):
            raise ToolError(f"Element [{data['element_id']}] is disabled.")
        submit = bool(data.get("submit"))
        if submit and not is_search_field(info):
            url = s.page.url
            await ctx.approve_or_raise(
                "browser_type", f'Fill in "{_label(info)}" and submit on {_host(url)}',
                {"url": url, "field": _label(info), "text": data["text"][:500]})
        try:
            await loc.fill(data["text"], timeout=10_000)
            if submit:
                await loc.press("Enter")
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Could not type into [{data['element_id']}]: {str(exc).splitlines()[0][:200]}") from exc
        await _settle(s.page)
        return await _snapshot(s, f'Typed into "{_label(info)}"' + (" and pressed Enter." if submit else "."))


@tool("browser_select", "Choose an option in a dropdown (select) from the latest snapshot.",
      obj({"element_id": _ID, "option": {"type": "string", "description": "The option's visible text or value."}},
          ["element_id", "option"]),
      group="browser", label="Choosing an option")
async def browser_select(ctx: ToolContext, data: dict) -> str:
    async with _lock_for(ctx.token_id):
        s = await _session(ctx, create=False)
        if s is None:
            raise ToolError("The browser is not open. Use browser_open first.")
        loc, info = await _element(s, data["element_id"])
        if info.get("tag") != "select":
            raise ToolError(f"Element [{data['element_id']}] is not a dropdown. Use browser_click for custom menus.")
        try:
            await loc.select_option(data["option"], timeout=5000)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Could not choose {data['option']!r}: {str(exc).splitlines()[0][:200]}. "
                            f"Options: {', '.join(info.get('options') or [])}") from exc
        await _settle(s.page)
        return await _snapshot(s, f'Chose "{data["option"]}" in "{_label(info)}".')


@tool("browser_scroll", "Scroll the current page up or down by about one screen.",
      obj({"direction": {"type": "string", "enum": ["down", "up"], "default": "down"}}),
      group="browser", label="Scrolling the page")
async def browser_scroll(ctx: ToolContext, data: dict) -> str:
    async with _lock_for(ctx.token_id):
        s = await _session(ctx, create=False)
        if s is None:
            raise ToolError("The browser is not open. Use browser_open first.")
        dy = -1 if data.get("direction") == "up" else 1
        await s.page.evaluate("(dy) => window.scrollBy(0, dy * window.innerHeight * 0.85)", dy)
        await asyncio.sleep(0.4)
        return await _snapshot(s)


@tool("browser_back", "Go back to the previous page in your browser.", obj({}),
      group="browser", label="Going back")
async def browser_back(ctx: ToolContext, data: dict) -> str:
    async with _lock_for(ctx.token_id):
        s = await _session(ctx, create=False)
        if s is None:
            raise ToolError("The browser is not open. Use browser_open first.")
        try:
            resp = await s.page.go_back(wait_until="load", timeout=10_000)
        except Exception:  # noqa: BLE001
            resp = True  # navigated but slow to load
        await _settle(s.page)
        return await _snapshot(s, "" if resp is not None else "There was no previous page.")


@tool("browser_screenshot", "Take a screenshot of what is visible in your browser, to see layout, images "
      "or anything the text snapshot misses.", obj({}), group="browser", label="Looking at the page")
async def browser_screenshot(ctx: ToolContext, data: dict) -> list[dict]:
    async with _lock_for(ctx.token_id):
        s = await _session(ctx, create=False)
        if s is None:
            raise ToolError("The browser is not open. Use browser_open first.")
        try:
            png = await s.page.screenshot(type="png", full_page=False, scale="css", timeout=10_000)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Could not take a screenshot: {str(exc).splitlines()[0][:200]}") from exc
        return [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(png).decode()}},
                {"type": "text", "text": f"Screenshot of {s.page.url}"}]


@tool("browser_close", "Close your browser (cookies and logins are kept for next time).", obj({}),
      group="browser", label="Closing the browser")
async def browser_close(ctx: ToolContext, data: dict) -> str:
    async with _lock_for(ctx.token_id):
        if ctx.token_id not in _sessions:
            return "The browser was not open."
        await _close(ctx.token_id)
        return "Browser closed."


async def _note(ctx: ToolContext) -> str:
    return ("You have your own web browser (browser_* tools) for sites that need interaction; prefer "
            "web_search/web_fetch for plain research. Clicks that submit, send, buy or sign in wait for the "
            "person's approval. Never enter passwords, card details, OTPs or ID numbers; ask the person to do that.")


register_group("browser", note=_note)
