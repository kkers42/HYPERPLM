"""
HYPERPLM — FastAPI request dependencies (Phase 2, step 5).

Per request:
1. get_principal (GLOBAL path): decode the JWT, load the user, and re-read the active-org
   membership from org_members. The token's active_org_id is only a HINT — membership and
   role are authoritative and re-read every request (§12.1), so a removed/downgraded user is
   denied immediately, not at token expiry.
2. get_ctx: open one tenant_session for the active org (RLS-scoped), read the role abilities
   inside it, and yield a RequestContext for the whole request. The transaction commits on
   success and rolls back if the handler raises.

require_ability / require_admin gate on the role abilities and return the context.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional

from fastapi import Depends, HTTPException, Request, status

from . import repo
from .auth import decode_token
from .tenancy import (
    TenantDB,
    get_membership,
    get_role_abilities,
    global_session,
    list_user_orgs,
    tenant_session,
)


@dataclass
class RequestContext:
    db: TenantDB
    user: dict            # principal: id, username, email, is_platform_admin, active_org_id, role_id
    abilities: dict       # can_view/can_write/... for the active-org role
    org_id: int


def _unauth(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def get_principal(request: Request) -> dict:
    """Resolve the caller: user + active org + membership. GLOBAL path only (§12.2)."""
    token = request.cookies.get("plm_session")
    if not token:
        raise _unauth()
    try:
        payload = decode_token(token)
    except Exception:
        raise _unauth("Invalid session")

    user_id = int(payload["sub"])
    hint_org = payload.get("active_org_id")

    with global_session() as c:
        user = repo.get_user(c, user_id)
        if not user or not user.get("is_active"):
            raise _unauth("User not found or disabled")

        active_org_id = hint_org
        membership = get_membership(c, user_id, active_org_id) if active_org_id is not None else None
        if membership is None:
            # Hint stale or absent — fall back to the user's first org.
            orgs = list_user_orgs(c, user_id)
            if not orgs:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "No organization for this account")
            active_org_id = orgs[0]["org_id"]
            membership = get_membership(c, user_id, active_org_id)

        repo.touch_user(c, user_id)

    return {
        "id": user_id,
        "username": user["username"],
        "email": user.get("email"),
        "is_platform_admin": bool(user.get("is_platform_admin", 0)),
        "active_org_id": active_org_id,
        "role_id": membership["role_id"] if membership else None,
    }


def get_ctx(principal: dict = Depends(get_principal)) -> Iterator[RequestContext]:
    """One tenant-scoped transaction for the whole request."""
    with tenant_session(principal["active_org_id"]) as db:
        abilities = get_role_abilities(db, principal["role_id"]) or {}
        yield RequestContext(db=db, user=principal, abilities=abilities,
                             org_id=principal["active_org_id"])


def require_ability(ability: str):
    """Dependency factory: require a role ability (view/write/release/upload/checkout)."""
    def _check(ctx: RequestContext = Depends(get_ctx)) -> RequestContext:
        if not ctx.abilities.get(f"can_{ability}"):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"Your role does not have '{ability}' permission")
        return ctx
    return _check


def require_admin(ctx: RequestContext = Depends(get_ctx)) -> RequestContext:
    if not ctx.abilities.get("can_admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return ctx


def optional_ctx(request: Request):
    """For pages: None if not authenticated, else a resolved principal (no tenant txn)."""
    try:
        return get_principal(request)
    except HTTPException:
        return None
