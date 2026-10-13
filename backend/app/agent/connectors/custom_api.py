"""A generic REST connector for the company's own platforms (projects, workforce) or any other API.

Each API is a ConnectorAccount (provider "custom_api", name = a short slug such as "projects").
Meta: base_url, auth_header, auth_scheme, description, allow_write, openapi_url, health_path.
Secret: {"token": ...}. The person's description goes into Bea's instructions so she knows the
endpoints; reads run freely, writes need allow_write and the person's approval each time.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from ..registry import ToolContext, ToolError, obj, register_group, tool, untrusted
from . import store
from .microsoft import account_names, clip

log = logging.getLogger("pom.connectors.custom_api")

PROVIDER = "custom_api"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_RESPONSE = 20000
READ_METHODS = {"GET", "HEAD"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
NAME_RE = re.compile(r"^[a-z0-9_-]{2,32}$")
_SPEC_CACHE: dict[tuple[str, str], str] = {}


def meta_of(row) -> dict:
    return json.loads(row.meta or "{}")


def auth_headers(meta: dict, secret: dict) -> dict:
    token = (secret or {}).get("token") or ""
    if not token:
        return {}
    header = (meta.get("auth_header") or "Authorization").strip()
    scheme = (meta.get("auth_scheme") or "").strip()
    return {header: f"{scheme} {token}" if scheme else token}


def resolve_url(base_url: str, path: str) -> str:
    """Join a relative path onto base_url, refusing anything that could leave it."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise ToolError("`path` must be relative to the API and start with '/', e.g. /tasks/42.")
    if "://" in path or "\\" in path or any(ord(c) < 32 for c in path):
        raise ToolError("`path` must be a plain relative path, not a full URL.")
    bare = path.split("?", 1)[0].split("#", 1)[0]
    decoded = unquote(unquote(bare))
    if any(seg in ("..", ".") for seg in decoded.replace("\\", "/").split("/")):
        raise ToolError("`path` may not contain '..' or '.' segments.")
    base = base_url.rstrip("/")
    url = base + path
    b, u = urlsplit(base), urlsplit(url)
    if (u.scheme, u.netloc) != (b.scheme, b.netloc) or not u.path.startswith(b.path):
        raise ToolError("That path would leave the configured API address.")
    return url


async def _api_row(ctx: ToolContext, api: str):
    rows = await store.list_accounts(ctx.sessionmaker, ctx.token_id, PROVIDER, enabled_only=True)
    for r in rows:
        if r.name == (api or "").strip().lower():
            return r
    names = ", ".join(r.name for r in rows) or "none"
    raise ToolError(f"No connected API named {api!r}. Connected APIs: {names}.")


async def send(http: httpx.AsyncClient, settings, row, method: str, path: str, query: dict | None = None,
               body=None) -> httpx.Response:
    """One request to a custom API. Follows redirects only when they stay on the same host."""
    meta = meta_of(row)
    url = resolve_url(meta["base_url"], path)
    headers = {"accept": "application/json, text/plain;q=0.9, */*;q=0.5", **auth_headers(meta, await store.load_secret(settings, row))}
    origin = urlsplit(meta["base_url"])
    kw: dict = {"params": {k: v if isinstance(v, (str, int, float, bool)) else json.dumps(v) for k, v in (query or {}).items()}}
    if body is not None and method not in READ_METHODS:
        if isinstance(body, str):
            kw["content"] = body.encode()
            headers["content-type"] = "text/plain; charset=utf-8"
        else:
            kw["json"] = body
    try:
        for _ in range(4):
            resp = await http.request(method, url, headers=headers, timeout=TIMEOUT, follow_redirects=False, **kw)
            if resp.status_code not in (301, 302, 303, 307, 308) or method not in READ_METHODS:
                return resp
            target = urljoin(url, resp.headers.get("location", ""))
            t = urlsplit(target)
            if (t.scheme, t.netloc) != (origin.scheme, origin.netloc):
                raise ToolError(f"The API redirected to another host ({t.netloc}); not following it.")
            url, kw["params"] = target, None
        return resp
    except httpx.TransportError as exc:
        raise ToolError(f"Couldn't reach the {row.name} API ({exc.__class__.__name__}). Check that it's online and "
                        "that the base URL in Bea Hub → Connections is right.") from exc


def format_response(resp: httpx.Response) -> str:
    ctype = resp.headers.get("content-type", "")
    text = resp.text if resp.request.method != "HEAD" else ""
    if "json" in ctype and text:
        try:
            text = json.dumps(resp.json(), ensure_ascii=False, indent=1)
        except ValueError:
            pass
    return f"HTTP {resp.status_code}\n" + (clip(text, MAX_RESPONSE) if text else "(no body)")


def _summary(api: str, method: str, path: str, body) -> str:
    b = "" if body is None else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))
    s = f"{method} {api} {path}" + (f" {b}" if b else "")
    return s if len(s) <= 200 else s[:197] + "…"


async def check_account(app, row) -> tuple[bool, str]:
    meta = meta_of(row)
    path = meta.get("health_path") or "/"
    try:
        resp = await send(app.state.http, app.state.settings, row, "GET", path)
    except ToolError as exc:
        return False, str(exc)
    ok = resp.status_code < 400
    hint = "" if ok else (" The token was rejected." if resp.status_code in (401, 403) else "")
    return ok, f"GET {path} returned HTTP {resp.status_code}.{hint}"


# ---------------------------------------------------------------- tools
async def _available(ctx: ToolContext) -> bool:
    return bool(await account_names(ctx, PROVIDER))


async def _note(ctx: ToolContext) -> str:
    rows = await store.list_accounts(ctx.sessionmaker, ctx.token_id, PROVIDER, enabled_only=True)
    if not rows:
        return ""
    lines = ["Connected company/custom APIs (use api_request; api_describe for full details):"]
    for r in rows:
        m = meta_of(r)
        desc = (m.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 300:
            desc = desc[:300] + "…"
        mode = "reads and writes (writes ask the person first)" if m.get("allow_write") else "read-only"
        lines.append(f"- {r.name} ({mode}): {desc or 'no description given'}")
    return "\n".join(lines)


register_group(PROVIDER, available=_available, note=_note)


@tool("api_request",
      "Call a connected company/custom API (e.g. the projects platform). `path` is relative to the API's base URL "
      "and starts with '/'. GET/HEAD run at once; POST/PUT/PATCH/DELETE need the API to allow writes and the "
      "person's approval.",
      obj({"api": {"type": "string", "description": "The API's short name"},
           "method": {"type": "string", "enum": ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]},
           "path": {"type": "string", "description": "e.g. /tasks/42"},
           "query": {"type": "object", "description": "Query string parameters"},
           "body": {"description": "JSON body for writes (object/array), or a string"}},
          ["api", "method", "path"]),
      group=PROVIDER, approval="dynamic", label="Calling an API",
      summarize=lambda a: _summary(a.get("api", ""), a.get("method", ""), a.get("path", ""), a.get("body")))
async def api_request(ctx: ToolContext, args: dict) -> str:
    row = await _api_row(ctx, args["api"])
    meta = meta_of(row)
    method = args["method"].upper()
    path, body = args["path"], args.get("body")
    resolve_url(meta["base_url"], path)  # validate before asking anyone
    if method in WRITE_METHODS:
        if not meta.get("allow_write"):
            raise ToolError(f"The {row.name} API is read-only in Bea's settings, so {method} isn't allowed. The person "
                            "can allow writes in Bea Hub → Connections.")
        await ctx.approve_or_raise("api_request", _summary(row.name, method, path, body),
                                   {"api": row.name, "method": method, "path": path, "query": args.get("query"), "body": body})
    elif method not in READ_METHODS:
        raise ToolError(f"Unsupported method {method}.")
    resp = await send(ctx.http, ctx.settings, row, method, path, args.get("query"), body)
    return untrusted(f"{row.name} API {method} {path}"[:120], format_response(resp))


def _compact_spec(spec: dict, limit: int = 8000) -> str:
    lines = []
    for p, ops in (spec.get("paths") or {}).items():
        if not isinstance(ops, dict):
            continue
        for m, op in ops.items():
            if m.lower() not in ("get", "post", "put", "patch", "delete", "head"):
                continue
            s = ""
            if isinstance(op, dict):
                s = (op.get("summary") or op.get("operationId") or (op.get("description") or "").split("\n")[0])[:120]
            lines.append(f"{m.upper()} {p}" + (f" — {s}" if s else ""))
    out = "\n".join(lines)
    return clip(out, limit) if out else "(the spec lists no endpoints)"


@tool("api_describe",
      "Show how to use a connected company/custom API: the person's description and, when an OpenAPI spec is "
      "configured, a list of its endpoints.",
      obj({"api": {"type": "string"}}, ["api"]),
      group=PROVIDER, label="Reading API docs")
async def api_describe(ctx: ToolContext, args: dict) -> str:
    row = await _api_row(ctx, args["api"])
    meta = meta_of(row)
    parts = [f"API {row.name}: base URL {meta.get('base_url')}; "
             f"{'writes allowed (with approval)' if meta.get('allow_write') else 'read-only'}.",
             "Description from the person:\n" + (meta.get("description") or "(none)")]
    spec_url = meta.get("openapi_url")
    if spec_url:
        key = (row.id, row.updated_at.isoformat() if row.updated_at else "")
        if key not in _SPEC_CACHE:
            _SPEC_CACHE[key] = await _fetch_spec(ctx, row, meta, spec_url)
        parts.append("Endpoints (from the OpenAPI spec):\n" + _SPEC_CACHE[key])
    return untrusted(f"{row.name} API docs", "\n\n".join(parts))


async def _fetch_spec(ctx: ToolContext, row, meta: dict, spec_url: str) -> str:
    base = urlsplit(meta["base_url"])
    url = spec_url if "://" in spec_url else urljoin(meta["base_url"].rstrip("/") + "/", spec_url.lstrip("/"))
    u = urlsplit(url)
    if u.scheme not in ("http", "https"):
        return "(the OpenAPI URL isn't http(s))"
    headers = {"accept": "application/json"}
    if (u.scheme, u.netloc) == (base.scheme, base.netloc):  # only send the token to the API's own host
        headers.update(auth_headers(meta, await store.load_secret(ctx.settings, row)))
    try:
        resp = await ctx.http.get(url, headers=headers, timeout=TIMEOUT, follow_redirects=False)
    except httpx.TransportError as exc:
        return f"(couldn't fetch the OpenAPI spec: {exc.__class__.__name__})"
    if resp.status_code != 200:
        return f"(couldn't fetch the OpenAPI spec: HTTP {resp.status_code})"
    if len(resp.content) > 5_000_000:
        return "(the OpenAPI spec is too large to read)"
    try:
        spec = resp.json()
    except ValueError:
        try:
            import yaml  # type: ignore
            spec = yaml.safe_load(resp.text)
        except Exception:  # noqa: BLE001
            return "(the OpenAPI spec isn't JSON; point openapi_url at the JSON version)"
    if not isinstance(spec, dict):
        return "(the OpenAPI spec couldn't be read)"
    return _compact_spec(spec)
