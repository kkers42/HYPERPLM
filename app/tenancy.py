"""
HYPERPLM — tenant-scoped database access (Phase 2, step 3).

Two connection paths, both connecting as the non-superuser app role so RLS binds:

- tenant_session(org_id): a transaction with `app.current_org` set via SET LOCAL
  (transaction-scoped, so the binding can never leak to another request that later
  reuses the pooled connection). ALL tenant-table access goes through here. A query
  that reaches a tenant table without this context errors (fail closed loud, §12.4).

- global_session(): a transaction WITHOUT the tenant GUC, for the global tables only
  (users, organizations, org_members). Membership must be resolved here — before an
  active org exists (§12.2) — never under tenant_session. `roles` is tenant-scoped, so
  role abilities are read separately, inside the org's tenant_session.

The query methods that operate on tenant tables are attached to TenantDB in step 4.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .db import get_engine


class TenantDB:
    """A database handle bound to one org, backed by a tenant-scoped transaction.

    Step 4 attaches the parts/BOM/document query methods here; they never need to
    pass org_id around because RLS scopes every statement to `app.current_org`.
    """

    def __init__(self, conn: Connection, org_id: int) -> None:
        self.conn = conn
        self.org_id = org_id

    def execute(self, statement, params: Optional[dict] = None):
        return self.conn.execute(statement, params or {})


@contextmanager
def tenant_session(org_id: int) -> Iterator[TenantDB]:
    """Yield a TenantDB inside a transaction scoped to org_id via RLS."""
    if org_id is None:
        raise ValueError("tenant_session requires an org_id")
    org = int(org_id)
    engine = get_engine()
    with engine.begin() as conn:
        # set_config(name, value, is_local=true) is the parameterizable form of
        # SET LOCAL — transaction-scoped, and safe from injection.
        conn.execute(
            text("SELECT set_config('app.current_org', :org, true)"),
            {"org": str(org)},
        )
        yield TenantDB(conn, org)


@contextmanager
def global_session() -> Iterator[Connection]:
    """Yield a transaction WITHOUT the tenant GUC, for global tables only.

    Touching a tenant table here raises (RLS needs app.current_org) — intended.
    """
    engine = get_engine()
    with engine.begin() as conn:
        yield conn


# ── Membership resolution — GLOBAL path only (§12.2) ──────────────────────────

def list_user_orgs(conn: Connection, user_id: int) -> list[dict]:
    """Orgs a user belongs to (global tables; no roles join, so no RLS involved)."""
    rows = conn.execute(
        text(
            """
            SELECT o.id AS org_id, o.name, o.slug, m.role_id
            FROM org_members m
            JOIN organizations o ON o.id = m.org_id
            WHERE m.user_id = :uid
            ORDER BY o.name
            """
        ),
        {"uid": user_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_membership(conn: Connection, user_id: int, org_id: int) -> Optional[dict]:
    """The user's membership row for an org, or None if not a member.

    Returns org_id, user_id, role_id (role *abilities* are read separately under
    tenant_session, since roles is tenant-scoped). This is the authority on whether
    a user may act in an org — resolve it every request; do not trust token claims (§12.1).
    """
    row = conn.execute(
        text(
            """
            SELECT org_id, user_id, role_id
            FROM org_members
            WHERE user_id = :uid AND org_id = :oid
            """
        ),
        {"uid": user_id, "oid": org_id},
    ).mappings().first()
    return dict(row) if row else None


def get_role_abilities(db: TenantDB, role_id: int) -> Optional[dict]:
    """Ability flags for a role, read inside the org's tenant_session (roles is RLS-scoped)."""
    if role_id is None:
        return None
    row = db.execute(
        text(
            """
            SELECT id, name, can_view, can_write, can_release,
                   can_upload, can_checkout, can_admin
            FROM roles
            WHERE id = :rid
            """
        ),
        {"rid": role_id},
    ).mappings().first()
    return dict(row) if row else None
