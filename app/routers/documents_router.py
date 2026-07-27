"""
HYPERPLM — Documents routes (tenant-scoped).
"""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from .. import config, repo
from ..deps import RequestContext, require_ability
from ..files import get_file_path, restore_version, save_upload
from ..models import MessageResponse

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("")
async def list_documents(part_id: Optional[int] = Query(None),
                         ctx: RequestContext = Depends(require_ability("view"))):
    return repo.list_documents(ctx.db, part_id)


@router.post("", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    part_id: Optional[int] = Form(None),
    description: str = Form(""),
    ctx: RequestContext = Depends(require_ability("upload")),
):
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    return await save_upload(
        file_data=data, filename=file.filename, part_id=part_id,
        description=description, user_id=ctx.user["id"], db=ctx.db,
    )


@router.get("/{doc_id}/download")
async def download_document(doc_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    doc = repo.get_document(ctx.db, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    try:
        fpath = get_file_path(doc["stored_path"])
    except PermissionError:
        raise HTTPException(400, "Invalid file path")
    if not fpath.exists():
        raise HTTPException(404, "File not found on disk")
    return FileResponse(path=str(fpath), filename=doc["filename"])


@router.get("/{doc_id}/open")
async def open_document_inplace(doc_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    """Return the plmopen:// URI so a LAN client can open the file in place (see main.py)."""
    if not config.FILES_UNC_ROOT:
        raise HTTPException(501, "Open-in-place not configured (FILES_UNC_ROOT not set)")
    doc = repo.get_document(ctx.db, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    stored = Path(doc["stored_path"])
    files_root = config.FILES_ROOT.resolve()
    try:
        rel = stored.resolve().relative_to(files_root)
    except ValueError:
        raise HTTPException(400, "Stored path is outside FILES_ROOT")
    rel_parts = str(rel).replace("/", "\\")
    if config.FILES_MAPPED_DRIVE:
        drive = config.FILES_MAPPED_DRIVE.rstrip("\\").rstrip(":")
        file_path = f"{drive}:\\{rel_parts}"
    else:
        unc_root = config.FILES_UNC_ROOT.rstrip("/").rstrip("\\")
        file_path = f"{unc_root}\\{rel_parts}"
    return {"uri": f"plmopen://{file_path}", "path": file_path}


@router.get("/{doc_id}")
async def get_document(doc_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    doc = repo.get_document(ctx.db, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


@router.delete("/{doc_id}")
async def delete_document(doc_id: int, ctx: RequestContext = Depends(require_ability("write"))):
    stored_path = repo.delete_document(ctx.db, doc_id, ctx.user["id"])
    if stored_path:
        p = Path(stored_path)
        if p.exists():
            p.unlink(missing_ok=True)
    return MessageResponse(message="Document deleted")


@router.get("/{doc_id}/versions")
async def list_versions(doc_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    return repo.list_file_versions(ctx.db, doc_id)


@router.post("/{doc_id}/restore/{version_id}")
async def restore_doc_version(doc_id: int, version_id: int,
                              ctx: RequestContext = Depends(require_ability("write"))):
    try:
        await restore_version(doc_id, version_id, ctx.user["id"], ctx.db)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return MessageResponse(message="Version restored")
