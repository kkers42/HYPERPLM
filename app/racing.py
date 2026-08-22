"""
HYPERPLM — Racing domain queries (Phase 3).

One module, one responsibility (CLAUDE.md rule 3): read/write the racing tables
added in migration 0003. Everything here takes the request's TenantDB, so RLS
scopes every statement to the active org — org_id is never passed around.

The fan wall is enforced here, in one place: `visibility` is 'public' | 'team',
and `public_only=True` restricts a query to published rows. RLS guarantees
cross-tenant isolation; this column guarantees what a fan may see inside a tenant.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select

from .db import (
    cars, checklist_items, checklists, drivers, events, laps, part_usages,
    parts, run_sessions, setup_values, setups, teams, tracks,
)
from .tenancy import TenantDB


def _rows(result) -> list[dict]:
    return [dict(r._mapping) for r in result]


# ── Tracks (global reference data — seeded by migration 0003) ─────────────────
def list_tracks(db: TenantDB, series: str = "") -> list[dict]:
    stmt = select(
        tracks.c.id, tracks.c.track_id, tracks.c.name, tracks.c.location,
        tracks.c.length_km, tracks.c.turns, tracks.c.layout, tracks.c.series_tag,
    ).order_by(tracks.c.name)
    if series:
        stmt = stmt.where(tracks.c.series_tag.like(f"%{series}%"))
    return _rows(db.execute(stmt))


# ── Teams / cars / drivers ────────────────────────────────────────────────────
def list_teams(db: TenantDB) -> list[dict]:
    stmt = select(teams).order_by(teams.c.car_number)
    out = _rows(db.execute(stmt))
    for t in out:
        t["drivers"] = _rows(db.execute(
            select(drivers.c.name, drivers.c.country).where(drivers.c.team_id == t["id"])
        ))
        t["cars"] = _rows(db.execute(
            select(cars.c.chassis, cars.c.model).where(cars.c.team_id == t["id"])
        ))
    return out


def get_team(db: TenantDB, team_id: int) -> Optional[dict]:
    row = db.execute(select(teams).where(teams.c.id == team_id)).first()
    return dict(row._mapping) if row else None


# ── Sessions & lap times ──────────────────────────────────────────────────────
def list_laps(db: TenantDB, team_id: int, public_only: bool = False,
              limit: int = 100) -> list[dict]:
    """Best laps for a team. public_only=True is the fan view."""
    stmt = (
        select(
            laps.c.id, laps.c.lap_no, laps.c.lap_time_ms, laps.c.tire_compound,
            laps.c.is_pb, laps.c.is_fastest, laps.c.visibility,
            run_sessions.c.session_type, run_sessions.c.session_date,
            drivers.c.name.label("driver"),
            events.c.name.label("event"),
            tracks.c.name.label("track"),
        )
        .select_from(
            laps.join(run_sessions, run_sessions.c.id == laps.c.run_session_id)
                .join(events, events.c.id == run_sessions.c.event_id)
                .join(tracks, tracks.c.id == events.c.track_id)
                .outerjoin(drivers, drivers.c.id == laps.c.driver_id)
        )
        .where(events.c.team_id == team_id)
        .order_by(laps.c.lap_time_ms)
        .limit(limit)
    )
    if public_only:
        stmt = stmt.where(laps.c.visibility == "public",
                          run_sessions.c.visibility == "public")
    return _rows(db.execute(stmt))


def list_sessions(db: TenantDB, team_id: int) -> list[dict]:
    """Session log with lap counts and the best lap of each session."""
    stmt = (
        select(
            run_sessions.c.id, run_sessions.c.session_type,
            run_sessions.c.session_date, run_sessions.c.visibility,
            events.c.name.label("event"), tracks.c.name.label("track"),
            func.count(laps.c.id).label("lap_count"),
            func.min(laps.c.lap_time_ms).label("best_ms"),
        )
        .select_from(
            run_sessions.join(events, events.c.id == run_sessions.c.event_id)
                        .join(tracks, tracks.c.id == events.c.track_id)
                        .outerjoin(laps, laps.c.run_session_id == run_sessions.c.id)
        )
        .where(events.c.team_id == team_id)
        .group_by(run_sessions.c.id, run_sessions.c.session_type,
                  run_sessions.c.session_date, run_sessions.c.visibility,
                  events.c.name, tracks.c.name)
        .order_by(run_sessions.c.session_date)
    )
    return _rows(db.execute(stmt))


# ── Setup sheets (team-only by default — the crown-jewel IP) ─────────────────
def list_setups(db: TenantDB, team_id: Optional[int] = None) -> list[dict]:
    stmt = (
        select(setups.c.id, setups.c.setup_key, setups.c.baseline,
               setups.c.revision_label, setups.c.visibility, setups.c.notes,
               setups.c.team_id, tracks.c.name.label("track"))
        .select_from(setups.join(tracks, tracks.c.id == setups.c.track_id))
        .order_by(setups.c.created_at.desc())
    )
    if team_id:
        stmt = stmt.where(setups.c.team_id == team_id)
    return _rows(db.execute(stmt))


def get_setup(db: TenantDB, setup_key: str, public_only: bool = False) -> Optional[dict]:
    """Return a setup and its values. public_only=True (fan view) refuses
    team-only sheets — the fan wall, enforced in one place."""
    row = db.execute(
        select(setups.c.id, setups.c.setup_key, setups.c.baseline,
               setups.c.revision_label, setups.c.visibility, setups.c.notes,
               setups.c.team_id, tracks.c.name.label("track"))
        .select_from(setups.join(tracks, tracks.c.id == setups.c.track_id))
        .where(setups.c.setup_key == setup_key)
    ).first()
    if not row:
        return None
    data = dict(row._mapping)
    if public_only and data["visibility"] != "public":
        return None
    data["values"] = _rows(db.execute(
        select(setup_values.c.group_name, setup_values.c.attr_key,
               setup_values.c.attr_value, setup_values.c.unit)
        .where(setup_values.c.setup_id == data["id"])
        .order_by(setup_values.c.attr_order)
    ))
    return data


# ── Checklists ────────────────────────────────────────────────────────────────
def list_checklists(db: TenantDB, team_id: Optional[int] = None) -> list[dict]:
    stmt = select(checklists).order_by(checklists.c.created_at.desc())
    if team_id:
        stmt = stmt.where(checklists.c.team_id == team_id)
    out = _rows(db.execute(stmt))
    for c in out:
        items = _rows(db.execute(
            select(checklist_items.c.label, checklist_items.c.assigned_role,
                   checklist_items.c.is_done)
            .where(checklist_items.c.checklist_id == c["id"])
            .order_by(checklist_items.c.item_order)
        ))
        c["items"] = items
        c["done"] = sum(1 for i in items if i["is_done"])
        c["total"] = len(items)
    return out


# ── Parts & CAD: PLM parts + racing service life ──────────────────────────────
def list_part_usages(db: TenantDB, team_id: Optional[int] = None) -> list[dict]:
    """Life-limited components: a PLM part joined to its racing usage."""
    stmt = (
        select(
            part_usages.c.id, part_usages.c.hours_used, part_usages.c.hours_limit,
            part_usages.c.cycles_used, part_usages.c.cycles_limit,
            part_usages.c.status, part_usages.c.team_id,
            parts.c.part_number, parts.c.part_name, parts.c.part_revision,
            parts.c.release_status,
        )
        .select_from(part_usages.join(parts, parts.c.id == part_usages.c.part_id))
        .order_by(part_usages.c.status.desc(), part_usages.c.hours_used.desc())
    )
    if team_id:
        stmt = stmt.where(part_usages.c.team_id == team_id)
    out = _rows(db.execute(stmt))
    for p in out:
        limit = p.get("hours_limit")
        p["life_pct"] = round(100 * p["hours_used"] / limit) if limit else None
    return out


# ── Company (org) rollup for the overview page ────────────────────────────────
def org_summary(db: TenantDB) -> dict:
    team_count = db.execute(select(func.count()).select_from(teams)).scalar_one()
    lap_count = db.execute(select(func.count()).select_from(laps)).scalar_one()
    setup_count = db.execute(select(func.count()).select_from(setups)).scalar_one()
    over = db.execute(
        select(func.count()).select_from(part_usages)
        .where(part_usages.c.status.in_(("service_soon", "over")))
    ).scalar_one()
    best = db.execute(select(func.min(laps.c.lap_time_ms))).scalar()
    track_count = db.execute(select(func.count()).select_from(tracks)).scalar_one()
    return {
        "teams": team_count,
        "laps": lap_count,
        "setups": setup_count,
        "parts_needing_service": over,
        "best_lap_ms": best,
        "tracks_available": track_count,
    }
