"""
HYPERPLM — Member management for the active org (admin only).

Users are global identities; membership + role are per-org. These routes manage the
membership of the caller's active organization.
"""
from fastapi import APIRouter, Depends, HTTPException

from .. import config, repo
from ..auth import hash_password
from ..deps import RequestContext, require_admin
from ..models import MessageResponse, PasswordReset, UserCreate, UserUpdate
from ..tenancy import get_membership, global_session

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
async def list_members(ctx: RequestContext = Depends(require_admin)):
    return repo.list_members(ctx.db)


@router.post("", status_code=201)
async def add_member(body: UserCreate, ctx: RequestContext = Depends(require_admin)):
    # Role must belong to the active org.
    if body.role_id is not None and not repo.get_role(ctx.db, body.role_id):
        raise HTTPException(400, "Role does not exist in this organization")
    with global_session() as c:
        user = repo.get_user_by_username(c, body.username)
        if user is None:
            user = repo.create_user(c, body.username, body.email, hash_password(body.password))
        else:
            existing = get_membership(c, user["id"], ctx.org_id)
            if existing:
                raise HTTPException(409, "User is already a member of this organization")
        repo.add_member(c, ctx.org_id, user["id"], body.role_id)
    return {"id": user["id"], "username": user["username"], "role_id": body.role_id}


@router.put("/{user_id}")
async def update_member(user_id: int, body: UserUpdate, ctx: RequestContext = Depends(require_admin)):
    if body.role_id is not None and not repo.get_role(ctx.db, body.role_id):
        raise HTTPException(400, "Role does not exist in this organization")
    with global_session() as c:
        if not get_membership(c, user_id, ctx.org_id):
            raise HTTPException(404, "User is not a member of this organization")
        repo.set_member_role(c, ctx.org_id, user_id, body.role_id)
    return MessageResponse(message="Member role updated")


@router.post("/{user_id}/reset-password")
async def reset_password(user_id: int, body: PasswordReset, ctx: RequestContext = Depends(require_admin)):
    if len(body.new_password) < config.PASSWORD_MIN_LENGTH:
        raise HTTPException(400, f"Password must be at least {config.PASSWORD_MIN_LENGTH} characters")
    with global_session() as c:
        if not get_membership(c, user_id, ctx.org_id):
            raise HTTPException(404, "User is not a member of this organization")
        repo.update_user_password(c, user_id, hash_password(body.new_password), must_change=1)
    return MessageResponse(message="Password reset — user must change on next login")
