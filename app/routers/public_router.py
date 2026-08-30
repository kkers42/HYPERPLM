"""
HYPERPLM — Public / fan routes (NO authentication).

This is the only unauthenticated data path in the app, so the rules are strict:

1. Resolve the team through `team_directory` (global, no RLS) to get its org_id.
   Anonymous callers have no active org, so they cannot read `teams` directly —
   its RLS policy fails closed.
2. Open a tenant_session for THAT org, so RLS scopes every read to exactly one
   tenant, exactly as it does for a logged-in member.
3. Read only rows with `visibility = 'public'`.

Result: a fan sees published lap times and results, never setups, telemetry,
CAD or checklists. Nothing here accepts a caller-supplied org_id.
"""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from .. import racing
from ..db import team_directory, tracks
from ..tenancy import global_session, tenant_session

router = APIRouter(prefix="/api/public", tags=["public"])


def _directory_row(slug_or_id: str) -> dict:
    """Global lookup: published teams only."""
    with global_session() as c:
        stmt = select(team_directory).where(team_directory.c.is_public == 1)
        if slug_or_id.isdigit():
            stmt = stmt.where(team_directory.c.team_id == int(slug_or_id))
        else:
            stmt = stmt.where(team_directory.c.slug == slug_or_id)
        row = c.execute(stmt).first()
    if not row:
        raise HTTPException(404, "Team not found")
    return dict(row._mapping)


@router.get("/teams")
async def public_teams():
    """Every published team, across all tenants — the fan browse list."""
    with global_session() as c:
        rows = c.execute(
            select(team_directory.c.team_id, team_directory.c.slug,
                   team_directory.c.name, team_directory.c.car_number,
                   team_directory.c.series, team_directory.c["class"])
            .where(team_directory.c.is_public == 1)
            .order_by(team_directory.c.car_number)
        ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/tracks")
async def public_tracks():
    """The circuit library is public reference data (global, no RLS)."""
    with global_session() as c:
        rows = c.execute(
            select(tracks.c.track_id, tracks.c.name, tracks.c.location,
                   tracks.c.length_km, tracks.c.turns, tracks.c.layout,
                   tracks.c.series_tag).order_by(tracks.c.name)
        ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/teams/{slug}")
async def public_team(slug: str):
    """A team's public page: identity, drivers, and published lap times."""
    d = _directory_row(slug)
    with tenant_session(d["org_id"]) as db:
        team = racing.get_team(db, d["team_id"])
        if not team:
            raise HTTPException(404, "Team not found")
        drivers = racing.team_drivers(db, d["team_id"])
        laps = racing.list_laps(db, d["team_id"], public_only=True, limit=25)
        sessions = [s for s in racing.list_sessions(db, d["team_id"])
                    if s["visibility"] == "public"]
    return {
        "team": {"name": d["name"], "car_number": d["car_number"],
                 "series": d["series"], "class": d["class"], "slug": d["slug"]},
        "drivers": drivers,
        "public_laps": laps,
        "sessions": sessions,
        "best_lap_ms": min((l["lap_time_ms"] for l in laps), default=None),
    }


@router.get("/teams/{slug}/setups")
async def public_team_setups(slug: str):
    """Only sheets a team chose to publish. Team-only sheets never appear."""
    d = _directory_row(slug)
    with tenant_session(d["org_id"]) as db:
        sheets = [s for s in racing.list_setups(db, team_id=d["team_id"])
                  if s["visibility"] == "public"]
    return sheets


@router.get("/leaderboard")
async def public_leaderboard(limit: int = Query(10, ge=1, le=50)):
    """Fan points leaderboard — global tables only, no tenant data involved."""
    from ..db import fan_profiles
    with global_session() as c:
        rows = c.execute(
            select(fan_profiles.c.display_name, fan_profiles.c.points)
            .order_by(fan_profiles.c.points.desc()).limit(limit)
        ).fetchall()
    return [dict(r._mapping) for r in rows]

@router.get("/laps")
async def public_laps(limit: int = Query(20, ge=1, le=100)):
    """Fastest published laps across every published team — the public board.

    Resolved team-by-team so each read still happens inside that team's own
    tenant session; there is no cross-tenant query anywhere in this app.
    """
    out = []
    with global_session() as c:
        teams = c.execute(
            select(team_directory.c.team_id, team_directory.c.org_id,
                   team_directory.c.slug, team_directory.c.name,
                   team_directory.c.car_number)
            .where(team_directory.c.is_public == 1)
        ).fetchall()
    for t in teams:
        d = dict(t._mapping)
        with tenant_session(d["org_id"]) as db:
            laps = racing.list_laps(db, d["team_id"], public_only=True, limit=5)
        for lap in laps:
            out.append({
                "team": d["name"], "slug": d["slug"], "car_number": d["car_number"],
                "lap_time_ms": lap["lap_time_ms"], "driver": lap["driver"],
                "track": lap["track"], "session_type": lap["session_type"],
                "tire_compound": lap["tire_compound"],
            })
    out.sort(key=lambda r: r["lap_time_ms"])
    return out[:limit]


@router.get("/stats")
async def public_stats():
    """Headline numbers for the public landing — no invented figures."""
    from ..db import fan_profiles, follows
    with global_session() as c:
        teams = c.execute(select(func.count()).select_from(team_directory)
                          .where(team_directory.c.is_public == 1)).scalar_one()
        circuits = c.execute(select(func.count()).select_from(tracks)).scalar_one()
        fans = c.execute(select(func.count()).select_from(fan_profiles)).scalar_one()
        follow_count = c.execute(select(func.count()).select_from(follows)).scalar_one()
    return {"published_teams": teams, "circuits": circuits,
            "fans": fans, "follows": follow_count}


@router.get("/questions")
async def public_questions():
    """Open prediction questions on published sessions. The answer key is never
    included while a question is live."""
    from .. import predictions
    return predictions.open_questions()

