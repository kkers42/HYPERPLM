"""
HYPERPLM — Racing routes (tenant-scoped via RequestContext / RLS).

Mirrors the parts/documents router pattern: every endpoint takes a
RequestContext from require_ability(), so RLS scopes the query to the active org
and the role gates the action. The racing-specific rule on top of that is the
fan wall — `visibility` decides what leaves the garage (see app/racing.py).
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from .. import racing
from ..deps import RequestContext, require_ability

router = APIRouter(prefix="/api/racing", tags=["racing"])


@router.get("/summary")
async def summary(ctx: RequestContext = Depends(require_ability("view"))):
    """Company-level rollup for the racing overview."""
    return racing.org_summary(ctx.db)


@router.get("/tracks")
async def list_tracks(
    series: str = Query("", description="filter, e.g. 'IMSA' or 'IndyCar'"),
    ctx: RequestContext = Depends(require_ability("view")),
):
    """The circuit library (shared reference data seeded by migration 0003)."""
    return racing.list_tracks(ctx.db, series=series)


@router.get("/teams")
async def list_teams(ctx: RequestContext = Depends(require_ability("view"))):
    return racing.list_teams(ctx.db)


@router.get("/teams/{team_id}/laps")
async def team_laps(
    team_id: int,
    public_only: bool = Query(False, description="fan view: published laps only"),
    ctx: RequestContext = Depends(require_ability("view")),
):
    if not racing.get_team(ctx.db, team_id):
        raise HTTPException(404, "Team not found")
    return racing.list_laps(ctx.db, team_id, public_only=public_only)


@router.get("/teams/{team_id}/sessions")
async def team_sessions(team_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    if not racing.get_team(ctx.db, team_id):
        raise HTTPException(404, "Team not found")
    return racing.list_sessions(ctx.db, team_id)


@router.get("/setups")
async def list_setups(
    team_id: int | None = Query(None),
    ctx: RequestContext = Depends(require_ability("view")),
):
    return racing.list_setups(ctx.db, team_id=team_id)


@router.get("/setups/{setup_key}")
async def get_setup(
    setup_key: str,
    public_only: bool = Query(False, description="fan view: refuses team-only sheets"),
    ctx: RequestContext = Depends(require_ability("view")),
):
    setup = racing.get_setup(ctx.db, setup_key, public_only=public_only)
    if not setup:
        # Same 404 whether it is missing or team-only: a fan learns nothing
        # about the existence of a private sheet.
        raise HTTPException(404, "Setup not found")
    return setup


@router.get("/checklists")
async def list_checklists(
    team_id: int | None = Query(None),
    ctx: RequestContext = Depends(require_ability("view")),
):
    return racing.list_checklists(ctx.db, team_id=team_id)


@router.get("/parts")
async def list_part_usages(
    team_id: int | None = Query(None),
    ctx: RequestContext = Depends(require_ability("view")),
):
    """Life-limited components: PLM parts joined to their racing service life."""
    return racing.list_part_usages(ctx.db, team_id=team_id)


# ── Series & setup templates (Phase 3, step 7) ───────────────────────────────

@router.get("/series")
async def list_series(ctx: RequestContext = Depends(require_ability("view"))):
    """The championships a team can run in — reference data, not per-org."""
    return racing.list_series(ctx.db)


@router.get("/series/{series_row_id}/tracks")
async def series_tracks(series_row_id: int,
                        ctx: RequestContext = Depends(require_ability("view"))):
    """Circuits this series visits, so picking a series narrows the track list."""
    return racing.tracks_for_series(ctx.db, series_row_id)


@router.get("/templates")
async def list_templates(ctx: RequestContext = Depends(require_ability("view"))):
    """This org's setup templates, plus the built-in starter field lists it can
    instantiate if it has none yet."""
    return {"templates": racing.list_templates(ctx.db),
            "starters": list(racing.STARTER_TEMPLATES.keys())}


@router.get("/cars")
async def list_cars(team_id: int | None = Query(None),
                    ctx: RequestContext = Depends(require_ability("view"))):
    return racing.list_cars(ctx.db, team_id=team_id)


@router.get("/cars/{car_id}")
async def car_dossier(car_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    """Everything that hangs off one car: what is fitted, what it has run, and
    how it was set up."""
    d = racing.car_dossier(ctx.db, car_id)
    if not d:
        raise HTTPException(404, "Car not found")
    return d

