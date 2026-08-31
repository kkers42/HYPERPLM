"""
HYPERPLM — importing existing team data (testing-notes follow-up).

Teams hold seasons of data in spreadsheets whose columns are named whatever the
engineer typed. These tests are mostly about being forgiving with real-world
files while never silently losing or mangling a row.
"""
import io


def team_with_session(client, reg, user="imp1", org="Import Co"):
    reg(client, user, org)
    team = client.post("/api/racing/teams", json={
        "team_key": "IM-1", "name": "Import Racing", "car_number": "3"}).json()
    track = client.get("/api/racing/tracks").json()[0]
    ev = client.post("/api/racing/events", json={
        "team_id": team["id"], "track_id": track["id"], "name": "Import Test"}).json()
    sess = client.post("/api/racing/sessions", json={
        "event_id": ev["id"], "session_type": "fp1"}).json()
    return team, sess


def upload(client, url, content, name="data.csv"):
    return client.post(url, files={"file": (name, io.BytesIO(content.encode()), "text/csv")})


# ── lap times ────────────────────────────────────────────────────────────────

def test_lap_import_accepts_the_formats_teams_actually_use(make_client, reg):
    c = make_client(); team, sess = team_with_session(c, reg)
    c.post("/api/racing/drivers", json={"team_id": team["id"], "name": "Sam Hale"})
    csv = ("Lap,Time,Driver,Tyre\n"
           "1,1:41.208,Sam Hale,Med\n"       # m:ss.mmm
           "2,101.9,Sam Hale,Med\n"          # bare seconds
           "3,1:42,Sam Hale,Hard\n"          # no decimals
           "4,,Sam Hale,Med\n"               # missing -> skipped, not fatal
           "5,rubbish,Sam Hale,Med\n")       # unparseable -> skipped
    r = upload(c, f"/api/racing/import/laps?run_session_id={sess['id']}", csv)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inserted"] == 3 and body["skipped"] == 2
    assert body["errors"][0]["row"] == 5, "row numbers refer to the file, header included"

    laps = c.get(f"/api/racing/teams/{team['id']}/laps").json()
    times = sorted(l["lap_time_ms"] for l in laps)
    assert times == [101208, 101900, 102000]
    assert laps[0]["is_fastest"], "flags recomputed after a bulk import"
    assert laps[0]["driver"] == "Sam Hale", "driver names matched to the roster"


def test_lap_import_tolerates_differently_named_columns(make_client, reg):
    c = make_client(); team, sess = team_with_session(c, reg, "imp2", "Import2")
    csv = "LapNo;Lap Time;Compound\n1;1:39.5;Soft\n2;1:39.9;Soft\n"
    r = upload(c, f"/api/racing/import/laps?run_session_id={sess['id']}", csv)
    assert r.json()["inserted"] == 2, "semicolons and alias headers must work"


def test_unmatched_drivers_are_reported_not_dropped(make_client, reg):
    c = make_client(); team, sess = team_with_session(c, reg, "imp3", "Import3")
    csv = "Lap,Time,Driver\n1,1:40.0,Nobody Here\n"
    body = upload(c, f"/api/racing/import/laps?run_session_id={sess['id']}", csv).json()
    assert body["inserted"] == 1
    assert body["unmatched_drivers"] == ["Nobody Here"], \
        "the lap is kept and the mismatch is surfaced"


# ── preview writes nothing ───────────────────────────────────────────────────

def test_preview_reports_without_writing(make_client, reg):
    c = make_client(); team, sess = team_with_session(c, reg, "imp4", "Import4")
    csv = "Lap,Time\n1,1:41.0\n2,bad\n"
    p = upload(c, "/api/racing/import/laps/preview", csv).json()
    assert p["total_rows"] == 2 and p["ready"] == 1 and p["skipped"] == 1
    assert p["detected_columns"] == ["Lap", "Time"]
    assert p["expected_columns"]
    assert c.get(f"/api/racing/teams/{team['id']}/laps").json() == [], \
        "preview must not write anything"


# ── setup sheets ─────────────────────────────────────────────────────────────

def test_setup_import_fills_a_sheet(make_client, reg):
    c = make_client(); team, _ = team_with_session(c, reg, "imp5", "Import5")
    track = c.get("/api/racing/tracks").json()[0]
    setup = c.post("/api/racing/setups", json={
        "setup_key": "IMP-1", "team_id": team["id"], "track_id": track["id"]}).json()
    csv = ("Group,Field,Value,Unit\n"
           "Corner weights,LF,312,kg\n"
           "Corner weights,RF,318,kg\n"
           "Tires,Compound,Medium,\n"
           ",,,\n")
    r = upload(c, f"/api/racing/import/setup?setup_id={setup['id']}", csv)
    assert r.status_code == 200
    vals = c.get("/api/racing/setups/IMP-1").json()["values"]
    assert len(vals) == 3
    assert {v["group_name"] for v in vals} == {"Corner weights", "Tires"}
    assert next(v for v in vals if v["attr_key"] == "LF")["unit"] == "kg"


# ── parts / service life ─────────────────────────────────────────────────────

def test_part_import_creates_plm_parts_and_is_repeatable(make_client, reg):
    c = make_client(); team, _ = team_with_session(c, reg, "imp6", "Import6")
    csv = ("Part Number,Description,Hours Used,Hours Limit\n"
           "74-DRV-0021,Right driveshaft,41,40\n"      # past its limit
           "74-SUS-0114,LF upright bearing,29.5,35\n"  # 84% -> due soon
           "74-BRK-0308,Front rotors,4,20\n")          # 20% -> fine
    body = upload(c, f"/api/racing/import/parts?team_id={team['id']}", csv).json()
    assert body["parts_created"] == 3 and body["total"] == 3

    parts = {p["part_number"]: p for p in c.get("/api/racing/parts").json()}
    assert len(parts) == 3
    # status is derived by the 0005 trigger, on import exactly as on manual entry
    assert parts["74-DRV-0021"]["status"] == "over"
    assert parts["74-SUS-0114"]["status"] == "service_soon"
    assert parts["74-BRK-0308"]["status"] == "ok"

    # re-importing the same file with new hours updates rather than duplicating
    csv2 = "Part Number,Description,Hours Used,Hours Limit\n74-DRV-0021,Right driveshaft,5,40\n"
    body2 = upload(c, f"/api/racing/import/parts?team_id={team['id']}", csv2).json()
    assert body2["parts_created"] == 0 and body2["usages_updated"] == 1
    assert len(c.get("/api/racing/parts").json()) == 3, "no duplicate part rows"
    assert next(p for p in c.get("/api/racing/parts").json()
                if p["part_number"] == "74-DRV-0021")["status"] == "ok"


# ── failure modes ────────────────────────────────────────────────────────────

def test_an_empty_or_unreadable_file_is_refused_clearly(make_client, reg):
    c = make_client(); team, sess = team_with_session(c, reg, "imp7", "Import7")
    r = upload(c, "/api/racing/import/laps/preview", "Lap,Time\n")
    assert r.status_code == 400 and "no data" in r.json()["detail"].lower()


def test_importing_into_another_orgs_session_is_refused(make_client, reg):
    a = make_client(); team, sess = team_with_session(a, reg, "impA", "AlphaImp")
    b = make_client(); reg(b, "impB", "BravoImp")
    csv = "Lap,Time\n1,1:40.0\n"
    r = upload(b, f"/api/racing/import/laps?run_session_id={sess['id']}", csv)
    assert r.status_code == 404, "RLS hides the session, so the import cannot land"
    assert a.get(f"/api/racing/teams/{team['id']}/laps").json() == []
