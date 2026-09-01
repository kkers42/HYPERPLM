"""
HYPERPLM — prediction questions and scoring (issue #7).

The interesting failures are money-adjacent: paying twice, paying the wrong
people, letting a fan see the answer key, or letting picks land after lock.
"""
import pytest


def setup_team_with_session(client, reg, username="pteam", org="Predict Co"):
    reg(client, username, org)
    team = client.post("/api/racing/teams", json={
        "team_key": "PT-1", "name": "Predict Racing", "car_number": "21"}).json()
    track = client.get("/api/racing/tracks").json()[0]
    ev = client.post("/api/racing/events", json={
        "team_id": team["id"], "track_id": track["id"], "name": "Test"}).json()
    sess = client.post("/api/racing/sessions", json={
        "event_id": ev["id"], "session_type": "qual"}).json()
    client.patch(f"/api/racing/teams/{team['id']}/public", json={"is_public": True})
    return team, sess


def ask(client, team, sess, prompt="Where does #21 qualify?",
        options=("P1-P3", "P4-P6", "P7+"), points=100):
    r = client.post(f"/api/racing/questions?team_id={team['id']}", json={
        "run_session_id": sess["id"], "prompt": prompt,
        "options": list(options), "points": points})
    assert r.status_code == 201, r.text
    return r.json()


def make_fan(make_client, name):
    c = make_client()
    assert c.post("/api/fan/register", json={
        "username": name, "password": "hunter2pass", "display_name": name}).status_code == 201
    return c


def test_fans_can_see_and_answer_an_open_question(make_client, reg, ):
    team_c = make_client()
    team, sess = setup_team_with_session(team_c, reg)
    q = ask(team_c, team, sess)
    assert q["status"] == "open" and q["options"] == ["P1-P3", "P4-P6", "P7+"]

    anon = make_client()
    live = anon.get("/api/public/questions").json()
    assert len(live) == 1
    assert "correct_answer" not in live[0], "the answer key must never be public"

    fan = make_fan(make_client, "picker1")
    assert fan.post(f"/api/fan/predict/{q['id']}", json={"answer": "P4-P6"}).status_code == 200
    mine = fan.get("/api/fan/predictions").json()
    assert mine[0]["answer"] == "P4-P6" and mine[0]["is_correct"] is None


def test_a_pick_can_be_changed_while_open_but_not_after_lock(make_client, reg):
    team_c = make_client()
    team, sess = setup_team_with_session(team_c, reg)
    q = ask(team_c, team, sess)
    fan = make_fan(make_client, "picker2")

    fan.post(f"/api/fan/predict/{q['id']}", json={"answer": "P1-P3"})
    r = fan.post(f"/api/fan/predict/{q['id']}", json={"answer": "P7+"})
    assert r.json()["changed"] is True
    assert len(fan.get("/api/fan/predictions").json()) == 1, "one pick per question"

    team_c.patch(f"/api/racing/questions/{q['id']}/status?team_id={team['id']}",
                 json={"status": "locked"})
    assert fan.post(f"/api/fan/predict/{q['id']}", json={"answer": "P1-P3"}).status_code == 400
    assert anon_open(make_client) == 0, "a locked question is no longer offered"


def anon_open(make_client):
    return len(make_client().get("/api/public/questions").json())


def test_an_answer_outside_the_options_is_refused(make_client, reg):
    team_c = make_client()
    team, sess = setup_team_with_session(team_c, reg)
    q = ask(team_c, team, sess)
    fan = make_fan(make_client, "picker3")
    assert fan.post(f"/api/fan/predict/{q['id']}",
                    json={"answer": "P99"}).status_code == 400


def test_resolving_pays_only_the_correct_fans(make_client, reg):
    team_c = make_client()
    team, sess = setup_team_with_session(team_c, reg)
    q = ask(team_c, team, sess, points=150)

    right = make_fan(make_client, "rightfan")
    wrong = make_fan(make_client, "wrongfan")
    right.post(f"/api/fan/predict/{q['id']}", json={"answer": "P4-P6"})
    wrong.post(f"/api/fan/predict/{q['id']}", json={"answer": "P7+"})
    before_r = right.get("/api/fan/me").json()["profile"]["points"]
    before_w = wrong.get("/api/fan/me").json()["profile"]["points"]

    res = team_c.post(f"/api/racing/questions/{q['id']}/resolve?team_id={team['id']}",
                      json={"correct_answer": "P4-P6"}).json()
    assert res["awarded"] == 1 and res["picks"] == 2

    assert right.get("/api/fan/me").json()["profile"]["points"] == before_r + 150
    assert wrong.get("/api/fan/me").json()["profile"]["points"] == before_w
    mine = right.get("/api/fan/predictions").json()[0]
    assert mine["is_correct"] == 1 and mine["points_awarded"] == 150


def test_resolving_twice_does_not_pay_twice(make_client, reg):
    team_c = make_client()
    team, sess = setup_team_with_session(team_c, reg)
    q = ask(team_c, team, sess)
    fan = make_fan(make_client, "oncefan")
    fan.post(f"/api/fan/predict/{q['id']}", json={"answer": "P1-P3"})

    team_c.post(f"/api/racing/questions/{q['id']}/resolve?team_id={team['id']}",
                json={"correct_answer": "P1-P3"})
    pts = fan.get("/api/fan/me").json()["profile"]["points"]

    again = team_c.post(f"/api/racing/questions/{q['id']}/resolve?team_id={team['id']}",
                        json={"correct_answer": "P1-P3"}).json()
    assert again["already_resolved"] is True and again["awarded"] == 0
    assert fan.get("/api/fan/me").json()["profile"]["points"] == pts, "no double payout"


def test_resolving_requires_release_not_merely_write(make_client, reg):
    team_c = make_client()
    team, sess = setup_team_with_session(team_c, reg)
    q = ask(team_c, team, sess)
    viewer = next(r for r in team_c.get("/api/admin/roles").json() if r["name"] == "Viewer")
    team_c.post("/api/users", json={"username": "vw", "password": "hunter2pass",
                                    "email": None, "role_id": viewer["id"]})
    v = make_client()
    v.post("/auth/login", json={"username": "vw", "password": "hunter2pass"})
    assert v.post(f"/api/racing/questions/{q['id']}/resolve?team_id={team['id']}",
                  json={"correct_answer": "P1-P3"}).status_code == 403


def test_another_org_cannot_touch_your_questions(make_client, reg):
    a = make_client(); team, sess = setup_team_with_session(a, reg, "orga", "Alpha")
    q = ask(a, team, sess)
    b = make_client(); reg(b, "orgb", "Bravo")
    assert b.post(f"/api/racing/questions/{q['id']}/resolve?team_id={team['id']}",
                  json={"correct_answer": "P1-P3"}).status_code == 400


def test_questions_on_unpublished_teams_are_not_public(make_client, reg):
    team_c = make_client()
    team, sess = setup_team_with_session(team_c, reg)
    ask(team_c, team, sess)
    assert anon_open(make_client) == 1
    team_c.patch(f"/api/racing/teams/{team['id']}/public", json={"is_public": False})
    assert anon_open(make_client) == 0, "unpublishing hides the questions too"


def test_predicting_requires_a_session(make_client, reg):
    team_c = make_client()
    team, sess = setup_team_with_session(team_c, reg)
    q = ask(team_c, team, sess)
    assert make_client().post(f"/api/fan/predict/{q['id']}",
                              json={"answer": "P1-P3"}).status_code == 401
