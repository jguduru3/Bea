"""Streaming chat providers: Anthropic Messages API and any OpenAI-compatible API."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

from ..config import Settings
from ..metrics import LLM_TOKENS, UPSTREAM_CALLS, UPSTREAM_LATENCY
from .base import UpstreamError, open_stream, sse_events

log = logging.getLogger("pom.llm")


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    provider: str = ""  # which model actually answered (differs from the main one after a fallback)
    model: str = ""
    extra: dict = field(default_factory=dict)


class LLMProvider:
    name = "base"
    model = ""

    async def stream(self, system: str, messages: list[dict], usage: Usage) -> AsyncIterator[str]:  # pragma: no cover
        raise NotImplementedError
        yield ""


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.s, self.client = settings, client
        self.model = settings.anthropic_model

    async def stream(self, system: str, messages: list[dict], usage: Usage) -> AsyncIterator[str]:
        key = self.s.anthropic_api_key.get_secret_value() if self.s.anthropic_api_key else ""
        if not key:
            raise UpstreamError(self.name, 503, "No Anthropic API key configured")
        usage.provider, usage.model = self.name, self.model
        started = time.perf_counter()
        resp = await open_stream(
            self.client, self.name, self.s.upstream_retries,
            self.s.anthropic_base_url.rstrip("/") + "/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": self.model, "system": system, "messages": messages, "stream": True,
                "max_tokens": self.s.max_output_tokens, "temperature": self.s.temperature,
            },
        )
        try:
            async for event, data in sse_events(resp):
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                typ = payload.get("type") or event
                if typ == "content_block_delta":
                    delta = payload.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield delta["text"]
                elif typ == "message_start":
                    usage.input_tokens = payload.get("message", {}).get("usage", {}).get("input_tokens", 0)
                elif typ == "message_delta":
                    usage.output_tokens = payload.get("usage", {}).get("output_tokens", usage.output_tokens)
                elif typ == "error":
                    err = payload.get("error", {})
                    raise UpstreamError(self.name, 502, err.get("message", "stream error"))
        finally:
            await resp.aclose()
            usage.latency_ms = int((time.perf_counter() - started) * 1000)
            UPSTREAM_CALLS.labels(self.name, "chat", "ok").inc()
            UPSTREAM_LATENCY.labels(self.name, "chat").observe(usage.latency_ms / 1000)
            LLM_TOKENS.labels("in").inc(usage.input_tokens)
            LLM_TOKENS.labels("out").inc(usage.output_tokens)


class OpenAICompatibleProvider(LLMProvider):
    name = "openai"

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.s, self.client = settings, client
        self.model = settings.openai_model

    async def stream(self, system: str, messages: list[dict], usage: Usage) -> AsyncIterator[str]:
        key = self.s.openai_api_key.get_secret_value() if self.s.openai_api_key else ""
        if not key:
            raise UpstreamError(self.name, 503, "No OpenAI API key configured")
        usage.provider, usage.model = self.name, self.model
        started = time.perf_counter()
        resp = await open_stream(
            self.client, self.name, self.s.upstream_retries,
            self.s.openai_base_url.rstrip("/") + "/chat/completions",
            headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
            json={
                "model": self.model, "stream": True, "stream_options": {"include_usage": True},
                "messages": [{"role": "system", "content": system}, *messages],
                "max_tokens": self.s.max_output_tokens, "temperature": self.s.temperature,
            },
        )
        try:
            async for _event, data in sse_events(resp):
                if data.strip() == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if payload.get("error"):
                    raise UpstreamError(self.name, 502, str(payload["error"].get("message", "stream error")))
                for choice in payload.get("choices") or []:
                    text = (choice.get("delta") or {}).get("content")
                    if text:
                        yield text
                u = payload.get("usage")
                if u:
                    usage.input_tokens = u.get("prompt_tokens", 0)
                    usage.output_tokens = u.get("completion_tokens", 0)
        finally:
            await resp.aclose()
            usage.latency_ms = int((time.perf_counter() - started) * 1000)
            UPSTREAM_CALLS.labels(self.name, "chat", "ok").inc()
            UPSTREAM_LATENCY.labels(self.name, "chat").observe(usage.latency_ms / 1000)
            LLM_TOKENS.labels("in").inc(usage.input_tokens)
            LLM_TOKENS.labels("out").inc(usage.output_tokens)


class FallbackProvider(LLMProvider):
    """Uses the main model, and switches to the backup if the main one fails before any text arrives.

    Once text has been streamed the error is raised as-is: restarting on another model would
    send the user two different half-answers.
    """

    def __init__(self, primary: LLMProvider, backup: LLMProvider) -> None:
        self.primary, self.backup = primary, backup
        self.name, self.model = primary.name, primary.model

    async def stream(self, system: str, messages: list[dict], usage: Usage) -> AsyncIterator[str]:
        sent = False
        try:
            async for chunk in self.primary.stream(system, messages, usage):
                sent = True
                yield chunk
            return
        except UpstreamError as exc:
            if sent:
                raise
            log.warning("main model failed, using backup", extra={"provider": exc.provider, "status": exc.status,
                                                                  "detail": exc.message, "backup": self.backup.name})
        async for chunk in self.backup.stream(system, messages, usage):
            yield chunk


_PROVIDERS = {"anthropic": AnthropicProvider, "openai": OpenAICompatibleProvider}


def build_llm(settings: Settings, client: httpx.AsyncClient) -> LLMProvider:
    main, backup = settings.llm_provider, settings.llm_fallback_provider
    if not settings.provider_configured(main) and settings.provider_configured(backup):
        main, backup = backup, None  # only the backup has a key: use it directly
    primary = _PROVIDERS[main](settings, client)
    if backup and backup != main and settings.provider_configured(backup):
        return FallbackProvider(primary, _PROVIDERS[backup](settings, client))
    return primary
