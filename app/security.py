"""
HYPERPLM — Security utilities: login rate limiting and HTTP security headers.

The rate limiter is in-memory and per-process — adequate for a single-worker
deployment. For multiple workers/hosts, back it with a shared store (e.g. Redis).

Login/Windows auth uses FAILURE-ONLY counting: the guard dependency checks the
current failure count without recording, and the handler records a failure only when
credentials are rejected. So a burst of *successful* logins from one NAT'd office IP
never triggers a lockout. Registration counts all attempts (abuse = volume of creates).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware

from . import config

# Cap the number of distinct keys tracked, so a flood of unique IPs can't grow the
# map without bound between the lazy per-key evictions.
_MAX_KEYS = 4096


def client_ip(request: Request) -> str:
    """Client IP. Honors X-Forwarded-For only when TRUST_PROXY is set (behind a proxy);
    otherwise a client could spoof the header to evade or poison rate limits."""
    if config.TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first
    return request.client.host if request.client else "unknown"


class SlidingWindowRateLimiter:
    """In-memory sliding-window limiter: max_attempts per window_seconds per key.

    Expired keys are evicted lazily (empty deques are dropped on prune), so the map
    does not grow unbounded.
    """

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> int:
        q = self._hits.get(key)
        if q is None:
            return 0
        cutoff = now - self.window_seconds
        while q and q[0] < cutoff:
            q.popleft()
        if not q:
            del self._hits[key]        # evict — no unbounded growth
            return 0
        return len(q)

    def _sweep(self, now: float) -> None:
        if len(self._hits) > _MAX_KEYS:
            for k in list(self._hits.keys()):
                self._prune(k, now)

    def _raise_if_over(self, key: str, now: float) -> None:
        count = self._prune(key, now)
        if count >= self.max_attempts:
            q = self._hits[key]
            retry_after = int(q[0] + self.window_seconds - now) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please wait and try again.",
                headers={"Retry-After": str(max(retry_after, 1))},
            )

    def guard(self, key: str) -> None:
        """Raise 429 if the key is already at/over the limit. Does NOT record."""
        self._raise_if_over(key, time.time())

    def record(self, key: str) -> None:
        """Record one hit against the key."""
        now = time.time()
        self._prune(key, now)
        self._hits.setdefault(key, deque()).append(now)
        self._sweep(now)

    def check(self, key: str) -> None:
        """Guard then record — for all-attempts limiting (e.g. registration)."""
        self.guard(key)
        self.record(key)


# Auth-attempt failures: 10 failures / 5 min / IP.
_login_limiter = SlidingWindowRateLimiter(max_attempts=10, window_seconds=300)
# Registration: 10 attempts / hour / IP (abuse = volume of account creation).
_register_limiter = SlidingWindowRateLimiter(max_attempts=10, window_seconds=3600)


def rate_limit_login(request: Request) -> None:
    """Dependency: reject if too many recent login failures from this IP (no record)."""
    _login_limiter.guard(f"login:{client_ip(request)}")


def note_login_failure(request: Request) -> None:
    """Record a failed login/auth attempt for this IP."""
    _login_limiter.record(f"login:{client_ip(request)}")


def rate_limit_register(request: Request) -> None:
    """Dependency: throttle registration attempts (all attempts counted)."""
    _register_limiter.check(f"register:{client_ip(request)}")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds conservative security headers. CSP is omitted while the static pages use
    inline scripts; add a nonce-based CSP when the frontend is reworked."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-XSS-Protection", "0")  # intentional: legacy auditor off
        if config.IS_PRODUCTION and config.APP_BASE_URL.startswith("https"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
