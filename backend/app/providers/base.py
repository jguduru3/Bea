from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator

import httpx

from ..metrics import UPSTREAM_CALLS, UPSTREAM_LATENCY

log = logging.getLogger("pom.upstream")
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class UpstreamError(Exception):
    def __init__(self, provider: str, status: int, message: str) -> None:
        super().__init__(f"{provider} returned {status}: {message}")
        self.provider, self.status, self.message = provider, status, message


def _error_message(body: bytes) -> str:
    try:
        data = json.loads(body)
        err = data.get("error", data)
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
        return str(err)[:300]
    except Exception:
        return body[:300].decode("utf-8", "replace")


async def _backoff(attempt: int, resp: httpx.Response | None) -> None:
    retry_after = resp.headers.get("retry-after") if resp is not None else None
    try:
        delay = min(float(retry_after), 10.0) if retry_after else 0.0
    except ValueError:
        delay = 0.0
    delay = delay or min(8.0, 0.5 * 2**attempt) + random.random() * 0.25
    await asyncio.sleep(delay)


async def request_with_retry(
    client: httpx.AsyncClient, provider: str, kind: str, retries: int, method: str, url: str, **kwargs
) -> httpx.Response:
    """Plain request with retries on network errors and transient statuses."""
    started = time.perf_counter()
    for attempt in range(retries + 1):
        resp = None
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code < 400:
                UPSTREAM_CALLS.labels(provider, kind, "ok").inc()
                UPSTREAM_LATENCY.labels(provider, kind).observe(time.perf_counter() - started)
                return resp
            if resp.status_code not in RETRY_STATUS or attempt == retries:
                UPSTREAM_CALLS.labels(provider, kind, "error").inc()
                raise UpstreamError(provider, resp.status_code, _error_message(resp.content))
        except (httpx.TransportError,) as exc:
            if attempt == retries:
                UPSTREAM_CALLS.labels(provider, kind, "error").inc()
                raise UpstreamError(provider, 503, f"network error: {exc.__class__.__name__}") from exc
        log.warning("retrying upstream", extra={"provider": provider, "kind": kind, "attempt": attempt + 1})
        await _backoff(attempt, resp)
    raise AssertionError("unreachable")


async def open_stream(
    client: httpx.AsyncClient, provider: str, retries: int, url: str, **kwargs
) -> httpx.Response:
    """Open a streaming POST, retrying only before the first byte arrives."""
    for attempt in range(retries + 1):
        resp = None
        try:
            req = client.build_request("POST", url, **kwargs)
            resp = await client.send(req, stream=True)
            if resp.status_code < 400:
                return resp
            body = await resp.aread()
            await resp.aclose()
            if resp.status_code not in RETRY_STATUS or attempt == retries:
                UPSTREAM_CALLS.labels(provider, "chat", "error").inc()
                raise UpstreamError(provider, resp.status_code, _error_message(body))
        except httpx.TransportError as exc:
            if attempt == retries:
                UPSTREAM_CALLS.labels(provider, "chat", "error").inc()
                raise UpstreamError(provider, 503, f"network error: {exc.__class__.__name__}") from exc
        log.warning("retrying stream", extra={"provider": provider, "attempt": attempt + 1})
        await _backoff(attempt, resp)
    raise AssertionError("unreachable")


async def sse_events(resp: httpx.Response) -> AsyncIterator[tuple[str | None, str]]:
    """Yield (event, data) pairs from a server-sent-events response."""
    event, data = None, []
    async for line in resp.aiter_lines():
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = None, []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, "\n".join(data)
