"""
HYPERPLM — tenant isolation acceptance suite (Phase 2 §8).

Proves that one org cannot see or touch another org's data — via the API (including
direct-id / IDOR access) and at the database layer (RLS fail-closed). Plus a single-org
regression that the core PLM flow still works.
"""
import pytest


# ── Parts isolation (list / get / update / delete by id) ──────────────────────

def test_parts_isolation(make_client, reg, part):
    alice, bob = make_client(), make_client()
    reg(alice, "alice", "Alpha Racing")
    reg(bob, "bob", "Bravo Motorsport")
    car = part(alice, "CAR-1", "Race Car")

    # Bob (a different org) sees nothing of Alice's.
    assert bob.get("/api/parts").json()["total"] == 0
    assert bob.get(f"/api/parts/{car['id']}").status_code == 404
    # IDOR: Bob cannot update or delete Alice's part by guessing its id.
    assert bob.put(f"/api/parts/{car['id']}",
                   json={"part_name": "hax", "description": "", "part_level": ""}).status_code == 404
    assert bob.delete(f"/api/parts/{car['id']}").status_code == 404
    # Alice still sees her part intact.
    assert alice.get(f"/api/parts/{car['id']}").json()["part_name"] == "Race Car"


def test_bom_and_relationships_isolation(make_client, reg, part):
    alice, bob = make_client(), make_client()
    reg(alice, "alice", "Alpha")
    reg(bob, "bob", "Bravo")
    car = part(alice, "CAR-1", "Car")
    eng = part(alice, "ENG-1", "Engine")
    assert alice.post("/api/relationships",
                      json={"parent_part_id": car["id"], "child_part_id": eng["id"],
                            "quantity": 1}).status_code == 201

    # Alice sees her BOM; Bob sees an empty BOM for the same (foreign) id and no relationships.
    assert len(alice.get(f"/api/parts/{car['id']}/bom").json()["items"]) == 1
    assert bob.get(f"/api/parts/{car['id']}/bom").status_code == 404
    assert bob.get("/api/relationships").json() == []


def test_documents_isolation(make_client, reg, part):
    alice, bob = make_client(), make_client()
    reg(alice, "alice", "Alpha")
    reg(bob, "bob", "Bravo")
    p = part(alice, "PRT-1", "Bracket")
    up = alice.post("/api/documents",
                    files={"file": ("bracket.stl", b"solid test\n", "application/octet-stream")},
                    data={"part_id": str(p["id"]), "description": "cad"})
    assert up.status_code == 201, up.text
    doc_id = up.json()["id"]

    assert bob.get("/api/documents").json() == []
    assert bob.get(f"/api/documents/{doc_id}").status_code == 404
    assert bob.get(f"/api/documents/{doc_id}/download").status_code == 404
    assert alice.get(f"/api/documents/{doc_id}/download").status_code == 200


def test_revisions_and_audit_isolation(make_client, reg, part):
    alice, bob = make_client(), make_client()
    reg(alice, "alice", "Alpha")
    reg(bob, "bob", "Bravo")
    p = part(alice, "REV-1", "Thing")
    assert alice.post(f"/api/parts/{p['id']}/revise", json={"description": "cut"}).status_code == 200

    # Revisions of a foreign part are not visible; Bob's audit log is empty.
    assert alice.get(f"/api/parts/{p['id']}/revisions").json()  # non-empty
    assert bob.get(f"/api/parts/{p['id']}/revisions").json() == []
    assert alice.get("/api/admin/audit").json()["total"] > 0
    assert bob.get("/api/admin/audit").json()["total"] == 0


def test_part_number_unique_per_org(make_client, reg, part):
    alice, bob = make_client(), make_client()
    reg(alice, "alice", "Alpha")
    reg(bob, "bob", "Bravo")
    part(alice, "SHARED-1", "A")
    part(bob, "SHARED-1", "B")  # same number, different org — allowed
    # Duplicate within the same org is rejected.
    assert alice.post("/api/parts", json={"part_number": "SHARED-1", "part_name": "dup"}).status_code == 409


# ── Org switching ─────────────────────────────────────────────────────────────

def test_org_switch_changes_dataset(make_client, reg, part):
    alice = make_client()
    org1 = reg(alice, "alice", "Alpha")
    part(alice, "A-1", "in org1")

    r = alice.post("/api/orgs", json={"name": "Alpha Two"})
    assert r.status_code == 201
    org2 = r.json()["active_org_id"]
    assert org2 != org1
    assert alice.get("/api/parts").json()["total"] == 0      # new org is empty
    part(alice, "A-1", "in org2")                            # same number reusable

    assert alice.post("/api/orgs/switch", json={"org_id": org1}).status_code == 200
    assert alice.get("/api/parts").json()["total"] == 1      # back to org1's data


def test_non_member_cannot_switch(make_client, reg):
    alice, bob = make_client(), make_client()
    org1 = reg(alice, "alice", "Alpha")
    reg(bob, "bob", "Bravo")
    assert bob.post("/api/orgs/switch", json={"org_id": org1}).status_code == 403


# ── Role-based access within an org ───────────────────────────────────────────

def test_viewer_role_cannot_write(make_client, reg):
    alice = make_client()
    reg(alice, "alice", "Alpha")
    viewer = next(r for r in alice.get("/api/admin/roles").json() if r["name"] == "Viewer")
    assert alice.post("/api/users", json={
        "username": "carl", "password": "hunter2pass", "email": None, "role_id": viewer["id"],
    }).status_code == 201

    carl = make_client()
    assert carl.post("/auth/login", json={"username": "carl", "password": "hunter2pass"}).status_code == 200
    assert carl.get("/api/parts").status_code == 200                              # view allowed
    assert carl.post("/api/parts", json={"part_number": "X", "part_name": "Y"}).status_code == 403  # write denied


# ── Unauthenticated ───────────────────────────────────────────────────────────

def test_unauthenticated_is_rejected(make_client):
    anon = make_client()
    assert anon.get("/api/parts").status_code == 401
    assert anon.get("/auth/me").status_code == 401


# ── Database-layer RLS (fail closed) ──────────────────────────────────────────

def test_rls_fail_closed_without_guc():
    from sqlalchemy import text
    from app.db import get_engine
    # App-role connection with no app.current_org set: tenant tables must ERROR, not leak.
    with get_engine().connect() as c:
        with pytest.raises(Exception):
            c.execute(text("SELECT count(*) FROM parts")).scalar()


def test_global_session_cannot_read_tenant_tables():
    from sqlalchemy import text
    from app.tenancy import global_session
    with pytest.raises(Exception):
        with global_session() as c:
            c.execute(text("SELECT count(*) FROM parts")).scalar()


# ── Single-org regression (core flow still works) ─────────────────────────────

def test_single_org_full_flow(make_client, reg, part):
    alice = make_client()
    reg(alice, "alice", "Alpha")
    car = part(alice, "CAR-1", "Car")
    eng = part(alice, "ENG-1", "Engine")
    alice.post("/api/relationships",
               json={"parent_part_id": car["id"], "child_part_id": eng["id"], "quantity": 2})

    # attribute
    assert alice.put(f"/api/parts/{eng['id']}/attributes",
                     json={"key": "Power", "value": "500hp", "order": 1}).status_code == 200
    # checkout / release / revise
    assert alice.post(f"/api/parts/{eng['id']}/checkout", json={"station": "b1"}).status_code == 200
    assert alice.post(f"/api/parts/{eng['id']}/release").status_code == 200
    assert alice.post(f"/api/parts/{eng['id']}/revise", json={"description": "v2"}).json()["new_revision"] == "B"
    # BOM export downloads an xlsx
    exp = alice.get(f"/api/parts/{car['id']}/bom/export")
    assert exp.status_code == 200
    assert exp.headers["content-type"].startswith("application/vnd.openxmlformats")
    # where-used
    assert len(alice.get(f"/api/parts/{eng['id']}/where-used").json()) == 1
