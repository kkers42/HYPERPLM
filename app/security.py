"""
HYPERPLM — Security utilities: login rate limiting and HTTP security headers.

Phase 1 (security hardening) module. The rate limiter is in-memory and
per-process — adequate for a single-worker deployment. When the app scales to
multiple workers or hosts, back it with a shared store (e.g. Redis).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware

from . import config


def client_ip(request: Request) -> str:
    """Best-effort client IP.

    Honors the first entry of X-Forwarded-For so the limiter keys on the real
    client rather than the nginx proxy (which would otherwise throttle every
    user together). Only trust this when the app sits behind a proxy that sets
    the header; direct-exposed deployments should not forward it.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


class SlidingWindowRateLimiter:
    """Simple in-memory sliding-window limiter: max_attempts per window_seconds per key."""

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        """Record an attempt for `key`; raise HTTP 429 if the window is exceeded."""
        now = time.time()
        cutoff = now - self.window_seconds
        q = self._hits[key]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.max_attempts:
            retry_after = int(q[0] + self.window_seconds - now) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please wait and try again.",
                headers={"Retry-After": str(max(retry_after, 1))},
            )
        q.append(now)


# 10 failed-or-not login attempts per 5 minutes per client IP.
_login_limiter = SlidingWindowRateLimiter(max_attempts=10, window_seconds=300)


def rate_limit_login(request: Request) -> None:
    """FastAPI dependency: throttle authentication attempts per client IP."""
    _login_limiter.check(f"login:{client_ip(request)}")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds conservative security headers to every response.

    A Content-Security-Policy is intentionally omitted for now because the
    current static pages use inline scripts; add a nonce-based CSP when the
    frontend is reworked.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-XSS-Protection", "0")
        if config.IS_PRODUCTION and config.APP_BASE_URL.startswith("https"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
