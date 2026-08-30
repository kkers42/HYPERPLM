"""
HYPERPLM — Fan routes (Phase 3, issue #6).

Fans authenticate with the same session cookie as members, but they must NOT go
through `get_principal`: that dependency resolves an active org and 403s with
"No organization for this account", which is exactly what a spectator is.
`current_fan` below therefore decodes the token and loads the user WITHOUT
touching membership.

That is also the security boundary: nothing in this router opens a tenant
session or reads a tenant table. Fans only ever touch global tables
(fan_profiles, follows, point_ledger, badges, team_directory). Published team
data is read through the anonymous `/api/public/*` routes, which do the
directory → tenant_session → visibility='public' dance in one audited place.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from .. import fans
from ..accounts import verify_local_login
from ..auth import create_token, decode_token, make_cookie_kwargs
from ..repo import get_user
from ..security import note_login_failure, rate_limit_login, rate_limit_register
from ..tenancy import global_session

router = APIRouter(prefix="/api/fan", tags=["fans"])


def current_fan(request: Request) -> dict:
    """The signed-in user, with no org requirement. Fans have no membership."""
    token = request.cookies.get("plm_session")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")
    user_id = int(payload["sub"])
    with global_session() as c:
        user = get_user(c, user_id)
    if not user or not user.get("is_active"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    return {"id": user_id, "username": user["username"]}


class FanRegister(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=200)
    display_name: str = ""
    email: Optional[str] = None


class FanLogin(BaseModel):
    username: str
    password: str


@router.post("/register", status_code=201, dependencies=[Depends(rate_limit_register)])
async def register(body: FanRegister, response: Response):
    """Create a spectator account — a user with NO organization."""
    try:
        user = fans.register_fan(body.username, body.password,
                                 body.display_name, body.email)
    except fans.FanError as e:
        raise HTTPException(409, str(e))
    # active_org_id is None: this account belongs to no tenant.
    token = create_token(user["id"], user["username"], None, user.get("email") or "")
    response.set_cookie(value=token, **make_cookie_kwargs())
    return {"message": "ok", "username": user["username"], "fan": True}


@router.post("/login", dependencies=[Depends(rate_limit_login)])
async def login(body: FanLogin, request: Request, response: Response):
    """Sign in as a fan. Works for members too — they simply also have an org."""
    user = verify_local_login(body.username, body.password)
    if not user:
        note_login_failure(request)
        raise HTTPException(401, "Invalid username or password")
    profile = fans.ensure_profile(user["id"], user["username"])
    token = create_token(user["id"], user["username"], None, user.get("email") or "")
    response.set_cookie(value=token, **make_cookie_kwargs())
    return {"message": "ok", "username": user["username"],
            "fan_type": profile["fan_type"], "points": profile["points"]}


@router.get("/me")
async def me(fan: dict = Depends(current_fan)):
    data = fans.home(fan["id"])
    if not data:
        raise HTTPException(404, "No fan profile for this account")
    return data


@router.post("/follow/{slug}")
async def follow(slug: str, fan: dict = Depends(current_fan)):
    fans.ensure_profile(fan["id"], fan["username"])
    try:
        return fans.follow(fan["id"], slug)
    except fans.FanError as e:
        raise HTTPException(404, str(e))


@router.delete("/follow/{slug}")
async def unfollow(slug: str, fan: dict = Depends(current_fan)):
    try:
        return fans.unfollow(fan["id"], slug)
    except fans.FanError as e:
        raise HTTPException(404, str(e))


@router.get("/following")
async def my_following(fan: dict = Depends(current_fan)):
    return fans.following(fan["id"])


class PickIn(BaseModel):
    answer: str


@router.post("/predict/{question_id}")
async def predict(question_id: int, body: PickIn, fan: dict = Depends(current_fan)):
    """Submit or change a pick while the question is open."""
    from .. import predictions
    fans.ensure_profile(fan["id"], fan["username"])
    try:
        return predictions.submit(fan["id"], question_id, body.answer)
    except predictions.PredictionError as e:
        raise HTTPException(400, str(e))


@router.get("/predictions")
async def my_predictions(fan: dict = Depends(current_fan)):
    from .. import predictions
    return predictions.my_predictions(fan["id"])

