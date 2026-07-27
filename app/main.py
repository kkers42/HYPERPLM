"""
HYPERPLM — FastAPI application entry point.

Thin: config validation, DB connectivity check, middleware, router wiring, and the
static page routes. All functionality lives in modules (CLAUDE.md rule 3). The schema
is managed by Alembic migrations, not created at startup.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import config
from .deps import optional_ctx
from .security import SecurityHeadersMiddleware
from .routers import (
    admin_router,
    auth_router,
    documents_router,
    orgs_router,
    parts_router,
    relationships_router,
    users_router,
)

_STATIC = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast on insecure production config; warn in development.
    for warning in config.validate():
        print(f"[HYPERPLM] CONFIG WARNING: {warning}")
    # Verify the database is reachable (schema is applied via Alembic migrations).
    try:
        from .db import get_engine
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        print("[HYPERPLM] database connection OK")
    except Exception as e:  # noqa: BLE001 — log and continue so the login page still serves
        print(f"[HYPERPLM] WARNING: database not reachable at startup: {e}")
    yield


app = FastAPI(title="HYPERPLM", version="1.0.0", docs_url="/api/docs", redoc_url=None,
              lifespan=lifespan)

app.add_middleware(SecurityHeadersMiddleware)

app.include_router(auth_router.router)
app.include_router(orgs_router.router)
app.include_router(parts_router.router)
app.include_router(relationships_router.router)
app.include_router(documents_router.router)
app.include_router(users_router.router)
app.include_router(admin_router.router)


# ── Pages ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root(principal=Depends(optional_ctx)):
    return RedirectResponse(url="/app" if principal else "/login")


@app.get("/login")
async def login_page():
    return FileResponse(str(_STATIC / "index.html"))


@app.get("/app")
async def app_page():
    return FileResponse(str(_STATIC / "app.html"))


@app.get("/api/auth-mode")
async def auth_mode():
    return {"mode": config.AUTH_MODE}


@app.get("/api/features")
async def features():
    return {
        "open_inplace": bool(config.FILES_UNC_ROOT),
        "mapped_drive": config.FILES_MAPPED_DRIVE or None,
    }


@app.get("/plmopen-handler.reg")
async def plmopen_reg():
    """Download once per workstation to register the plmopen:// URI scheme handler."""
    reg_content = r"""Windows Registry Editor Version 5.00

[HKEY_CLASSES_ROOT\plmopen]
@="PLM Open in CAD"
"URL Protocol"=""

[HKEY_CLASSES_ROOT\plmopen\DefaultIcon]
@="shell32.dll,3"

[HKEY_CLASSES_ROOT\plmopen\shell]

[HKEY_CLASSES_ROOT\plmopen\shell\open]

[HKEY_CLASSES_ROOT\plmopen\shell\open\command]
@="cmd.exe /v:on /c \"set P=%1& set P=!P:plmopen://=! & start \"\" \"!P!\""
"""
    return PlainTextResponse(
        content=reg_content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="plmopen-handler.reg"'},
    )


app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
