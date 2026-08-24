"""
HYPERPLM — racing + fan-wall acceptance suite (Phase 3, issue #8).

Mirrors the Phase 2 tenant-isolation suite for the racing domain. Two guarantees
are under test and they are deliberately different mechanisms:

  * RLS keeps ORGS apart          — org B can never see org A's racing rows.
  * `visibility` keeps FANS out   — inside one org, only published rows are public.

Plus the database-side rules from migration 0005 (directory sync, part status),
which must hold no matter what a caller sends.
"""
import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def mk_team(client, key="T74", name="Meridian Autosport", car="74"):
    r = client.post("/api/racing/teams", json={
        "team_key": key, "name": name, "car_number": car,
        "series": "IMSA WeatherTech", "class": "GTD"})
    assert r.status_code == 201, r.text
    return r.json()


def mk_session(client, team, visibility="public"):
    track_id = client.get("/api/racing/tracks").json()[0]["id"]
    ev = client.post("/api/racing/events", json={
        "team_id": team["id"], "track_id": track_id, "name": "Test Weekend"})
    assert ev.status_code == 201, ev.text
    s = client.post("/api/racing/sessions", json={
        "event_id": ev.json()["id"], "session_type": "fp1", "visibility": visibility})
    assert s.status_code == 201, s.text
    return s.json()


def mk_lap(client, session, ms=101208, lap_no=3):
    r = client.post("/api/racing/laps", json={
        "run_session_id": session["id"], "lap_no": lap_no, "lap_time_ms": ms})
    assert r.status_code == 201, r.text
    return r.json()


def mk_setup(client, team, key="WG-Q"):
    track_id = client.get("/api/racing/tracks").json()[0]["id"]
    r = client.post("/api/racing/setups", json={
        "setup_key": key, "team_id": team["id"], "track_id": track_id})
    assert r.status_code == 201, r.text
    return r.json()


# ── the circuit library is shared reference data ──────────────────────────────

def test_tracks_are_seeded_and_shared(make_client, reg):
    alice = make_client(); reg(alice, "alice", "Alpha")
    tracks = alice.get("/api/racing/tracks").json()
    assert len(tracks) >= 23, "migration 0003 seeds the IMSA/IndyCar circuits"
    assert any(t["name"].startswith("Watkins Glen") for t in tracks)


# ── cross-tenant isolation (RLS) ──────────────────────────────────────────────

def test_racing_rows_are_invisible_across_orgs(make_client, reg):
    alice = make_client(); reg(alice, "alice", "Alpha")
    bob = make_client(); reg(bob, "bob", "Bravo")

    team = mk_team(alice)
    sess = mk_session(alice, team)
    mk_lap(alice, sess)
    mk_setup(alice, team)

    assert alice.get("/api/racing/teams").json(), "owner sees their own team"
    assert bob.get("/api/racing/teams").json() == [], "other org sees nothing"
    assert bob.get("/api/racing/setups").json() == []


def test_direct_id_access_across_orgs_is_refused(make_client, reg):
    """IDOR: guessing another org's ids must not work."""
    alice = make_client(); reg(alice, "alice", "Alpha")
    bob = make_client(); reg(bob, "bob", "Bravo")
    team = mk_team(alice)
    sess = mk_session(alice, team)
    mk_lap(alice, sess)

    assert bob.get(f"/api/racing/teams/{team['id']}/laps").status_code == 404
    assert bob.get(f"/api/racing/teams/{team['id']}/sessions").status_code == 404
    # and bob cannot publish someone else's team
    assert bob.patch(f"/api/racing/teams/{team['id']}/public",
                     json={"is_public": True}).status_code in (403, 404)


# ── the fan wall (visibility) ─────────────────────────────────────────────────

def test_new_team_is_not_public_until_published(make_client, reg):
    """0005's trigger defaults is_public to 0 — publishing is opt-in."""
    alice = make_client(); reg(alice, "alice", "Alpha")
    team = mk_team(alice)
    anon = make_client()
    assert anon.get("/api/public/teams").json() == []

    assert alice.patch(f"/api/racing/teams/{team['id']}/public",
                       json={"is_public": True}).status_code == 200
    published = anon.get("/api/public/teams").json()
    assert [t["car_number"] for t in published] == ["74"]


def test_fans_never_see_a_team_only_setup(make_client, reg):
    alice = make_client(); reg(alice, "alice", "Alpha")
    team = mk_team(alice)
    setup = mk_setup(alice, team)
    alice.patch(f"/api/racing/teams/{team['id']}/public", json={"is_public": True})

    # default visibility is team-only
    assert setup["visibility"] == "team"
    anon = make_client()
    slug = anon.get("/api/public/teams").json()[0]["slug"]
    assert anon.get(f"/api/public/teams/{slug}/setups").json() == []
    # and the member-facing route refuses it in fan mode, with 404 not 403,
    # so a fan cannot even confirm the sheet exists
    assert alice.get(f"/api/racing/setups/{setup['setup_key']}"
                     "?public_only=true").status_code == 404


def test_unpublishing_a_session_withdraws_its_laps_from_fans(make_client, reg):
    alice = make_client(); reg(alice, "alice", "Alpha")
    team = mk_team(alice)
    sess = mk_session(alice, team)
    mk_lap(alice, sess)
    alice.patch(f"/api/racing/teams/{team['id']}/public", json={"is_public": True})

    anon = make_client()
    slug = anon.get("/api/public/teams").json()[0]["slug"]
    assert len(anon.get(f"/api/public/teams/{slug}").json()["public_laps"]) == 1

    alice.patch(f"/api/racing/sessions/{sess['id']}/visibility", json={"visibility": "team"})
    assert anon.get(f"/api/public/teams/{slug}").json()["public_laps"] == []

    alice.patch(f"/api/racing/sessions/{sess['id']}/visibility", json={"visibility": "public"})
    assert len(anon.get(f"/api/public/teams/{slug}").json()["public_laps"]) == 1


def test_private_team_is_not_reachable_by_slug(make_client, reg):
    alice = make_client(); reg(alice, "alice", "Alpha")
    team = mk_team(alice)
    alice.patch(f"/api/racing/teams/{team['id']}/public", json={"is_public": True})
    anon = make_client()
    slug = anon.get("/api/public/teams").json()[0]["slug"]

    alice.patch(f"/api/racing/teams/{team['id']}/public", json={"is_public": False})
    assert anon.get(f"/api/public/teams/{slug}").status_code == 404


# ── publishing is gated on `release`, not merely `write` ──────────────────────

def test_publishing_requires_release_ability(make_client, reg):
    alice = make_client(); reg(alice, "alice", "Alpha")
    team = mk_team(alice)
    viewer = next(r for r in alice.get("/api/admin/roles").json() if r["name"] == "Viewer")
    assert alice.post("/api/users", json={
        "username": "carl", "password": "hunter2pass", "email": None,
        "role_id": viewer["id"]}).status_code == 201

    carl = make_client()
    assert carl.post("/auth/login", json={
        "username": "carl", "password": "hunter2pass"}).status_code == 200
    assert carl.get("/api/racing/teams").status_code == 200          # view allowed
    assert carl.post("/api/racing/teams", json={                     # write denied
        "team_key": "X", "name": "X"}).status_code == 403
    assert carl.patch(f"/api/racing/teams/{team['id']}/public",       # publish denied
                      json={"is_public": True}).status_code == 403


# ── database-side rules (migration 0005) ──────────────────────────────────────

def test_part_status_is_derived_not_trusted(make_client, reg):
    """A caller cannot store a status that contradicts hours vs limit."""
    alice = make_client(); reg(alice, "alice", "Alpha")
    team = mk_team(alice)
    p = alice.post("/api/parts", json={
        "part_number": "74-DRV-0021", "part_name": "Driveshaft"}).json()
    # track it with a limit, then drive it past the limit
    from app.tenancy import tenant_session
    from app import racing
    org_id = alice.get("/auth/me").json()["active_org_id"]
    with tenant_session(org_id) as db:
        usage = racing.track_part(db, {"part_id": p["id"], "team_id": team["id"],
                                       "hours_used": 0, "hours_limit": 10})
    rows = alice.get("/api/racing/parts").json()
    assert rows[0]["status"] == "ok"

    alice.patch(f"/api/racing/parts/{usage['id']}/hours", json={"hours_used": 8.5})
    assert alice.get("/api/racing/parts").json()[0]["status"] == "service_soon"
    alice.patch(f"/api/racing/parts/{usage['id']}/hours", json={"hours_used": 11})
    assert alice.get("/api/racing/parts").json()[0]["status"] == "over"


def test_renaming_a_team_does_not_republish_it(make_client, reg):
    """The directory trigger preserves is_public across updates."""
    alice = make_client(); reg(alice, "alice", "Alpha")
    team = mk_team(alice)
    anon = make_client()
    assert anon.get("/api/public/teams").json() == []

    from app.tenancy import tenant_session
    from sqlalchemy import update
    from app.db import teams as teams_tbl
    org_id = alice.get("/auth/me").json()["active_org_id"]
    with tenant_session(org_id) as db:
        db.execute(update(teams_tbl).where(teams_tbl.c.id == team["id"])
                   .values(name="Renamed Autosport"))
    assert anon.get("/api/public/teams").json() == [], "a rename must not publish"
