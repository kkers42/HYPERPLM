"""
HYPERPLM — cold start (Phase 3).

The journey a brand-new user actually takes, with an empty database: register,
create a team, add a car and driver, run a session, log laps, publish, and then
see it as a fan. If this path breaks, nothing else matters — so it is one test,
in order, with no fixtures pre-seeding anything.
"""


def test_new_user_can_go_from_signup_to_published_laps(make_client):
    team_client = make_client()

    # 1. sign up — this provisions the organisation
    r = team_client.post("/auth/register", json={
        "username": "newcrew", "password": "hunter2pass", "org_name": "Redline Racing"})
    assert r.status_code == 200, r.text
    assert r.json()["active_org_id"]

    # 2. the workspace is reachable and genuinely empty
    assert team_client.get("/api/racing/teams").json() == []
    assert team_client.get("/api/racing/setups").json() == []
    assert team_client.get("/api/racing/parts").json() == []
    assert len(team_client.get("/api/racing/tracks").json()) >= 23, "circuits are seeded"
    summary = team_client.get("/api/racing/summary").json()
    assert summary["teams"] == 0 and summary["best_lap_ms"] is None

    # 3. create the team
    team = team_client.post("/api/racing/teams", json={
        "team_key": "REDLINE-12", "name": "Redline Racing", "car_number": "12",
        "series": "IMSA WeatherTech", "class": "GTD"}).json()
    assert team["id"]

    # 4. car + driver
    car = team_client.post("/api/racing/cars", json={
        "team_id": team["id"], "chassis": "RL-GT3-001", "model": "Porsche 911 GT3 R"}).json()
    driver = team_client.post("/api/racing/drivers", json={
        "team_id": team["id"], "name": "Sam Hale", "country": "GBR"}).json()
    assert car["id"] and driver["id"]

    # 5. event -> session -> laps
    track = next(t for t in team_client.get("/api/racing/tracks").json()
                 if t["name"].startswith("Watkins Glen"))
    event = team_client.post("/api/racing/events", json={
        "team_id": team["id"], "track_id": track["id"], "name": "Test Day"}).json()
    session = team_client.post("/api/racing/sessions", json={
        "event_id": event["id"], "car_id": car["id"], "session_type": "fp1"}).json()
    for n, ms in [(1, 104200), (2, 103100), (3, 102450)]:
        assert team_client.post("/api/racing/laps", json={
            "run_session_id": session["id"], "lap_no": n, "lap_time_ms": ms,
            "driver_id": driver["id"], "tire_compound": "Med"}).status_code == 201

    laps = team_client.get(f"/api/racing/teams/{team['id']}/laps").json()
    assert len(laps) == 3
    assert laps[0]["lap_time_ms"] == 102450 and laps[0]["is_fastest"], "fastest auto-flagged"

    # 6. a setup sheet, with values, cloned to a revision
    setup = team_client.post("/api/racing/setups", json={
        "setup_key": "WG-TEST-A", "team_id": team["id"], "track_id": track["id"],
        "baseline": "WG-A", "notes": "First run"}).json()
    assert setup["visibility"] == "team", "setups are private by default"
    assert team_client.put(f"/api/racing/setups/{setup['id']}/values", json={
        "attr_key": "LF", "attr_value": "312", "unit": "kg",
        "group_name": "Corner weights"}).status_code == 200
    clone = team_client.post(
        f"/api/racing/setups/WG-TEST-A/clone?new_key=WG-TEST-B").json()
    assert clone["setup_key"] == "WG-TEST-B"
    assert len(team_client.get("/api/racing/setups/WG-TEST-B").json()["values"]) == 1, \
        "cloning copies the values"

    # 7. a checklist with sign-off
    cl = team_client.post("/api/racing/checklists", json={
        "team_id": team["id"], "name": "Pre-session",
        "items": ["Tyre pressures", "Fuel load", "Radio check"]}).json()
    items = team_client.get("/api/racing/checklists").json()[0]["items"]
    assert len(items) == 3
    team_client.patch(f"/api/racing/checklist-items/{items[0]['id']}?done=true")
    assert team_client.get("/api/racing/checklists").json()[0]["done"] == 1

    # 8. nothing is public until the team publishes
    anon = make_client()
    assert anon.get("/api/public/teams").json() == []
    assert anon.get("/api/public/laps").json() == []

    team_client.patch(f"/api/racing/teams/{team['id']}/public", json={"is_public": True})
    published = anon.get("/api/public/teams").json()
    assert len(published) == 1
    slug = published[0]["slug"]

    # 9. the fan sees laps — and never the setup
    page = anon.get(f"/api/public/teams/{slug}").json()
    assert len(page["public_laps"]) == 3
    assert page["best_lap_ms"] == 102450
    assert anon.get(f"/api/public/teams/{slug}/setups").json() == [], "setups stay private"
    board = anon.get("/api/public/laps").json()
    assert board and board[0]["lap_time_ms"] == 102450

    # 10. a fan registers and follows
    fan = make_client()
    assert fan.post("/api/fan/register", json={
        "username": "firstfan", "password": "hunter2pass",
        "display_name": "First Fan"}).status_code == 201
    assert fan.post(f"/api/fan/follow/{slug}").json()["first_follow"] is True
    home = fan.get("/api/fan/me").json()
    assert home["profile"]["points"] == 50
    assert [t["slug"] for t in home["following"]] == [slug]

    # 11. stats reflect reality
    stats = anon.get("/api/public/stats").json()
    assert stats["published_teams"] == 1 and stats["fans"] == 1 and stats["follows"] == 1
