"""
HYPERPLM — the car as the spine (testing-notes follow-up).

From the notes: "are we trying to connect the multiple cars a team might have to
the set-ups and data/components?" Yes. These tests assert that a car answers
"what is fitted, what has it run, and how was it set up" — and that two cars on
one team stay separate.
"""


def build_team(client, reg, user="car1", org="Car Co"):
    reg(client, user, org)
    team = client.post("/api/racing/teams", json={
        "team_key": "CC-1", "name": "Chassis Racing", "car_number": "11"}).json()
    a = client.post("/api/racing/cars", json={
        "team_id": team["id"], "chassis": "CH-001", "model": "GT3"}).json()
    b = client.post("/api/racing/cars", json={
        "team_id": team["id"], "chassis": "CH-002", "model": "GT3"}).json()
    return team, a, b


def run_session(client, team, car, ms_list, track_idx=0):
    track = client.get("/api/racing/tracks").json()[track_idx]
    ev = client.post("/api/racing/events", json={
        "team_id": team["id"], "track_id": track["id"], "name": "Meeting"}).json()
    sess = client.post("/api/racing/sessions", json={
        "event_id": ev["id"], "car_id": car["id"], "session_type": "fp1"}).json()
    for i, ms in enumerate(ms_list, start=1):
        client.post("/api/racing/laps", json={
            "run_session_id": sess["id"], "lap_no": i, "lap_time_ms": ms})
    return sess


def test_a_car_gathers_its_own_sessions_parts_and_setups(make_client, reg):
    c = make_client(); team, car_a, car_b = build_team(c, reg)
    run_session(c, team, car_a, [103000, 102000])
    track = c.get("/api/racing/tracks").json()[0]
    c.post("/api/racing/setups", json={
        "setup_key": "CH1-A", "team_id": team["id"], "track_id": track["id"],
        "car_id": car_a["id"]})
    part = c.post("/api/parts", json={"part_number": "P-1", "part_name": "Upright"}).json()
    c.post("/api/racing/parts", json={"part_id": part["id"], "team_id": team["id"],
                                      "car_id": car_a["id"], "hours_used": 9,
                                      "hours_limit": 10})

    d = c.get(f"/api/racing/cars/{car_a['id']}").json()
    assert d["car"]["chassis"] == "CH-001"
    assert d["totals"]["sessions"] == 1 and d["totals"]["laps"] == 2
    assert d["totals"]["best_lap_ms"] == 102000
    assert [s["setup_key"] for s in d["setups"]] == ["CH1-A"]
    assert d["parts"][0]["part_number"] == "P-1"
    assert d["parts"][0]["life_pct"] == 90 and d["parts"][0]["status"] == "service_soon"
    assert d["totals"]["parts_due"] == 1


def test_two_cars_on_one_team_do_not_bleed_into_each_other(make_client, reg):
    c = make_client(); team, car_a, car_b = build_team(c, reg, "car2", "Car2")
    run_session(c, team, car_a, [101000])
    run_session(c, team, car_b, [99000], track_idx=1)

    a = c.get(f"/api/racing/cars/{car_a['id']}").json()
    b = c.get(f"/api/racing/cars/{car_b['id']}").json()
    assert a["totals"]["best_lap_ms"] == 101000
    assert b["totals"]["best_lap_ms"] == 99000
    assert a["totals"]["sessions"] == 1 and b["totals"]["sessions"] == 1
    assert a["sessions"][0]["track"] != b["sessions"][0]["track"]


def test_a_new_car_is_honestly_empty(make_client, reg):
    c = make_client(); team, car_a, car_b = build_team(c, reg, "car3", "Car3")
    d = c.get(f"/api/racing/cars/{car_b['id']}").json()
    assert d["totals"] == {"sessions": 0, "laps": 0, "best_lap_ms": None,
                           "parts_fitted": 0, "parts_due": 0}
    assert d["sessions"] == [] and d["parts"] == [] and d["setups"] == []


def test_another_org_cannot_open_your_car(make_client, reg):
    a = make_client(); team, car_a, _ = build_team(a, reg, "carA", "AlphaCar")
    b = make_client(); reg(b, "carB", "BravoCar")
    assert b.get(f"/api/racing/cars/{car_a['id']}").status_code == 404
    assert b.get("/api/racing/cars").json() == []
