import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from app.agent import browser
from app.agent.browser import click_risk, sensitive_field, url_block_reason
from app.agent.registry import ApprovalDenied, ToolContext, ToolError
from app.config import Settings


# --- URL safety

async def test_public_urls_allowed(monkeypatch):
    assert await url_block_reason("https://8.8.8.8/path") is None
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("93.184.215.14", 0))])
    assert await url_block_reason("https://example.com/") is None


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/", "http://localhost/", "http://foo.localhost/", "http://10.1.2.3/",
    "http://192.168.1.1/admin", "http://169.254.169.254/latest/meta-data", "http://[::1]/",
    "http://0.0.0.0/", "file:///C:/Windows/win.ini", "chrome://settings", "data:text/html,hi",
    "javascript:alert(1)", "ftp://example.com/",
])
async def test_blocked_urls(url):
    assert await url_block_reason(url) is not None


async def test_hostname_resolving_to_private_is_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [
        (2, 1, 6, "", ("93.184.215.14", 0)), (2, 1, 6, "", ("10.0.0.5", 0))])
    assert await url_block_reason("https://sneaky.example/") is not None


async def test_private_allowed_when_configured():
    assert await url_block_reason("http://127.0.0.1:9/", allow_private=True) is None
    assert await url_block_reason("file:///etc/passwd", allow_private=True) is not None


# --- element classifiers

def el(**kw):
    base = {"tag": "button", "type": "", "role": "", "name": "", "text": "", "aria": "", "label": "",
            "value_attr": "", "title": "", "href": "", "is_submit": False}
    base.update(kw)
    return base


@pytest.mark.parametrize("e", [
    el(name="Place order", text="Place order"),
    el(name="Go", is_submit=True),
    el(tag="input", type="submit", name="Continue", value_attr="Continue", is_submit=True),
    el(name="Buy now"), el(name="Sign in"), el(name="Log In"), el(aria="Send message"),
    el(name="Proceed to checkout"), el(name="Delete account"), el(name="Cancel subscription"),
    el(name="I agree"), el(tag="a", name="Continue", href="/checkout/step1"),
    el(tag="a", name="Account", href="https://example.com/login?next=/"),
    el(name="Book now"), el(name="Publish"), el(name="Pay ₹499"),
])
def test_risky_clicks(e):
    assert click_risk(e)


@pytest.mark.parametrize("e", [
    el(name="Show more"), el(name="Next page"), el(role="tab", name="Reviews"),
    el(tag="a", name="Pricing", href="/pricing"), el(tag="a", name="Facebook", href="https://facebook.com/x"),
    el(name="Menu"), el(tag="a", name="Read the blog", href="/blog/posts/12"),
    el(name="Add to cart"),
])
def test_harmless_clicks(e):
    assert click_risk(e) is None


def field(**kw):
    base = {"tag": "input", "type": "text", "field_name": "", "field_id": "", "placeholder": "", "label": "",
            "aria": "", "name": "", "autocomplete": ""}
    base.update(kw)
    return base


@pytest.mark.parametrize("f", [
    field(type="password"), field(autocomplete="cc-number"), field(autocomplete="cc-csc"),
    field(autocomplete="one-time-code"), field(field_name="cardNumber"), field(field_id="card_number"),
    field(placeholder="CVV"), field(label="Enter OTP"), field(label="Aadhaar number"), field(field_name="pan"),
    field(label="SSN"), field(label="Bank account number"), field(field_name="user_password"),
    field(label="UPI PIN"),
])
def test_sensitive_fields(f):
    assert sensitive_field(f)


@pytest.mark.parametrize("f", [
    field(field_name="q", placeholder="Search"), field(type="email", label="Email"),
    field(label="Your name"), field(label="PIN code"), field(field_name="pincode"),
    field(tag="textarea", label="Message"), field(label="Company"),
])
def test_ordinary_fields(f):
    assert sensitive_field(f) is None


# --- end to end with a real browser

PAGES = {
    "/": """<!doctype html><html><head><title>Test shop</title></head><body>
<main><h1>Widget</h1><p>A very nice widget that does many useful things for the whole family, every day.
It is made of sturdy materials and ships quickly. Buy it now and enjoy it for many years to come.</p>
<a href="/about">About us</a>
<form action="/ordered" method="get">
  <label for="name">Your name</label><input id="name" name="name" type="text">
  <label for="pw">Secret</label><input id="pw" name="pw" type="password">
  <button type="submit">Place order</button>
</form></main></body></html>""",
    "/about": "<!doctype html><html><head><title>About</title></head><body><p>About page text</p></body></html>",
}


@pytest.fixture
def server():
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            body = PAGES.get(self.path.split("?")[0], "<html><body>Thanks for your order</body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", hits
    httpd.shutdown()
    httpd.server_close()


def find_id(snapshot: str, pattern: str) -> int:
    m = re.search(r"\[(\d+)\] " + pattern, snapshot)
    assert m, f"{pattern!r} not in snapshot:\n{snapshot}"
    return int(m.group(1))


async def test_browser_end_to_end(tmp_path, server):
    base, hits = server
    settings = Settings(_env_file=None, data_dir=str(tmp_path), browser_allow_private_networks=True,
                        browser_headless=True)
    approvals = []

    async def request_approval(tool, summary, details):
        approvals.append((tool, summary, details))
        return False

    ctx = ToolContext(app=SimpleNamespace(state=SimpleNamespace()), settings=settings, token_id="tok-1",
                      run_id="run-1", http=None, request_approval=request_approval)
    try:
        try:
            snap = await browser.browser_open(ctx, {"url": base + "/"})
        except ToolError as exc:
            if "could not start" in str(exc):
                pytest.skip(f"Chromium unavailable: {exc}")
            raise
        assert "<untrusted_content" in snap and "Test shop" in snap and "very nice widget" in snap
        order = find_id(snap, r'button "Place order"')
        name = find_id(snap, r'input\[type=text\] "Your name"')
        pw = find_id(snap, r'input\[type=password\]')
        about = find_id(snap, r'link "About us" -> /about')

        snap = await browser.browser_type(ctx, {"element_id": name, "text": "Asha"})
        assert "value: Asha" in snap
        name, pw = find_id(snap, r'input\[type=text\]'), find_id(snap, r'input\[type=password\]')
        order = find_id(snap, r'button "Place order"')

        with pytest.raises(ToolError, match="password"):
            await browser.browser_type(ctx, {"element_id": pw, "text": "hunter2"})

        with pytest.raises(ApprovalDenied):
            await browser.browser_click(ctx, {"element_id": order})
        assert len(approvals) == 1 and approvals[0][0] == "browser_click"
        assert approvals[0][1] == 'Click "Place order" on 127.0.0.1'
        assert not any(h.startswith("/ordered") for h in hits)

        with pytest.raises(ToolError, match="new browser_snapshot"):
            await browser.browser_click(ctx, {"element_id": 999})

        snap = await browser.browser_snapshot(ctx, {})
        about = find_id(snap, r'link "About us"')
        snap = await browser.browser_click(ctx, {"element_id": about})
        assert len(approvals) == 1
        assert "About page text" in snap and "/about" in hits

        shot = await browser.browser_screenshot(ctx, {})
        assert shot[0]["type"] == "image" and shot[0]["source"]["media_type"] == "image/png"

        assert await browser.browser_close(ctx, {}) == "Browser closed."
    finally:
        await browser.shutdown_browsers()


async def test_open_blocks_private_by_default(tmp_path):
    settings = Settings(_env_file=None, data_dir=str(tmp_path))
    ctx = ToolContext(app=SimpleNamespace(state=SimpleNamespace()), settings=settings, token_id="tok-2",
                      run_id="run-2", http=None)
    with pytest.raises(ToolError, match="private or local|localhost"):
        await browser.browser_open(ctx, {"url": "http://127.0.0.1:1/"})
    assert "tok-2" not in browser._sessions
