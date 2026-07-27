"""
HYPERPLM — data-access layer (Phase 2, step 4).

SQLAlchemy Core queries over the PostgreSQL schema in app/db.py. Two families:

- Global (users, organizations, org_members): take a Connection from
  tenancy.global_session(). No RLS — access is governed by app logic.
- Tenant (roles, parts, attributes, revisions, relationships, documents, file
  versions, audit): take a tenancy.TenantDB. Row-level security scopes every SELECT/
  UPDATE/DELETE to the active org automatically; INSERTs set org_id = db.org_id, and
  the WITH CHECK policy rejects any attempt to write another org's rows.

Callers open the connection/context (global_session / tenant_session) and pass it in;
these functions never open their own, so a request runs in one transaction. Routers are
switched onto this layer in step 5 — the SQLite database.py stays until then.
"""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.engine import Connection

from .db import (
    audit_log,
    documents,
    file_versions,
    org_members,
    organizations,
    part_attributes,
    part_relationships,
    part_revisions,
    parts,
    roles,
    users,
)
from .tenancy import TenantDB


def _one(result) -> Optional[dict]:
    row = result.mappings().first()
    return dict(row) if row else None


def _all(result) -> list[dict]:
    return [dict(r) for r in result.mappings().all()]


# ══════════════════════════════════════════════════════════════════════════════
#  GLOBAL TABLES  (users, organizations, org_members) — via global_session()
# ══════════════════════════════════════════════════════════════════════════════

def create_user(conn: Connection, username: str, email: Optional[str],
                password_hash: Optional[str]) -> dict:
    return _one(conn.execute(
        insert(users)
        .values(username=username, email=email, password_hash=password_hash)
        .returning(users)
    ))


def get_user(conn: Connection, user_id: int) -> Optional[dict]:
    return _one(conn.execute(select(users).where(users.c.id == user_id)))


def get_user_by_username(conn: Connection, username: str) -> Optional[dict]:
    return _one(conn.execute(
        select(users).where(func.lower(users.c.username) == username.lower())
    ))


def get_user_by_email(conn: Connection, email: str) -> Optional[dict]:
    return _one(conn.execute(
        select(users).where(func.lower(users.c.email) == email.lower())
    ))


def touch_user(conn: Connection, user_id: int) -> None:
    conn.execute(update(users).where(users.c.id == user_id).values(last_active=func.now()))


def update_user_password(conn: Connection, user_id: int, password_hash: str,
                         must_change: int = 0) -> None:
    conn.execute(
        update(users).where(users.c.id == user_id)
        .values(password_hash=password_hash, must_change_password=must_change)
    )


def create_org(conn: Connection, name: str, slug: str,
               owner_user_id: Optional[int] = None) -> dict:
    return _one(conn.execute(
        insert(organizations)
        .values(name=name, slug=slug, owner_user_id=owner_user_id)
        .returning(organizations)
    ))


def get_org(conn: Connection, org_id: int) -> Optional[dict]:
    return _one(conn.execute(select(organizations).where(organizations.c.id == org_id)))


def get_org_by_slug(conn: Connection, slug: str) -> Optional[dict]:
    return _one(conn.execute(select(organizations).where(organizations.c.slug == slug)))


def add_member(conn: Connection, org_id: int, user_id: int,
               role_id: Optional[int] = None) -> dict:
    return _one(conn.execute(
        insert(org_members)
        .values(org_id=org_id, user_id=user_id, role_id=role_id)
        .returning(org_members)
    ))


def set_member_role(conn: Connection, org_id: int, user_id: int, role_id: Optional[int]) -> None:
    conn.execute(
        update(org_members)
        .where(org_members.c.org_id == org_id, org_members.c.user_id == user_id)
        .values(role_id=role_id)
    )


# ══════════════════════════════════════════════════════════════════════════════
#  TENANT TABLES  (RLS-scoped) — via TenantDB
# ══════════════════════════════════════════════════════════════════════════════

def _audit(db: TenantDB, user_id: Optional[int], action: str, entity_type: str,
           entity_id: Optional[int], detail: dict) -> None:
    db.conn.execute(
        insert(audit_log).values(
            org_id=db.org_id, user_id=user_id, action=action,
            entity_type=entity_type, entity_id=entity_id,
            detail_json=json.dumps(detail),
        )
    )


# ── Roles (per-org) ───────────────────────────────────────────────────────────

def list_roles(db: TenantDB) -> list[dict]:
    return _all(db.conn.execute(select(roles).order_by(roles.c.name)))


def get_role(db: TenantDB, role_id: int) -> Optional[dict]:
    return _one(db.conn.execute(select(roles).where(roles.c.id == role_id)))


def create_role(db: TenantDB, name: str, abilities: dict) -> dict:
    vals = {"org_id": db.org_id, "name": name}
    for k in ("can_release", "can_view", "can_write", "can_upload", "can_checkout", "can_admin"):
        vals[k] = int(abilities.get(k, 1 if k != "can_admin" else 0))
    return _one(db.conn.execute(insert(roles).values(**vals).returning(roles)))


def update_role(db: TenantDB, role_id: int, name: str, abilities: dict) -> None:
    vals = {"name": name}
    for k in ("can_release", "can_view", "can_write", "can_upload", "can_checkout", "can_admin"):
        vals[k] = int(abilities.get(k, 1 if k != "can_admin" else 0))
    db.conn.execute(update(roles).where(roles.c.id == role_id).values(**vals))


def delete_role(db: TenantDB, role_id: int) -> None:
    db.conn.execute(delete(roles).where(roles.c.id == role_id))


def seed_default_roles(db: TenantDB) -> None:
    """Seed the editable default role set for a newly created org."""
    defaults = [
        ("Owner",    dict(can_release=1, can_view=1, can_write=1, can_upload=1, can_checkout=1, can_admin=1)),
        ("Admin",    dict(can_release=1, can_view=1, can_write=1, can_upload=1, can_checkout=1, can_admin=1)),
        ("Engineer", dict(can_release=1, can_view=1, can_write=1, can_upload=1, can_checkout=1, can_admin=0)),
        ("Viewer",   dict(can_release=0, can_view=1, can_write=0, can_upload=0, can_checkout=0, can_admin=0)),
    ]
    for name, ab in defaults:
        create_role(db, name, ab)


# ── Parts ─────────────────────────────────────────────────────────────────────

def _part_select():
    u1 = users.alias("u1")
    u2 = users.alias("u2")
    return (
        select(
            parts,
            u1.c.username.label("created_by_name"),
            u2.c.username.label("checked_out_by_name"),
        )
        .select_from(
            parts.join(u1, parts.c.created_by == u1.c.id, isouter=True)
                 .join(u2, parts.c.checked_out_by == u2.c.id, isouter=True)
        )
    )


def create_part(db: TenantDB, data: dict, created_by: int) -> dict:
    part = _one(db.conn.execute(
        insert(parts).values(
            org_id=db.org_id,
            part_number=data["part_number"],
            part_name=data["part_name"],
            part_revision=data.get("part_revision", "A"),
            description=data.get("description", ""),
            part_level=data.get("part_level", ""),
            created_by=created_by,
        ).returning(parts)
    ))
    _audit(db, created_by, "create_part", "part", part["id"], {"part_number": part["part_number"]})
    return part


def get_part(db: TenantDB, part_id: int) -> Optional[dict]:
    part = _one(db.conn.execute(_part_select().where(parts.c.id == part_id)))
    if not part:
        return None
    part["attributes"] = get_attributes(db, part_id)
    return part


def get_part_by_number(db: TenantDB, part_number: str) -> Optional[dict]:
    row = _one(db.conn.execute(select(parts.c.id).where(parts.c.part_number == part_number)))
    return get_part(db, row["id"]) if row else None


def list_parts(db: TenantDB, search: str = "", status: str = "",
               checked_out_only: bool = False, page: int = 1, per_page: int = 50) -> dict:
    conds = []
    if search:
        like = f"%{search.lower()}%"
        conds.append(func.lower(parts.c.part_number).like(like) |
                     func.lower(parts.c.part_name).like(like))
    if status:
        conds.append(parts.c.release_status == status)
    if checked_out_only:
        conds.append(parts.c.checked_out_by.isnot(None))

    total = db.conn.execute(
        select(func.count()).select_from(parts).where(*conds)
    ).scalar_one()
    rows = _all(db.conn.execute(
        _part_select().where(*conds)
        .order_by(parts.c.part_number)
        .limit(per_page).offset((page - 1) * per_page)
    ))
    return {"total": total, "page": page, "per_page": per_page, "items": rows}


def update_part(db: TenantDB, part_id: int, data: dict, updated_by: int) -> None:
    db.conn.execute(
        update(parts).where(parts.c.id == part_id).values(
            part_name=data["part_name"],
            description=data.get("description", ""),
            part_level=data.get("part_level", ""),
            updated_at=func.now(),
        )
    )
    _audit(db, updated_by, "update_part", "part", part_id, data)


def delete_part(db: TenantDB, part_id: int, deleted_by: int) -> None:
    db.conn.execute(delete(parts).where(parts.c.id == part_id))
    _audit(db, deleted_by, "delete_part", "part", part_id, {})


def checkout_part(db: TenantDB, part_id: int, user_id: int, station: str = "") -> bool:
    cur = _one(db.conn.execute(
        select(parts.c.checked_out_by).where(parts.c.id == part_id)
    ))
    if cur is None or cur["checked_out_by"] is not None:
        return False
    db.conn.execute(
        update(parts).where(parts.c.id == part_id).values(
            checked_out_by=user_id, checked_out_at=func.now(), checked_out_station=station
        )
    )
    _audit(db, user_id, "checkout", "part", part_id, {"station": station})
    return True


def checkin_part(db: TenantDB, part_id: int, user_id: int) -> None:
    db.conn.execute(
        update(parts).where(parts.c.id == part_id).values(
            checked_out_by=None, checked_out_at=None, checked_out_station=None
        )
    )
    _audit(db, user_id, "checkin", "part", part_id, {})


def release_part(db: TenantDB, part_id: int, user_id: int) -> None:
    db.conn.execute(
        update(parts).where(parts.c.id == part_id)
        .values(release_status="Released", is_locked=1, updated_at=func.now())
    )
    _audit(db, user_id, "release", "part", part_id, {})


def unrelease_part(db: TenantDB, part_id: int, user_id: int) -> None:
    db.conn.execute(
        update(parts).where(parts.c.id == part_id)
        .values(release_status="Prototype", is_locked=0, updated_at=func.now())
    )
    _audit(db, user_id, "unrelease", "part", part_id, {})


def bump_revision(db: TenantDB, part_id: int, user_id: int, description: str = "") -> str:
    part = get_part(db, part_id)
    if not part:
        raise ValueError("Part not found")
    current_rev = part["part_revision"]
    next_rev = chr(ord(current_rev[-1]) + 1) if current_rev else "B"
    db.conn.execute(insert(part_revisions).values(
        org_id=db.org_id, part_id=part_id, revision_label=current_rev,
        description=description, changed_by=user_id, snapshot_json=json.dumps(part, default=str),
    ))
    db.conn.execute(
        update(parts).where(parts.c.id == part_id)
        .values(part_revision=next_rev, is_locked=0, release_status="Prototype", updated_at=func.now())
    )
    _audit(db, user_id, "bump_revision", "part", part_id, {"from": current_rev, "to": next_rev})
    return next_rev


def list_revisions(db: TenantDB, part_id: int) -> list[dict]:
    u = users.alias("u")
    return _all(db.conn.execute(
        select(part_revisions, u.c.username.label("changed_by_name"))
        .select_from(part_revisions.join(u, part_revisions.c.changed_by == u.c.id, isouter=True))
        .where(part_revisions.c.part_id == part_id)
        .order_by(part_revisions.c.changed_at.desc())
    ))


# ── Attributes ────────────────────────────────────────────────────────────────

def get_attributes(db: TenantDB, part_id: int) -> list[dict]:
    return _all(db.conn.execute(
        select(part_attributes).where(part_attributes.c.part_id == part_id)
        .order_by(part_attributes.c.attr_order, part_attributes.c.attr_key)
    ))


def set_attribute(db: TenantDB, part_id: int, key: str, value: str, order: int = 0) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    stmt = pg_insert(part_attributes).values(
        org_id=db.org_id, part_id=part_id, attr_key=key, attr_value=value, attr_order=order
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[part_attributes.c.part_id, part_attributes.c.attr_key],
        set_={"attr_value": stmt.excluded.attr_value, "attr_order": stmt.excluded.attr_order},
    )
    db.conn.execute(stmt)


def delete_attribute(db: TenantDB, part_id: int, key: str) -> None:
    db.conn.execute(delete(part_attributes).where(
        part_attributes.c.part_id == part_id, part_attributes.c.attr_key == key
    ))


# ── Relationships / BOM ───────────────────────────────────────────────────────

def add_relationship(db: TenantDB, parent_id: int, child_id: int, quantity: float = 1.0,
                     rel_type: str = "assembly", notes: str = "", user_id: int = 0) -> dict:
    rel = _one(db.conn.execute(
        insert(part_relationships).values(
            org_id=db.org_id, parent_part_id=parent_id, child_part_id=child_id,
            quantity=quantity, relationship_type=rel_type, notes=notes,
        ).returning(part_relationships)
    ))
    _audit(db, user_id, "add_relationship", "relationship", rel["id"],
           {"parent": parent_id, "child": child_id})
    return rel


def delete_relationship(db: TenantDB, rel_id: int, user_id: int = 0) -> None:
    db.conn.execute(delete(part_relationships).where(part_relationships.c.id == rel_id))
    _audit(db, user_id, "delete_relationship", "relationship", rel_id, {})


def relationship_exists(db: TenantDB, parent_id: int, child_id: int) -> bool:
    return db.conn.execute(
        select(part_relationships.c.id).where(
            part_relationships.c.parent_part_id == parent_id,
            part_relationships.c.child_part_id == child_id,
        )
    ).first() is not None


def get_children(db: TenantDB, part_id: int) -> list[dict]:
    pr, p = part_relationships, parts
    return _all(db.conn.execute(
        select(
            pr.c.id.label("rel_id"), pr.c.quantity, pr.c.relationship_type, pr.c.notes,
            p.c.id, p.c.part_number, p.c.part_name, p.c.part_revision, p.c.release_status,
        )
        .select_from(pr.join(p, pr.c.child_part_id == p.c.id))
        .where(pr.c.parent_part_id == part_id)
        .order_by(p.c.part_number)
    ))


def get_parents(db: TenantDB, part_id: int) -> list[dict]:
    pr, p = part_relationships, parts
    return _all(db.conn.execute(
        select(
            pr.c.id.label("rel_id"), pr.c.relationship_type,
            p.c.id, p.c.part_number, p.c.part_name, p.c.part_revision, p.c.release_status,
        )
        .select_from(pr.join(p, pr.c.parent_part_id == p.c.id))
        .where(pr.c.child_part_id == part_id)
        .order_by(p.c.part_number)
    ))


def get_bom_flat(db: TenantDB, root_part_id: int) -> list[dict]:
    """Recursive BOM as a flat depth-ordered list. RLS scopes it to the active org."""
    return _all(db.conn.execute(
        text(
            """
            WITH RECURSIVE bom(part_id, depth, path) AS (
                SELECT CAST(:root AS BIGINT), 0, CAST(:root AS TEXT)
              UNION ALL
                SELECT pr.child_part_id, b.depth + 1, b.path || '/' || pr.child_part_id
                FROM part_relationships pr JOIN bom b ON pr.parent_part_id = b.part_id
                WHERE b.depth < 20
            )
            SELECT b.depth, pr_link.quantity, pr_link.relationship_type,
                   p.id, p.part_number, p.part_name, p.part_revision, p.release_status
            FROM bom b
            JOIN parts p ON b.part_id = p.id
            LEFT JOIN part_relationships pr_link ON pr_link.child_part_id = b.part_id
            WHERE b.depth > 0
            ORDER BY b.path
            """
        ),
        {"root": root_part_id},
    ))


# ── Documents ─────────────────────────────────────────────────────────────────

def create_document(db: TenantDB, filename: str, stored_path: str, file_type: str,
                    description: str, uploaded_by: int, part_id: Optional[int] = None) -> dict:
    doc = _one(db.conn.execute(
        insert(documents).values(
            org_id=db.org_id, part_id=part_id, filename=filename, stored_path=stored_path,
            file_type=file_type, description=description, uploaded_by=uploaded_by,
        ).returning(documents)
    ))
    _audit(db, uploaded_by, "upload_document", "document", doc["id"],
           {"filename": filename, "part_id": part_id})
    return doc


def get_document(db: TenantDB, doc_id: int) -> Optional[dict]:
    return _one(db.conn.execute(select(documents).where(documents.c.id == doc_id)))


def list_documents(db: TenantDB, part_id: Optional[int] = None) -> list[dict]:
    u = users.alias("u")
    stmt = (
        select(documents, u.c.username.label("uploaded_by_name"))
        .select_from(documents.join(u, documents.c.uploaded_by == u.c.id, isouter=True))
        .order_by(documents.c.uploaded_at.desc())
    )
    if part_id is not None:
        stmt = stmt.where(documents.c.part_id == part_id)
    return _all(db.conn.execute(stmt))


def attach_document(db: TenantDB, part_id: int, doc_id: int) -> None:
    db.conn.execute(update(documents).where(documents.c.id == doc_id).values(part_id=part_id))


def detach_document(db: TenantDB, doc_id: int) -> None:
    db.conn.execute(update(documents).where(documents.c.id == doc_id).values(part_id=None))


def delete_document(db: TenantDB, doc_id: int, user_id: int) -> Optional[str]:
    doc = get_document(db, doc_id)
    if not doc:
        return None
    db.conn.execute(delete(documents).where(documents.c.id == doc_id))
    _audit(db, user_id, "delete_document", "document", doc_id, {"filename": doc["filename"]})
    return doc["stored_path"]


# ── File versions ─────────────────────────────────────────────────────────────

def add_file_version(db: TenantDB, doc_id: int, version_label: str, backup_path: str,
                     file_size: int, saved_by: int) -> dict:
    return _one(db.conn.execute(
        insert(file_versions).values(
            org_id=db.org_id, document_id=doc_id, version_label=version_label,
            backup_path=backup_path, file_size=file_size, saved_by=saved_by,
        ).returning(file_versions)
    ))


def list_file_versions(db: TenantDB, doc_id: int) -> list[dict]:
    return _all(db.conn.execute(
        select(file_versions).where(file_versions.c.document_id == doc_id)
        .order_by(file_versions.c.saved_at.desc())
    ))


def get_old_versions(db: TenantDB, doc_id: int, keep: int) -> list[dict]:
    rows = list_file_versions(db, doc_id)
    return rows[keep:]


def delete_file_version(db: TenantDB, version_id: int) -> Optional[str]:
    row = _one(db.conn.execute(
        select(file_versions.c.backup_path).where(file_versions.c.id == version_id)
    ))
    if not row:
        return None
    db.conn.execute(delete(file_versions).where(file_versions.c.id == version_id))
    return row["backup_path"]


# ── Audit log ─────────────────────────────────────────────────────────────────

def get_audit_log(db: TenantDB, page: int = 1, per_page: int = 100,
                  entity_type: str = "", entity_id: Optional[int] = None) -> dict:
    conds = []
    if entity_type:
        conds.append(audit_log.c.entity_type == entity_type)
    if entity_id is not None:
        conds.append(audit_log.c.entity_id == entity_id)
    total = db.conn.execute(
        select(func.count()).select_from(audit_log).where(*conds)
    ).scalar_one()
    u = users.alias("u")
    rows = _all(db.conn.execute(
        select(audit_log, u.c.username)
        .select_from(audit_log.join(u, audit_log.c.user_id == u.c.id, isouter=True))
        .where(*conds)
        .order_by(audit_log.c.timestamp.desc())
        .limit(per_page).offset((page - 1) * per_page)
    ))
    return {"total": total, "page": page, "per_page": per_page, "items": rows}


def list_attribute_keys(db: TenantDB) -> list[str]:
    rows = db.conn.execute(
        select(part_attributes.c.attr_key).distinct().order_by(part_attributes.c.attr_key)
    ).all()
    return [r[0] for r in rows]


def get_file_version(db: TenantDB, version_id: int) -> Optional[dict]:
    return _one(db.conn.execute(select(file_versions).where(file_versions.c.id == version_id)))


def get_tree(db: TenantDB, part_id: int, depth: int = 0) -> dict:
    part = get_part(db, part_id)
    if not part:
        return {}
    children = get_children(db, part_id) if depth < 20 else []
    return {
        "id": part_id,
        "part_number": part["part_number"],
        "part_name": part["part_name"],
        "part_revision": part["part_revision"],
        "release_status": part["release_status"],
        "depth": depth,
        "children": [get_tree(db, c["id"], depth + 1) for c in children],
    }


def list_all_relationships(db: TenantDB) -> list[dict]:
    pr = part_relationships
    pp, pc = parts.alias("pp"), parts.alias("pc")
    return _all(db.conn.execute(
        select(
            pr.c.id, pr.c.quantity, pr.c.relationship_type, pr.c.notes, pr.c.created_at,
            pp.c.part_number.label("parent_pn"), pp.c.part_name.label("parent_name"),
            pc.c.part_number.label("child_pn"), pc.c.part_name.label("child_name"),
        )
        .select_from(pr.join(pp, pr.c.parent_part_id == pp.c.id)
                       .join(pc, pr.c.child_part_id == pc.c.id))
        .order_by(pp.c.part_number, pc.c.part_number)
    ))


# ── Org members (listed under the active org's tenant_session so roles join works) ──

def list_members(db: TenantDB) -> list[dict]:
    m, u, r = org_members, users, roles
    return _all(db.conn.execute(
        select(
            m.c.user_id, u.c.username, u.c.email, m.c.role_id,
            r.c.name.label("role_name"), m.c.created_at,
        )
        .select_from(
            m.join(u, m.c.user_id == u.c.id)
             .join(r, m.c.role_id == r.c.id, isouter=True)
        )
        .where(m.c.org_id == db.org_id)
        .order_by(u.c.username)
    ))


def get_role_by_name(db: TenantDB, name: str) -> Optional[dict]:
    return _one(db.conn.execute(
        select(roles).where(func.lower(roles.c.name) == name.lower())
    ))
