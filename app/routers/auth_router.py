"""
HYPERPLM — Auth routes. AUTH_MODE = local | google | windows.

Local mode supports self-service registration (which creates the user's first org).
Every issued token carries active_org_id as a hint; the server re-resolves membership
and role on every request (see deps.get_principal).
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from .. import accounts, config, repo
from ..auth import (
    create_token,
    exchange_google_code,
    google_auth_url,
    hash_password,
    make_cookie_kwargs,
)
from ..deps import RequestContext, get_ctx, get_principal
from ..models import LoginRequest, MessageResponse, RegisterRequest
from ..security import note_login_failure, rate_limit_login, rate_limit_register
from ..tenancy import global_session

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue(response: Response, user: dict, org_id) -> None:
    token = create_token(user["id"], user["username"], org_id, user.get("email") or "")
    response.set_cookie(value=token, **make_cookie_kwargs())


# ── Local ─────────────────────────────────────────────────────────────────────

@router.post("/register", dependencies=[Depends(rate_limit_register)])
async def register(body: RegisterRequest, response: Response):
    if config.AUTH_MODE != "local":
        raise HTTPException(400, "Registration is only available in local auth mode")
    if len(body.password) < config.PASSWORD_MIN_LENGTH:
        raise HTTPException(400, f"Password must be at least {config.PASSWORD_MIN_LENGTH} characters")
    try:
        user, org_id = accounts.register_local_user(
            body.username.strip(), body.email, body.password, body.org_name
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    _issue(response, user, org_id)
    return {"message": "ok", "active_org_id": org_id}


@router.post("/login", dependencies=[Depends(rate_limit_login)])
async def local_login(body: LoginRequest, request: Request, response: Response):
    if config.AUTH_MODE != "local":
        raise HTTPException(400, "Local login not enabled")
    user = accounts.verify_local_login(body.username, body.password)
    if not user:
        note_login_failure(request)
        raise HTTPException(401, "Invalid username or password")
    org_id = accounts.default_org_for(user["id"])
    _issue(response, user, org_id)
    return {
        "message": "ok",
        "must_change_password": bool(user.get("must_change_password", 0)),
        "active_org_id": org_id,
    }


# ── Windows identity ──────────────────────────────────────────────────────────

@router.post("/windows", dependencies=[Depends(rate_limit_login)])
async def windows_login(body: dict, request: Request, response: Response):
    if config.AUTH_MODE != "windows":
        raise HTTPException(400, "Windows auth not enabled")
    win_user = (body.get("username") or "").strip()
    if not win_user:
        raise HTTPException(400, "Username is required")
    bare = win_user.split("\\")[-1].split("/")[-1].lower()
    user = accounts.get_or_provision_external_user("", bare)
    if not user.get("is_active", 1):
        note_login_failure(request)
        raise HTTPException(403, "Your account has been disabled.")
    org_id = accounts.default_org_for(user["id"])
    _issue(response, user, org_id)
    return {"message": "ok", "active_org_id": org_id}


# ── Google OAuth ──────────────────────────────────────────────────────────────

@router.get("/google")
async def google_login():
    if config.AUTH_MODE != "google":
        raise HTTPException(400, "Google OAuth not enabled")
    return RedirectResponse(url=google_auth_url())


@router.get("/google/callback")
async def google_callback(code: str):
    if config.AUTH_MODE != "google":
        raise HTTPException(400, "Google OAuth not enabled")
    try:
        info = await exchange_google_code(code)
    except Exception:
        raise HTTPException(400, "Failed to authenticate with Google")
    email = (info.get("email") or "").lower()
    if not email:
        raise HTTPException(400, "No email returned from Google")
    if config.ALLOWED_EMAILS and email not in config.ALLOWED_EMAILS:
        raise HTTPException(403, "Your email is not authorized")
    name = info.get("name") or email.split("@")[0]
    user = accounts.get_or_provision_external_user(email, name)
    org_id = accounts.default_org_for(user["id"])
    base = config.APP_BASE_URL.rstrip("/")
    redir = RedirectResponse(url=f"{base}/app", status_code=302)
    token = create_token(user["id"], user["username"], org_id, email)
    redir.set_cookie(value=token, **make_cookie_kwargs())
    return redir


# ── Session ───────────────────────────────────────────────────────────────────

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("plm_session", path="/")
    return MessageResponse(message="Logged out")


@router.get("/me")
async def me(ctx: RequestContext = Depends(get_ctx)):
    ab = ctx.abilities
    return {
        "id": ctx.user["id"],
        "username": ctx.user["username"],
        "email": ctx.user.get("email"),
        "active_org_id": ctx.org_id,
        "role_id": ctx.user["role_id"],
        "role_name": ab.get("name"),
        "can_admin": bool(ab.get("can_admin", 0)),
        "can_write": bool(ab.get("can_write", 0)),
        "can_release": bool(ab.get("can_release", 0)),
        "can_checkout": bool(ab.get("can_checkout", 0)),
        "can_upload": bool(ab.get("can_upload", 0)),
        "is_platform_admin": ctx.user["is_platform_admin"],
        "auth_mode": config.AUTH_MODE,
    }


@router.post("/change-password")
async def change_password(body: dict, principal: dict = Depends(get_principal)):
    new_pw = body.get("new_password", "")
    if len(new_pw) < config.PASSWORD_MIN_LENGTH:
        raise HTTPException(400, f"Password must be at least {config.PASSWORD_MIN_LENGTH} characters")
    with global_session() as c:
        repo.update_user_password(c, principal["id"], hash_password(new_pw), must_change=0)
    return MessageResponse(message="Password changed")
