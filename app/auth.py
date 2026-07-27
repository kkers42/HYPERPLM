"""
HYPERPLM — auth primitives: JWT tokens, password hashing, Google OAuth helpers.

No database access here. Per-request principal resolution (user + active org +
membership, re-read every request) lives in deps.py; account/org provisioning and
credential checks live in accounts.py.

The JWT carries `active_org_id` as a HINT only — membership and role are always
re-read from the database each request (§12.1), never trusted from the token.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt as _bcrypt_lib
import httpx
from jose import jwt

from . import config


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_token(user_id: int, username: str, active_org_id: Optional[int],
                 email: str = "") -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=config.JWT_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "username": username,
        "email": email or "",
        "active_org_id": active_org_id,
        "exp": expire,
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, config.SECRET_KEY, algorithms=[config.JWT_ALGORITHM])


def make_cookie_kwargs() -> dict:
    return dict(
        key="plm_session",
        path="/",
        httponly=True,
        samesite="lax",
        secure=config.APP_BASE_URL.startswith("https"),
        max_age=config.JWT_EXPIRE_HOURS * 3600,
    )


# ── Passwords ─────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return _bcrypt_lib.hashpw(password.encode(), _bcrypt_lib.gensalt()).decode()


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    if not password_hash:
        return False
    return _bcrypt_lib.checkpw(password.encode(), password_hash.encode())


# ── Google OAuth ──────────────────────────────────────────────────────────────

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def google_auth_url() -> str:
    import urllib.parse
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    }
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)


async def exchange_google_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "redirect_uri": config.google_redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()
        info_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        info_resp.raise_for_status()
        return info_resp.json()
