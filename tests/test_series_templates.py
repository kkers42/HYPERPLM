"""
HYPERPLM — series and setup templates (testing-notes follow-up).

From the notes: sessions should let a team pick a series and event rather than
retyping strings, and "IndyCar will be different than IMSA" — so a team needs
its own setup field lists.
"""


def team_for(client, reg, user="tmpl1", org="Template Co"):
    reg(client, user, org)
    return client.post("/api/racing/teams", json={
        "team_key": "TT-1", "name": "Template Racing", "car_number": "8"}).json()


def test_series_are_reference_data(make_client, reg):
    c = make_client(); team_for(c, reg)
    s = c.get("/api/racing/series").json()
    names = [x["name"] for x in s]
    assert "NTT IndyCar Series" in names
    assert any("IMSA WeatherTech" in n for n in names)


def test_picking_a_series_narrows_the_track_list(make_client, reg):
    """The point of the feature: choosing IndyCar should not offer Lime Rock."""
    c = make_client(); team_for(c, reg)
    series = {x["name"]: x for x in c.get("/api/racing/series").json()}
    indy = series["NTT IndyCar Series"]
    tracks = c.get(f"/api/racing/series/{indy['id']}/tracks").json()
    names = [t["name"] for t in tracks]
    assert names, "IndyCar must offer circuits"
    assert all("IndyCar" in (t["series_tag"] or "") for t in tracks)
    assert not any("Lime Rock" in n for n in names), "Lime Rock is not an IndyCar circuit"
    assert any("Indianapolis" in n for n in names)

    imsa = next(v for k, v in series.items() if "IMSA WeatherTech" in k)
    imsa_tracks = [t["name"] for t in c.get(f"/api/racing/series/{imsa['id']}/tracks").json()]
    assert any("Lime Rock" in n for n in imsa_tracks), "Lime Rock IS an IMSA circuit"


def test_an_event_can_record_its_series(make_client, reg):
    c = make_client(); team = team_for(c, reg)
    series = c.get("/api/racing/series").json()[0]
    track = c.get("/api/racing/tracks").json()[0]
    ev = c.post("/api/racing/events", json={
        "team_id": team["id"], "track_id": track["id"], "name": "Round 1",
        "series_id": series["id"]})
    assert ev.status_code == 201, ev.text
    assert ev.json()["series_id"] == series["id"]


def test_starter_templates_differ_by_discipline(make_client, reg):
    """A GT3 sheet and an IndyCar sheet are genuinely different documents."""
    c = make_client(); team_for(c, reg)
    starters = c.get("/api/racing/templates").json()["starters"]
    assert "GT3 / sports car" in starters and "IndyCar / open wheel" in starters

    for kind in starters:
        assert c.post("/api/racing/templates/starter",
                      json={"kind": kind}).status_code == 201
    tpls = {t["name"]: t for t in c.get("/api/racing/templates").json()["templates"]}
    gt3 = [f["attr_key"] for f in tpls["GT3 / sports car"]["fields"]]
    indy = [f["attr_key"] for f in tpls["IndyCar / open wheel"]["fields"]]

    assert "Diff preload" in gt3 and "Diff preload" not in indy
    assert "Weight jacker" in indy and "Weight jacker" not in gt3
    assert "Stagger" in indy, "stagger is an oval concern"
    # units differ by discipline too
    gt3_units = {f["attr_key"]: f["unit"] for f in tpls["GT3 / sports car"]["fields"]}
    indy_units = {f["attr_key"]: f["unit"] for f in tpls["IndyCar / open wheel"]["fields"]}
    assert gt3_units["LF"] == "kg" and indy_units["LF"] == "lb"


def test_a_template_lays_its_fields_onto_a_sheet(make_client, reg):
    c = make_client(); team = team_for(c, reg)
    c.post("/api/racing/templates/starter", json={"kind": "GT3 / sports car"})
    tpl = c.get("/api/racing/templates").json()["templates"][0]
    track = c.get("/api/racing/tracks").json()[0]
    setup = c.post("/api/racing/setups", json={
        "setup_key": "T-1", "team_id": team["id"], "track_id": track["id"]}).json()
    assert c.get("/api/racing/setups/T-1").json()["values"] == []

    r = c.post(f"/api/racing/setups/{setup['id']}/apply-template/{tpl['id']}")
    assert r.status_code == 200 and r.json()["fields_added"] == len(tpl["fields"])
    values = c.get("/api/racing/setups/T-1").json()["values"]
    assert len(values) == len(tpl["fields"])
    assert {v["group_name"] for v in values} >= {"Corner weights", "Tires"}


def test_templates_are_private_to_the_org(make_client, reg):
    a = make_client(); team_for(a, reg, "orga2", "Alpha2")
    a.post("/api/racing/templates/starter", json={"kind": "GT3 / sports car"})
    assert len(a.get("/api/racing/templates").json()["templates"]) == 1

    b = make_client(); reg(b, "orgb2", "Bravo2")
    assert b.get("/api/racing/templates").json()["templates"] == [], \
        "another org must not see your templates"
