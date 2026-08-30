"""
HYPERPLM — fan accounts (Phase 3, issue #6).

The point of these tests is the *association model boundary*: a fan is an
account with NO organization. The interesting failures are a fan reaching tenant
data, and a fan being accidentally given an org by the normal signup path.
"""
import pytest


def reg_fan(client, username="fan1", display="Fan One"):
    r = client.post("/api/fan/register", json={
        "username": username, "password": "hunter2pass", "display_name": display})
    assert r.status_code == 201, r.text
    return r.json()


def publish_a_team(owner_client, reg, car="74"):
    """A member org publishes a team so fans have something to follow."""
    reg(owner_client, "owner1", "Alpha")
    t = owner_client.post("/api/racing/teams", json={
        "team_key": f"T{car}", "name": "Meridian Autosport",
        "car_number": car, "series": "IMSA WeatherTech", "class": "GTD"}).json()
    owner_client.patch(f"/api/racing/teams/{t['id']}/public", json={"is_public": True})
    return t


# ── a fan has no organization ────────────────────────────────────────────────

def test_fan_registration_creates_no_org(make_client):
    fan = make_client()
    reg_fan(fan)
    me = fan.get("/api/fan/me")
    assert me.status_code == 200
    body = me.json()
    assert body["profile"]["fan_type"] == "spectator"
    assert body["is_member"] is False
    # the tenant path must refuse them — that is what "no organization" means
    assert fan.get("/api/racing/teams").status_code == 403


def test_fan_cannot_reach_tenant_data(make_client, reg):
    owner = make_client()
    team = publish_a_team(owner, reg)
    owner.post("/api/racing/setups", json={
        "setup_key": "WG-Q", "team_id": team["id"],
        "track_id": owner.get("/api/racing/tracks").json()[0]["id"]})

    fan = make_client()
    reg_fan(fan, "spectator9")
    for path in ["/api/racing/teams", "/api/racing/setups", "/api/racing/parts",
                 "/api/racing/checklists"]:
        assert fan.get(path).status_code == 403, f"{path} leaked to a fan"


def test_member_signup_still_creates_an_org(make_client, reg):
    """Guard against the two paths drifting: /auth/register must keep making a tenant."""
    member = make_client()
    org_id = reg(member, "engineer1", "Bravo")
    assert org_id
    assert member.get("/api/racing/teams").status_code == 200


# ── following ────────────────────────────────────────────────────────────────

def test_follow_and_unfollow_a_published_team(make_client, reg):
    owner = make_client()
    publish_a_team(owner, reg)
    slug = make_client().get("/api/public/teams").json()[0]["slug"]

    fan = make_client()
    reg_fan(fan)
    assert fan.get("/api/fan/following").json() == []

    r = fan.post(f"/api/fan/follow/{slug}")
    assert r.status_code == 200 and r.json()["following"] is True
    assert [t["slug"] for t in fan.get("/api/fan/following").json()] == [slug]

    assert fan.delete(f"/api/fan/follow/{slug}").json()["following"] is False
    assert fan.get("/api/fan/following").json() == []


def test_cannot_follow_an_unpublished_team(make_client, reg):
    """A private team must not even be discoverable by slug."""
    owner = make_client()
    reg(owner, "owner2", "Charlie")
    t = owner.post("/api/racing/teams", json={
        "team_key": "SECRET", "name": "Skunkworks", "car_number": "1"}).json()
    owner.patch(f"/api/racing/teams/{t['id']}/public", json={"is_public": True})
    slug = make_client().get("/api/public/teams").json()[0]["slug"]
    owner.patch(f"/api/racing/teams/{t['id']}/public", json={"is_public": False})

    fan = make_client()
    reg_fan(fan)
    assert fan.post(f"/api/fan/follow/{slug}").status_code == 404


def test_following_requires_a_session(make_client, reg):
    owner = make_client()
    publish_a_team(owner, reg)
    slug = make_client().get("/api/public/teams").json()[0]["slug"]
    anon = make_client()
    assert anon.post(f"/api/fan/follow/{slug}").status_code == 401
    assert anon.get("/api/fan/me").status_code == 401


# ── points and badges ────────────────────────────────────────────────────────

def test_first_follow_awards_points_and_a_badge(make_client, reg):
    owner = make_client()
    publish_a_team(owner, reg)
    slug = make_client().get("/api/public/teams").json()[0]["slug"]

    fan = make_client()
    reg_fan(fan)
    assert fan.get("/api/fan/me").json()["profile"]["points"] == 0

    assert fan.post(f"/api/fan/follow/{slug}").json()["first_follow"] is True
    home = fan.get("/api/fan/me").json()
    assert home["profile"]["points"] == 50
    assert "first_follow" in [b["code"] for b in home["badges_earned"]]
    assert home["recent_points"][0]["delta"] == 50


def test_following_twice_does_not_double_award(make_client, reg):
    owner = make_client()
    publish_a_team(owner, reg)
    slug = make_client().get("/api/public/teams").json()[0]["slug"]
    fan = make_client()
    reg_fan(fan)
    fan.post(f"/api/fan/follow/{slug}")
    fan.post(f"/api/fan/follow/{slug}")          # idempotent
    assert fan.get("/api/fan/me").json()["profile"]["points"] == 50
    assert len(fan.get("/api/fan/following").json()) == 1


def test_fan_appears_on_the_public_leaderboard(make_client, reg):
    owner = make_client()
    publish_a_team(owner, reg)
    slug = make_client().get("/api/public/teams").json()[0]["slug"]
    fan = make_client()
    reg_fan(fan, "leader1", "Leader One")
    fan.post(f"/api/fan/follow/{slug}")

    board = make_client().get("/api/public/leaderboard").json()
    assert any(r["display_name"] == "Leader One" and r["points"] == 50 for r in board)


# ── a member can also be a fan ───────────────────────────────────────────────

def test_a_member_can_follow_teams_too(make_client, reg):
    owner = make_client()
    publish_a_team(owner, reg)
    slug = make_client().get("/api/public/teams").json()[0]["slug"]

    # the same account that owns an org follows a team
    assert owner.post(f"/api/fan/follow/{slug}").status_code == 200
    home = owner.get("/api/fan/me").json()
    assert home["is_member"] is True
    assert home["profile"]["fan_type"] == "member"
    assert owner.get("/api/racing/teams").status_code == 200, "still a member"


# ── registration validation ──────────────────────────────────────────────────

def test_duplicate_and_weak_registrations_are_refused(make_client):
    a = make_client(); reg_fan(a, "taken1")
    b = make_client()
    assert b.post("/api/fan/register", json={
        "username": "taken1", "password": "hunter2pass"}).status_code == 409
    assert b.post("/api/fan/register", json={
        "username": "shortpw", "password": "abc"}).status_code == 422
