"""
HYPERPLM — Parts routes (tenant-scoped via RequestContext / RLS).
"""
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import repo
from ..deps import RequestContext, require_ability, require_admin
from ..export import generate_bom_excel
from ..models import (
    AttributeSet, CheckoutRequest, MessageResponse,
    PartCreate, PartUpdate, RevisionCreate,
)

router = APIRouter(prefix="/api/parts", tags=["parts"])


@router.get("")
async def list_parts(
    search: str = Query(""),
    status: str = Query(""),
    checked_out_only: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    ctx: RequestContext = Depends(require_ability("view")),
):
    return repo.list_parts(ctx.db, search=search, status=status,
                           checked_out_only=checked_out_only, page=page, per_page=per_page)


@router.post("", status_code=201)
async def create_part(body: PartCreate, ctx: RequestContext = Depends(require_ability("write"))):
    if repo.get_part_by_number(ctx.db, body.part_number):
        raise HTTPException(409, f"Part number '{body.part_number}' already exists")
    return repo.create_part(ctx.db, body.model_dump(), ctx.user["id"])


@router.get("/{part_id}")
async def get_part(part_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    part = repo.get_part(ctx.db, part_id)
    if not part:
        raise HTTPException(404, "Part not found")
    return part


@router.put("/{part_id}")
async def update_part(part_id: int, body: PartUpdate, ctx: RequestContext = Depends(require_ability("write"))):
    part = repo.get_part(ctx.db, part_id)
    if not part:
        raise HTTPException(404, "Part not found")
    if part.get("is_locked"):
        raise HTTPException(423, "Part is locked (Released). Use 'Unreleased' to edit.")
    repo.update_part(ctx.db, part_id, body.model_dump(), ctx.user["id"])
    return repo.get_part(ctx.db, part_id)


@router.delete("/{part_id}")
async def delete_part(part_id: int, ctx: RequestContext = Depends(require_admin)):
    if not repo.get_part(ctx.db, part_id):
        raise HTTPException(404, "Part not found")
    repo.delete_part(ctx.db, part_id, ctx.user["id"])
    return MessageResponse(message="Part deleted")


@router.post("/{part_id}/checkout")
async def checkout(part_id: int, body: CheckoutRequest, ctx: RequestContext = Depends(require_ability("checkout"))):
    if not repo.get_part(ctx.db, part_id):
        raise HTTPException(404, "Part not found")
    if not repo.checkout_part(ctx.db, part_id, ctx.user["id"], body.station):
        raise HTTPException(409, "Part is already checked out")
    return repo.get_part(ctx.db, part_id)


@router.post("/{part_id}/checkin")
async def checkin(part_id: int, ctx: RequestContext = Depends(require_ability("checkout"))):
    part = repo.get_part(ctx.db, part_id)
    if not part:
        raise HTTPException(404, "Part not found")
    if part.get("checked_out_by") != ctx.user["id"] and not ctx.abilities.get("can_admin"):
        raise HTTPException(403, "You can only check in parts you have checked out")
    repo.checkin_part(ctx.db, part_id, ctx.user["id"])
    return repo.get_part(ctx.db, part_id)


@router.post("/{part_id}/release")
async def release_part(part_id: int, ctx: RequestContext = Depends(require_ability("release"))):
    if not repo.get_part(ctx.db, part_id):
        raise HTTPException(404, "Part not found")
    repo.release_part(ctx.db, part_id, ctx.user["id"])
    return repo.get_part(ctx.db, part_id)


@router.post("/{part_id}/unreleased")
async def unrelease_part(part_id: int, ctx: RequestContext = Depends(require_ability("release"))):
    if not repo.get_part(ctx.db, part_id):
        raise HTTPException(404, "Part not found")
    repo.unrelease_part(ctx.db, part_id, ctx.user["id"])
    return repo.get_part(ctx.db, part_id)


@router.post("/{part_id}/revise")
async def bump_revision(part_id: int, body: RevisionCreate, ctx: RequestContext = Depends(require_ability("write"))):
    if not repo.get_part(ctx.db, part_id):
        raise HTTPException(404, "Part not found")
    new_rev = repo.bump_revision(ctx.db, part_id, ctx.user["id"], body.description)
    return {"message": f"Revision bumped to {new_rev}", "new_revision": new_rev}


@router.get("/{part_id}/revisions")
async def list_revisions(part_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    return repo.list_revisions(ctx.db, part_id)


@router.get("/{part_id}/attributes")
async def get_attributes(part_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    return repo.get_attributes(ctx.db, part_id)


@router.put("/{part_id}/attributes")
async def set_attribute(part_id: int, body: AttributeSet, ctx: RequestContext = Depends(require_ability("write"))):
    if not repo.get_part(ctx.db, part_id):
        raise HTTPException(404, "Part not found")
    repo.set_attribute(ctx.db, part_id, body.key, body.value, body.order)
    return repo.get_attributes(ctx.db, part_id)


@router.delete("/{part_id}/attributes/{key}")
async def delete_attribute(part_id: int, key: str, ctx: RequestContext = Depends(require_ability("write"))):
    repo.delete_attribute(ctx.db, part_id, key)
    return MessageResponse(message="Attribute removed")


@router.get("/{part_id}/where-used")
async def where_used(part_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    return repo.get_parents(ctx.db, part_id)


@router.get("/{part_id}/bom")
async def get_bom(part_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    part = repo.get_part(ctx.db, part_id)
    if not part:
        raise HTTPException(404, "Part not found")
    return {"root": part, "items": repo.get_bom_flat(ctx.db, part_id)}


@router.get("/{part_id}/bom/export")
async def export_bom(part_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    part = repo.get_part(ctx.db, part_id)
    if not part:
        raise HTTPException(404, "Part not found")
    xlsx_bytes = generate_bom_excel(part, repo.get_bom_flat(ctx.db, part_id))
    filename = f"BOM_{part['part_number']}_Rev{part['part_revision']}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{part_id}/documents")
async def list_part_docs(part_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    return repo.list_documents(ctx.db, part_id)


@router.post("/{part_id}/documents/{doc_id}")
async def attach_doc(part_id: int, doc_id: int, ctx: RequestContext = Depends(require_ability("write"))):
    if not repo.get_part(ctx.db, part_id):
        raise HTTPException(404, "Part not found")
    if not repo.get_document(ctx.db, doc_id):
        raise HTTPException(404, "Document not found")
    repo.attach_document(ctx.db, part_id, doc_id)
    return MessageResponse(message="Document attached")


@router.delete("/{part_id}/documents/{doc_id}")
async def detach_doc(part_id: int, doc_id: int, ctx: RequestContext = Depends(require_ability("write"))):
    repo.detach_document(ctx.db, doc_id)
    return MessageResponse(message="Document detached")
