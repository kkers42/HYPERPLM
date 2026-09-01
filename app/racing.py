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


def team_drivers(db: TenantDB, team_id: int) -> list[dict]:
    return _rows(db.execute(
        select(drivers.c.name, drivers.c.country).where(drivers.c.team_id == team_id)
    ))


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
            run_sessions.c.id, run_sessions.c.event_id, run_sessions.c.session_type,
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
        .group_by(run_sessions.c.id, run_sessions.c.event_id, run_sessions.c.session_type,
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
            select(checklist_items.c.id, checklist_items.c.label,
                   checklist_items.c.assigned_role, checklist_items.c.is_done)
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


# ══ WRITES ════════════════════════════════════════════════════════════════════
# Every insert sets org_id explicitly (RLS WITH CHECK requires it to match the
# active org, so a cross-tenant write is rejected by the database, not by us).

from sqlalchemy import delete, insert, update  # noqa: E402

from .db import team_directory  # noqa: E402


def create_team(db: TenantDB, data: dict) -> dict:
    row = db.execute(insert(teams).values({
        "org_id": db.org_id, "team_key": data["team_key"], "name": data["name"],
        "series": data.get("series", ""), "class": data.get("class", ""),
        "car_number": data.get("car_number", ""),
    }).returning(teams)).first()
    return dict(row._mapping)


def create_car(db: TenantDB, data: dict) -> dict:
    row = db.execute(insert(cars).values(
        org_id=db.org_id, team_id=data["team_id"], chassis=data["chassis"],
        model=data.get("model", ""), homologation=data.get("homologation", ""),
    ).returning(cars)).first()
    return dict(row._mapping)


def create_driver(db: TenantDB, data: dict) -> dict:
    row = db.execute(insert(drivers).values(
        org_id=db.org_id, team_id=data["team_id"], name=data["name"],
        country=data.get("country", ""),
    ).returning(drivers)).first()
    return dict(row._mapping)


def create_event(db: TenantDB, data: dict) -> dict:
    row = db.execute(insert(events).values(
        org_id=db.org_id, team_id=data["team_id"], track_id=data["track_id"],
        name=data["name"], round=data.get("round"), starts_on=data.get("starts_on"),
        series_id=data.get("series_id"),
    ).returning(events)).first()
    return dict(row._mapping)


def create_session(db: TenantDB, data: dict, user_id: int) -> dict:
    row = db.execute(insert(run_sessions).values(
        org_id=db.org_id, event_id=data["event_id"], car_id=data.get("car_id"),
        session_type=data["session_type"], session_date=data.get("session_date"),
        visibility=data.get("visibility", "public"), created_by=user_id,
    ).returning(run_sessions)).first()
    return dict(row._mapping)


def add_lap(db: TenantDB, data: dict) -> dict:
    """Log a lap, then recompute PB/fastest flags for the whole session."""
    row = db.execute(insert(laps).values(
        org_id=db.org_id, run_session_id=data["run_session_id"],
        lap_no=data["lap_no"], lap_time_ms=data["lap_time_ms"],
        driver_id=data.get("driver_id"), tire_compound=data.get("tire_compound", ""),
        visibility=data.get("visibility", "public"),
    ).returning(laps)).first()
    _reflag_session(db, data["run_session_id"])
    return dict(row._mapping)


def _reflag_session(db: TenantDB, run_session_id: int) -> None:
    best = db.execute(
        select(func.min(laps.c.lap_time_ms)).where(laps.c.run_session_id == run_session_id)
    ).scalar()
    db.execute(update(laps).where(laps.c.run_session_id == run_session_id)
               .values(is_fastest=0, is_pb=0))
    if best is not None:
        db.execute(update(laps).where(laps.c.run_session_id == run_session_id,
                                      laps.c.lap_time_ms == best).values(is_fastest=1, is_pb=1))


def set_session_visibility(db: TenantDB, session_id: int, visibility: str) -> None:
    """Publishing a session publishes its laps with it."""
    db.execute(update(run_sessions).where(run_sessions.c.id == session_id)
               .values(visibility=visibility))
    db.execute(update(laps).where(laps.c.run_session_id == session_id)
               .values(visibility=visibility))


def create_setup(db: TenantDB, data: dict, user_id: int) -> dict:
    row = db.execute(insert(setups).values(
        org_id=db.org_id, setup_key=data["setup_key"], team_id=data["team_id"],
        track_id=data["track_id"], run_session_id=data.get("run_session_id"),
        car_id=data.get("car_id"),
        baseline=data.get("baseline", ""), revision_label=data.get("revision_label", "A"),
        visibility=data.get("visibility", "team"), engineer_id=user_id,
        notes=data.get("notes", ""),
    ).returning(setups)).first()
    return dict(row._mapping)


def set_setup_value(db: TenantDB, setup_id: int, key: str, value: str,
                    group: str = "", unit: str = "", order: int = 0) -> None:
    existing = db.execute(select(setup_values.c.id).where(
        setup_values.c.setup_id == setup_id, setup_values.c.attr_key == key)).first()
    if existing:
        db.execute(update(setup_values).where(setup_values.c.id == existing[0])
                   .values(attr_value=value, unit=unit, group_name=group))
    else:
        db.execute(insert(setup_values).values(
            org_id=db.org_id, setup_id=setup_id, group_name=group,
            attr_key=key, attr_value=value, unit=unit, attr_order=order))


def clone_setup(db: TenantDB, setup_key: str, new_key: str, user_id: int) -> Optional[dict]:
    """Copy a sheet and its values — how a new revision actually gets made."""
    src = get_setup(db, setup_key)
    if not src:
        return None
    new = create_setup(db, {
        "setup_key": new_key, "team_id": src["team_id"],
        "track_id": db.execute(select(setups.c.track_id)
                               .where(setups.c.id == src["id"])).scalar(),
        "baseline": src["baseline"] or src["setup_key"],
        "revision_label": src["revision_label"], "visibility": src["visibility"],
        "notes": src["notes"],
    }, user_id)
    for i, v in enumerate(src["values"]):
        set_setup_value(db, new["id"], v["attr_key"], v["attr_value"],
                        v["group_name"], v["unit"], i)
    return new


def set_setup_visibility(db: TenantDB, setup_id: int, visibility: str) -> None:
    db.execute(update(setups).where(setups.c.id == setup_id).values(visibility=visibility))


def toggle_checklist_item(db: TenantDB, item_id: int, done: bool, user_id: int) -> None:
    db.execute(update(checklist_items).where(checklist_items.c.id == item_id).values(
        is_done=1 if done else 0,
        signed_by=user_id if done else None,
        signed_at=func.now() if done else None,
    ))


def create_checklist(db: TenantDB, data: dict) -> dict:
    row = db.execute(insert(checklists).values(
        org_id=db.org_id, team_id=data["team_id"], name=data["name"],
        is_template=1 if data.get("is_template") else 0, event_id=data.get("event_id"),
    ).returning(checklists)).first()
    cl = dict(row._mapping)
    for i, label in enumerate(data.get("items", [])):
        db.execute(insert(checklist_items).values(
            org_id=db.org_id, checklist_id=cl["id"], label=label, item_order=i))
    return cl


def log_part_hours(db: TenantDB, part_usage_id: int, hours_used: float) -> None:
    """status is derived by a DB trigger (migration 0005), never passed in."""
    db.execute(update(part_usages).where(part_usages.c.id == part_usage_id)
               .values(hours_used=hours_used))


def track_part(db: TenantDB, data: dict) -> dict:
    row = db.execute(insert(part_usages).values(
        org_id=db.org_id, part_id=data["part_id"], team_id=data["team_id"],
        car_id=data.get("car_id"), hours_used=data.get("hours_used", 0),
        hours_limit=data.get("hours_limit"), cycles_limit=data.get("cycles_limit"),
    ).returning(part_usages)).first()
    return dict(row._mapping)


def set_team_public(db: TenantDB, team_id: int, is_public: bool) -> None:
    """Publish/unpublish a team to the fan side. team_directory is global, so
    this is the one racing write that touches a non-RLS table — the team_id is
    resolved inside the tenant session first, so you can only publish your own."""
    own = db.execute(select(teams.c.id).where(teams.c.id == team_id)).first()
    if not own:
        raise PermissionError("team not in this organization")
    db.execute(update(team_directory).where(team_directory.c.team_id == team_id)
               .values(is_public=1 if is_public else 0))


# ══ SERIES & SETUP TEMPLATES (Phase 3, step 7) ════════════════════════════════
# Raised in testing: sessions should let you pick a series and event rather than
# retyping strings, and "IndyCar will be different than IMSA" — so a team needs
# its own setup field lists, not one hardcoded shape.

from .db import series as series_tbl  # noqa: E402
from .db import setup_template_fields, setup_templates  # noqa: E402

# Starter field lists offered when a team has no template yet. These are real
# engineering fields for each discipline, not sample data — a team can take one
# and edit it rather than starting from a blank sheet.
STARTER_TEMPLATES = {
    "GT3 / sports car": [
        ("Corner weights", "LF", "kg"), ("Corner weights", "RF", "kg"),
        ("Corner weights", "LR", "kg"), ("Corner weights", "RR", "kg"),
        ("Corner weights", "Cross", "%"), ("Corner weights", "Total w/ driver", "kg"),
        ("Springs & dampers", "Spring front", "N/mm"),
        ("Springs & dampers", "Spring rear", "N/mm"),
        ("Springs & dampers", "Bump LS front", "clk"),
        ("Springs & dampers", "Bump LS rear", "clk"),
        ("Springs & dampers", "Rebound LS front", "clk"),
        ("Springs & dampers", "Rebound LS rear", "clk"),
        ("Springs & dampers", "ARB front", ""), ("Springs & dampers", "ARB rear", ""),
        ("Alignment", "Camber LF", "deg"), ("Alignment", "Camber RF", "deg"),
        ("Alignment", "Camber LR", "deg"), ("Alignment", "Camber RR", "deg"),
        ("Alignment", "Toe front", "mm"), ("Alignment", "Toe rear", "mm"),
        ("Alignment", "Caster", "deg"),
        ("Ride & aero", "Ride height front", "mm"), ("Ride & aero", "Ride height rear", "mm"),
        ("Ride & aero", "Rake", "mm"), ("Ride & aero", "Rear wing", ""),
        ("Ride & aero", "Front splitter", ""),
        ("Brakes & diff", "Brake bias", "%F"), ("Brakes & diff", "Brake ducts", "%"),
        ("Brakes & diff", "Diff preload", "Nm"),
        ("Tires", "Compound", ""), ("Tires", "Cold pressure LF", "bar"),
        ("Tires", "Cold pressure RF", "bar"), ("Tires", "Cold pressure LR", "bar"),
        ("Tires", "Cold pressure RR", "bar"), ("Tires", "Target hot", "bar"),
        ("Conditions", "Air temp", "C"), ("Conditions", "Track temp", "C"),
        ("Conditions", "Fuel load", "L"),
    ],
    "IndyCar / open wheel": [
        ("Corner weights", "LF", "lb"), ("Corner weights", "RF", "lb"),
        ("Corner weights", "LR", "lb"), ("Corner weights", "RR", "lb"),
        ("Corner weights", "Cross", "%"),
        ("Springs & bars", "Spring front", "lb/in"), ("Springs & bars", "Spring rear", "lb/in"),
        ("Springs & bars", "Third spring", "lb/in"),
        ("Springs & bars", "Front bar", ""), ("Springs & bars", "Rear bar", ""),
        ("Dampers", "Bump front", "clk"), ("Dampers", "Bump rear", "clk"),
        ("Dampers", "Rebound front", "clk"), ("Dampers", "Rebound rear", "clk"),
        ("Alignment", "Camber LF", "deg"), ("Alignment", "Camber RF", "deg"),
        ("Alignment", "Toe front", "in"), ("Alignment", "Toe rear", "in"),
        ("Alignment", "Stagger", "in"),
        ("Aero", "Front wing angle", "deg"), ("Aero", "Rear wing angle", "deg"),
        ("Aero", "Wicker", "in"), ("Aero", "Ride height front", "in"),
        ("Aero", "Ride height rear", "in"),
        ("Weight jacker", "Weight jacker", "turns"),
        ("Brakes & diff", "Brake bias", "%F"), ("Brakes & diff", "Anti-roll setting", ""),
        ("Tires", "Compound", ""), ("Tires", "Cold pressure LF", "psi"),
        ("Tires", "Cold pressure RF", "psi"), ("Tires", "Cold pressure LR", "psi"),
        ("Tires", "Cold pressure RR", "psi"),
        ("Powertrain", "Boost", "kPa"), ("Powertrain", "Fuel load", "gal"),
        ("Conditions", "Air temp", "F"), ("Conditions", "Track temp", "F"),
    ],
}


def list_series(db: TenantDB) -> list[dict]:
    return _rows(db.execute(select(series_tbl).order_by(series_tbl.c.name)))


def tracks_for_series(db: TenantDB, series_row_id: int) -> list[dict]:
    """The circuits a series actually visits — derived from the tag we already
    hold on each track, rather than an invented calendar."""
    tag = db.execute(select(series_tbl.c.track_tag)
                     .where(series_tbl.c.id == series_row_id)).scalar()
    if not tag:
        return list_tracks(db)
    return list_tracks(db, series=tag)


def list_templates(db: TenantDB) -> list[dict]:
    out = _rows(db.execute(
        select(setup_templates.c.id, setup_templates.c.name,
               setup_templates.c.description, setup_templates.c.series_id,
               series_tbl.c.name.label("series"))
        .select_from(setup_templates.outerjoin(
            series_tbl, series_tbl.c.id == setup_templates.c.series_id))
        .order_by(setup_templates.c.name)))
    for t in out:
        t["fields"] = _rows(db.execute(
            select(setup_template_fields.c.id, setup_template_fields.c.group_name,
                   setup_template_fields.c.attr_key, setup_template_fields.c.unit,
                   setup_template_fields.c.default_value)
            .where(setup_template_fields.c.template_id == t["id"])
            .order_by(setup_template_fields.c.field_order)))
    return out


def create_template(db: TenantDB, name: str, fields: list[dict],
                    series_row_id: Optional[int] = None,
                    description: str = "", user_id: Optional[int] = None) -> dict:
    row = db.execute(insert(setup_templates).values(
        org_id=db.org_id, name=name, series_id=series_row_id,
        description=description, created_by=user_id).returning(setup_templates)).first()
    tpl = dict(row._mapping)
    for i, f in enumerate(fields):
        db.execute(insert(setup_template_fields).values(
            org_id=db.org_id, template_id=tpl["id"],
            group_name=f.get("group_name", ""), attr_key=f["attr_key"],
            unit=f.get("unit", ""), default_value=f.get("default_value", ""),
            field_order=i))
    return tpl


def create_starter_template(db: TenantDB, kind: str, series_row_id: Optional[int] = None,
                            user_id: Optional[int] = None) -> dict:
    """Instantiate one of the built-in field lists so a team can edit rather than
    invent. The rows become the team's own — nothing is shared or locked."""
    spec = STARTER_TEMPLATES.get(kind)
    if not spec:
        raise ValueError(f"Unknown starter template: {kind}")
    fields = [{"group_name": g, "attr_key": k, "unit": u} for g, k, u in spec]
    return create_template(db, kind, fields, series_row_id,
                           f"Starter field list for {kind}", user_id)


def apply_template(db: TenantDB, setup_id: int, template_id: int) -> int:
    """Copy a template's fields onto a sheet as empty values, ready to fill."""
    fields = _rows(db.execute(
        select(setup_template_fields.c.group_name, setup_template_fields.c.attr_key,
               setup_template_fields.c.unit, setup_template_fields.c.default_value)
        .where(setup_template_fields.c.template_id == template_id)
        .order_by(setup_template_fields.c.field_order)))
    for i, f in enumerate(fields):
        set_setup_value(db, setup_id, f["attr_key"], f["default_value"],
                        f["group_name"], f["unit"], i)
    db.execute(update(setups).where(setups.c.id == setup_id)
               .values(template_id=template_id))
    return len(fields)


# ══ IMPORT (testing-notes follow-up) ══════════════════════════════════════════
# Applying a parsed spreadsheet. Parsing lives in app/importer.py; this is the
# part that writes, so it stays with the rest of the racing writes.

def import_laps(db: TenantDB, run_session_id: int, rows: list[dict]) -> dict:
    """Bulk-insert laps into one session, resolving driver names to this team's
    drivers where they match. Unknown driver names are kept on the lap as-is
    rather than silently dropped, so nothing is lost in translation."""
    sess = db.execute(select(run_sessions.c.id, run_sessions.c.event_id,
                             run_sessions.c.visibility)
                      .where(run_sessions.c.id == run_session_id)).first()
    if not sess:
        raise ValueError("Session not found")
    team_id = db.execute(select(events.c.team_id)
                         .where(events.c.id == sess._mapping["event_id"])).scalar()
    known = {d["name"].strip().lower(): d["id"] for d in _rows(db.execute(
        select(drivers.c.id, drivers.c.name).where(drivers.c.team_id == team_id)))}

    inserted, unmatched = 0, set()
    for r in rows:
        did = None
        nm = (r.get("driver_name") or "").strip().lower()
        if nm:
            did = known.get(nm)
            if did is None:
                unmatched.add(r["driver_name"].strip())
        db.execute(insert(laps).values(
            org_id=db.org_id, run_session_id=run_session_id,
            lap_no=r["lap_no"], lap_time_ms=r["lap_time_ms"], driver_id=did,
            tire_compound=r.get("tire_compound", ""),
            visibility=sess._mapping["visibility"]))
        inserted += 1
    _reflag_session(db, run_session_id)
    return {"inserted": inserted, "unmatched_drivers": sorted(unmatched)}


def import_setup_values(db: TenantDB, setup_id: int, rows: list[dict]) -> dict:
    own = db.execute(select(setups.c.id).where(setups.c.id == setup_id)).first()
    if not own:
        raise ValueError("Setup sheet not found")
    for i, r in enumerate(rows):
        set_setup_value(db, setup_id, r["attr_key"], r["attr_value"],
                        r.get("group_name", ""), r.get("unit", ""), i)
    return {"values": len(rows)}


def import_parts(db: TenantDB, team_id: int, car_id: Optional[int],
                 rows: list[dict], user_id: int) -> dict:
    """Create the PLM part if it doesn't exist yet, then track its service life.
    Re-importing the same file updates hours rather than duplicating parts."""
    own = db.execute(select(teams.c.id).where(teams.c.id == team_id)).first()
    if not own:
        raise ValueError("Team not found")
    created, updated = 0, 0
    for r in rows:
        pid = db.execute(select(parts.c.id)
                         .where(parts.c.part_number == r["part_number"])).scalar()
        if pid is None:
            pid = db.execute(insert(parts).values(
                org_id=db.org_id, part_number=r["part_number"],
                part_name=r["part_name"], created_by=user_id).returning(parts.c.id)).scalar()
            created += 1
        existing = db.execute(select(part_usages.c.id).where(
            part_usages.c.part_id == pid, part_usages.c.team_id == team_id)).scalar()
        if existing:
            db.execute(update(part_usages).where(part_usages.c.id == existing).values(
                hours_used=r["hours_used"], hours_limit=r["hours_limit"]))
            updated += 1
        else:
            db.execute(insert(part_usages).values(
                org_id=db.org_id, part_id=pid, team_id=team_id, car_id=car_id,
                hours_used=r["hours_used"], hours_limit=r["hours_limit"]))
    return {"parts_created": created, "usages_updated": updated,
            "total": len(rows)}


def car_dossier(db: TenantDB, car_id: int) -> Optional[dict]:
    """Everything that hangs off one car — the answer to "what is on this car,
    what has it run, and what was it set up like?"

    The database already joined these; nothing showed them together, which is
    why the Car & Build and Parts & CAD screens read as disconnected fragments.
    """
    car = db.execute(select(cars).where(cars.c.id == car_id)).first()
    if not car:
        return None
    car = dict(car._mapping)

    team = db.execute(select(teams.c.id, teams.c.name, teams.c.car_number,
                             teams.c.series, teams.c["class"])
                      .where(teams.c.id == car["team_id"])).first()

    sessions = _rows(db.execute(
        select(run_sessions.c.id, run_sessions.c.session_type,
               run_sessions.c.session_date, run_sessions.c.visibility,
               events.c.name.label("event"), tracks.c.name.label("track"),
               func.count(laps.c.id).label("lap_count"),
               func.min(laps.c.lap_time_ms).label("best_ms"))
        .select_from(run_sessions.join(events, events.c.id == run_sessions.c.event_id)
                     .join(tracks, tracks.c.id == events.c.track_id)
                     .outerjoin(laps, laps.c.run_session_id == run_sessions.c.id))
        .where(run_sessions.c.car_id == car_id)
        .group_by(run_sessions.c.id, run_sessions.c.session_type,
                  run_sessions.c.session_date, run_sessions.c.visibility,
                  events.c.name, tracks.c.name)
        .order_by(run_sessions.c.session_date.desc())))

    fitted = _rows(db.execute(
        select(part_usages.c.id, part_usages.c.hours_used, part_usages.c.hours_limit,
               part_usages.c.status, parts.c.part_number, parts.c.part_name,
               parts.c.part_revision, parts.c.id.label("plm_part_id"))
        .select_from(part_usages.join(parts, parts.c.id == part_usages.c.part_id))
        .where(part_usages.c.car_id == car_id)
        .order_by(part_usages.c.status.desc(), part_usages.c.hours_used.desc())))
    for p in fitted:
        lim = p.get("hours_limit")
        p["life_pct"] = round(100 * p["hours_used"] / lim) if lim else None

    sheets = _rows(db.execute(
        select(setups.c.id, setups.c.setup_key, setups.c.revision_label,
               setups.c.visibility, tracks.c.name.label("track"))
        .select_from(setups.join(tracks, tracks.c.id == setups.c.track_id))
        .where(setups.c.car_id == car_id)
        .order_by(setups.c.created_at.desc())))

    best = min((s["best_ms"] for s in sessions if s["best_ms"]), default=None)
    return {
        "car": car,
        "team": dict(team._mapping) if team else None,
        "sessions": sessions,
        "parts": fitted,
        "setups": sheets,
        "totals": {
            "sessions": len(sessions),
            "laps": sum(s["lap_count"] or 0 for s in sessions),
            "best_lap_ms": best,
            "parts_fitted": len(fitted),
            "parts_due": sum(1 for p in fitted if p["status"] != "ok"),
        },
    }


def list_cars(db: TenantDB, team_id: Optional[int] = None) -> list[dict]:
    stmt = select(cars).order_by(cars.c.chassis)
    if team_id:
        stmt = stmt.where(cars.c.team_id == team_id)
    return _rows(db.execute(stmt))
