"""
HYPERPLM — Admin routes for the active org: roles, audit log, attribute keys.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from .. import repo
from ..deps import RequestContext, require_admin
from ..models import MessageResponse, RoleCreate, RoleUpdate

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── Roles (per-org) ───────────────────────────────────────────────────────────

@router.get("/roles")
async def list_roles(ctx: RequestContext = Depends(require_admin)):
    return repo.list_roles(ctx.db)


@router.post("/roles", status_code=201)
async def create_role(body: RoleCreate, ctx: RequestContext = Depends(require_admin)):
    if repo.get_role_by_name(ctx.db, body.name):
        raise HTTPException(409, "Role name already exists")
    return repo.create_role(ctx.db, body.name, body.model_dump())


@router.put("/roles/{role_id}")
async def update_role(role_id: int, body: RoleUpdate, ctx: RequestContext = Depends(require_admin)):
    if not repo.get_role(ctx.db, role_id):
        raise HTTPException(404, "Role not found")
    repo.update_role(ctx.db, role_id, body.name, body.model_dump())
    return repo.get_role(ctx.db, role_id)


@router.delete("/roles/{role_id}")
async def delete_role(role_id: int, ctx: RequestContext = Depends(require_admin)):
    role = repo.get_role(ctx.db, role_id)
    if not role:
        raise HTTPException(404, "Role not found")
    if role["name"] in ("Owner", "Admin"):
        raise HTTPException(400, f"Cannot delete the {role['name']} role")
    repo.delete_role(ctx.db, role_id)
    return MessageResponse(message="Role deleted")


# ── Audit log ─────────────────────────────────────────────────────────────────

@router.get("/audit")
async def get_audit(
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    entity_type: str = Query(""),
    ctx: RequestContext = Depends(require_admin),
):
    return repo.get_audit_log(ctx.db, page=page, per_page=per_page, entity_type=entity_type)


# ── Attribute keys ─────────────────────────────────────────────────────────────

@router.get("/attributes/keys")
async def attribute_keys(ctx: RequestContext = Depends(require_admin)):
    return repo.list_attribute_keys(ctx.db)
