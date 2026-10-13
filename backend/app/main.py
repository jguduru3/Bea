"""Bea backend: chat with emotions, an agent that acts on goals, Sarvam voice, device auth, usage tracking."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .agent import browser, connectors, scheduler, tools_core  # noqa: F401  (importing registers the tools)
from .agent.runner import AgentManager, default_client_factory
from .config import Settings, get_settings
from .db import ApiToken, Database
from .keys import apply_saved_keys
from .logging_setup import request_id_var, setup_logging
from .metrics import HTTP_LATENCY, HTTP_REQUESTS
from .providers.base import UpstreamError
from .ratelimit import TokenBucketLimiter
from .routers import admin, agent, chat, connectors as connectors_router, health, keys, voice
from .security import create_token, hash_token

log = logging.getLogger("pom")


def create_app(settings: Settings | None = None, agent_client_factory=None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.db = Database(settings.database_url)
        await app.state.db.create_all()
        app.state.http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.upstream_timeout_s, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            headers={"user-agent": "pom-backend/1.0"},
        )
        app.state.base_settings = settings
        await apply_saved_keys(app)  # sets app.state.settings, llm and sarvam (keys saved from the app win over .env)
        app.state.limiter = TokenBucketLimiter(settings.rate_limit_per_minute, settings.rate_limit_burst)
        if settings.bootstrap_device_token:
            raw = settings.bootstrap_device_token.get_secret_value()
            async with app.state.db.sessionmaker() as s:
                exists = (await s.execute(select(ApiToken).where(ApiToken.token_hash == hash_token(raw)))).scalar_one_or_none()
                if not exists:
                    await create_token(s, "bootstrap device", raw)
        app.state.agent = AgentManager(app, agent_client_factory or default_client_factory)
        await app.state.agent.recover()
        sched = asyncio.create_task(scheduler.loop(app))
        live = app.state.settings
        log.info("pom backend started", extra={"env": settings.env, "llm": settings.llm_provider,
                                               "llm_fallback": settings.llm_fallback_provider,
                                               "chat_configured": live.llm_configured, "voice_configured": live.voice_configured})
        yield
        sched.cancel()
        await app.state.agent.shutdown()
        await browser.shutdown_browsers()
        await app.state.http.aclose()
        await app.state.db.dispose()

    app = FastAPI(
        title="Pom API",
        version="1.0.0",
        description="Backend for the Pom desktop companion: streaming chat with emotions, Sarvam AI voice, device tokens and usage reporting.",
        lifespan=lifespan,
        docs_url="/docs" if settings.env != "production" else None,
        redoc_url=None,
    )

    if settings.trusted_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "DELETE"],
                           allow_headers=["authorization", "content-type", "x-request-id"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error")
            response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
        elapsed = time.perf_counter() - started
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        HTTP_LATENCY.labels(path).observe(elapsed)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if path not in ("/metrics", "/healthz"):
            log.info("request", extra={"method": request.method, "path": path, "status": response.status_code,
                                       "ms": round(elapsed * 1000, 1), "token_id": getattr(request.state, "token_id", None)})
        return response

    @app.exception_handler(UpstreamError)
    async def upstream_error(_request: Request, exc: UpstreamError):
        return JSONResponse({"detail": f"{exc.provider} error: {exc.message}"}, status_code=502)

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(voice.router)
    app.include_router(keys.router)
    app.include_router(agent.router)
    app.include_router(connectors_router.router)
    app.include_router(admin.router)
    return app


app = create_app()
