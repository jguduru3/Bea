"""Per-token rate limiting (token bucket).

The in-memory limiter suits a single API instance. For several replicas behind a
load balancer, put the same interface in front of Redis.
"""
from __future__ import annotations

import asyncio
import math
import time

from fastapi import HTTPException, Request, status


class TokenBucketLimiter:
    def __init__(self, per_minute: int, burst: int) -> None:
        self.rate = per_minute / 60.0
        self.capacity = max(1, burst)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()

    async def take(self, key: str, cost: float = 1.0) -> float:
        """Consume tokens; returns 0 when allowed, otherwise seconds until allowed."""
        async with self._lock:
            now = time.monotonic()
            tokens, last = self._buckets.get(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens >= cost:
                self._buckets[key] = (tokens - cost, now)
                return 0.0
            self._buckets[key] = (tokens, now)
            return (cost - tokens) / self.rate if self.rate else 60.0


def rate_limited(cost: float = 1.0):
    async def dependency(request: Request) -> None:
        limiter: TokenBucketLimiter = request.app.state.limiter
        key = getattr(request.state, "token_id", None) or (request.client.host if request.client else "anon")
        wait = await limiter.take(f"{key}", cost)
        if wait > 0:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Pom needs a short breather. Try again in a moment.",
                headers={"Retry-After": str(math.ceil(wait))},
            )

    return dependency
