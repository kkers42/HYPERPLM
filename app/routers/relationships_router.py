"""
HYPERPLM — Relationships routes (tenant-scoped).
"""
from fastapi import APIRouter, Depends, HTTPException

from .. import repo
from ..deps import RequestContext, require_ability
from ..models import MessageResponse, RelationshipCreate

router = APIRouter(prefix="/api/relationships", tags=["relationships"])


@router.get("")
async def list_relationships(ctx: RequestContext = Depends(require_ability("view"))):
    return repo.list_all_relationships(ctx.db)


@router.post("", status_code=201)
async def add_relationship(body: RelationshipCreate, ctx: RequestContext = Depends(require_ability("write"))):
    if body.parent_part_id == body.child_part_id:
        raise HTTPException(400, "Parent and child cannot be the same part")
    if not repo.get_part(ctx.db, body.parent_part_id):
        raise HTTPException(404, "Parent part not found")
    if not repo.get_part(ctx.db, body.child_part_id):
        raise HTTPException(404, "Child part not found")
    if repo.relationship_exists(ctx.db, body.parent_part_id, body.child_part_id):
        raise HTTPException(409, "Relationship already exists")
    return repo.add_relationship(
        ctx.db, parent_id=body.parent_part_id, child_id=body.child_part_id,
        quantity=body.quantity, rel_type=body.relationship_type, notes=body.notes,
        user_id=ctx.user["id"],
    )


@router.delete("/{rel_id}")
async def delete_relationship(rel_id: int, ctx: RequestContext = Depends(require_ability("write"))):
    repo.delete_relationship(ctx.db, rel_id, ctx.user["id"])
    return MessageResponse(message="Relationship removed")


@router.get("/tree/{part_id}")
async def get_tree(part_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    if not repo.get_part(ctx.db, part_id):
        raise HTTPException(404, "Part not found")
    return repo.get_tree(ctx.db, part_id)
