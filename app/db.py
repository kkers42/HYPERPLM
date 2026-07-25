"""
HYPERPLM — PostgreSQL data layer (SQLAlchemy Core).

Phase 2 schema metadata + engine. This is the single source of truth for the query
layer (steps 3-4) and for Alembic diffs. It does NOT describe row-level security —
RLS policies, FORCE ROW LEVEL SECURITY, the app role, and GRANTs live only in the
hand-authored migrations (Alembic cannot see them; §12.3).

Tenancy (Phase 2, step 2):
- organizations / org_members introduce tenants and per-org membership.
- roles and all data tables carry org_id and are isolated by RLS.
- users are a global identity (no org_id); role is per-membership, not per-user.

The connection URL comes from the DATABASE_URL environment variable, e.g.
    postgresql+psycopg://hyperplm_app:<password>@localhost:5432/hyperplm
Secrets are never hardcoded here (CLAUDE.md rule 0).
"""
from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.engine import Engine, create_engine


# Explicit, stable constraint/index naming so Alembic autogenerate produces
# deterministic names (and so we can reference them in hand-authored migrations).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _pk() -> Column:
    return Column("id", BigInteger, Identity(always=False), primary_key=True)


def _created() -> Column:
    return Column("created_at", TIMESTAMP(timezone=True), server_default=func.now())


def _org_id() -> Column:
    """Tenant key on every RLS-protected table."""
    return Column(
        "org_id", BigInteger,
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
    )


# ── Organizations (tenants) — global, NOT tenant-scoped ───────────────────────
organizations = Table(
    "organizations", metadata,
    _pk(),
    Column("name", Text, nullable=False),
    Column("slug", Text, nullable=False, unique=True),
    Column("owner_user_id", BigInteger, ForeignKey("users.id", ondelete="SET NULL")),
    _created(),
)

# ── Users — global identity, NOT tenant-scoped ────────────────────────────────
users = Table(
    "users", metadata,
    _pk(),
    Column("username", Text, nullable=False, unique=True),
    Column("email", Text, unique=True),
    Column("password_hash", Text),
    # Platform super-user (support/billing). The RLS-bypass path itself is NOT
    # wired until Phase 3 (§12.5) — this flag only marks the capability.
    Column("is_platform_admin", Integer, nullable=False, server_default=text("0")),
    Column("is_active", Integer, nullable=False, server_default=text("1")),
    Column("must_change_password", Integer, nullable=False, server_default=text("0")),
    _created(),
    Column("last_active", TIMESTAMP(timezone=True)),
)

# ── Org membership — global lookup path (resolved BEFORE the tenant GUC, §12.2) ─
org_members = Table(
    "org_members", metadata,
    _pk(),
    Column("org_id", BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("role_id", BigInteger, ForeignKey("roles.id", ondelete="SET NULL")),
    _created(),
    UniqueConstraint("org_id", "user_id", name="uq_org_members_org_user"),
    Index("ix_org_members_user_id", "user_id"),
)

# ── Roles — per-org (tenant-scoped) ───────────────────────────────────────────
roles = Table(
    "roles", metadata,
    _pk(),
    _org_id(),
    Column("name", Text, nullable=False),
    Column("can_release", Integer, nullable=False, server_default=text("1")),
    Column("can_view", Integer, nullable=False, server_default=text("1")),
    Column("can_write", Integer, nullable=False, server_default=text("1")),
    Column("can_upload", Integer, nullable=False, server_default=text("1")),
    Column("can_checkout", Integer, nullable=False, server_default=text("1")),
    Column("can_admin", Integer, nullable=False, server_default=text("0")),
    _created(),
    UniqueConstraint("org_id", "name", name="uq_roles_org_name"),
)

# ── Parts (tenant-scoped) ─────────────────────────────────────────────────────
parts = Table(
    "parts", metadata,
    _pk(),
    _org_id(),
    Column("part_number", Text, nullable=False),
    Column("part_name", Text, nullable=False),
    Column("part_revision", Text, nullable=False, server_default=text("'A'")),
    Column("description", Text),
    Column("part_level", Text),
    Column("release_status", Text, nullable=False, server_default=text("'Prototype'")),
    Column("checked_out_by", BigInteger, ForeignKey("users.id", ondelete="SET NULL")),
    Column("checked_out_at", TIMESTAMP(timezone=True)),
    Column("checked_out_station", Text),
    Column("created_by", BigInteger, ForeignKey("users.id"), nullable=False),
    _created(),
    Column("updated_at", TIMESTAMP(timezone=True), server_default=func.now()),
    Column("is_locked", Integer, nullable=False, server_default=text("0")),
    # Part numbers are unique WITHIN an org — two teams may reuse the same number.
    UniqueConstraint("org_id", "part_number", name="uq_parts_org_part_number"),
    Index("ix_parts_org_release_status", "org_id", "release_status"),
    Index("ix_parts_org_created_by", "org_id", "created_by"),
    Index("ix_parts_checked_out_by", "checked_out_by"),
)

# ── Part Attributes (tenant-scoped) ───────────────────────────────────────────
part_attributes = Table(
    "part_attributes", metadata,
    _pk(),
    _org_id(),
    Column("part_id", BigInteger, ForeignKey("parts.id", ondelete="CASCADE"), nullable=False),
    Column("attr_key", Text, nullable=False),
    Column("attr_value", Text),
    Column("attr_order", Integer, nullable=False, server_default=text("0")),
    _created(),
    UniqueConstraint("part_id", "attr_key", name="uq_part_attributes_part_id_attr_key"),
    Index("ix_part_attributes_part_id", "part_id"),
)

# ── Part Revisions (tenant-scoped) ────────────────────────────────────────────
part_revisions = Table(
    "part_revisions", metadata,
    _pk(),
    _org_id(),
    Column("part_id", BigInteger, ForeignKey("parts.id", ondelete="CASCADE"), nullable=False),
    Column("revision_label", Text, nullable=False),
    Column("description", Text),
    Column("changed_by", BigInteger, ForeignKey("users.id"), nullable=False),
    Column("changed_at", TIMESTAMP(timezone=True), server_default=func.now()),
    Column("snapshot_json", Text),
    Index("ix_part_revisions_part_id", "part_id"),
)

# ── Part Relationships (tenant-scoped) ────────────────────────────────────────
part_relationships = Table(
    "part_relationships", metadata,
    _pk(),
    _org_id(),
    Column("parent_part_id", BigInteger, ForeignKey("parts.id", ondelete="CASCADE"), nullable=False),
    Column("child_part_id", BigInteger, ForeignKey("parts.id", ondelete="CASCADE"), nullable=False),
    Column("quantity", Float, nullable=False, server_default=text("1.0")),
    Column("relationship_type", Text, nullable=False, server_default=text("'assembly'")),
    Column("notes", Text),
    _created(),
    UniqueConstraint("parent_part_id", "child_part_id", name="uq_part_relationships_parent_child"),
    Index("ix_part_relationships_parent", "parent_part_id"),
    Index("ix_part_relationships_child", "child_part_id"),
)

# ── Documents (tenant-scoped) ─────────────────────────────────────────────────
documents = Table(
    "documents", metadata,
    _pk(),
    _org_id(),
    Column("part_id", BigInteger, ForeignKey("parts.id", ondelete="SET NULL")),
    Column("filename", Text, nullable=False),
    Column("stored_path", Text, nullable=False),
    Column("file_type", Text, nullable=False),
    Column("description", Text),
    Column("uploaded_by", BigInteger, ForeignKey("users.id"), nullable=False),
    Column("uploaded_at", TIMESTAMP(timezone=True), server_default=func.now()),
    Index("ix_documents_part_id", "part_id"),
)

# ── File Versions (tenant-scoped) ─────────────────────────────────────────────
file_versions = Table(
    "file_versions", metadata,
    _pk(),
    _org_id(),
    Column("document_id", BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("version_label", Text, nullable=False),
    Column("backup_path", Text, nullable=False),
    Column("file_size", BigInteger),
    Column("saved_by", BigInteger, ForeignKey("users.id", ondelete="SET NULL")),
    Column("saved_at", TIMESTAMP(timezone=True), server_default=func.now()),
    Column("is_current", Integer, nullable=False, server_default=text("0")),
    Index("ix_file_versions_document_id", "document_id"),
)

# ── Audit Log (tenant-scoped) ─────────────────────────────────────────────────
audit_log = Table(
    "audit_log", metadata,
    _pk(),
    _org_id(),
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="SET NULL")),
    Column("action", Text, nullable=False),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", BigInteger),
    Column("detail_json", Text),
    Column("timestamp", TIMESTAMP(timezone=True), server_default=func.now()),
    Index("ix_audit_log_org_time", "org_id", "timestamp"),
    Index("ix_audit_log_entity", "org_id", "entity_type", "entity_id"),
)


# Tables protected by row-level security (org_id + policy). Kept here as the
# authoritative list the migrations enable RLS on and the query layer scopes.
TENANT_TABLES: tuple[str, ...] = (
    "roles",
    "parts",
    "part_attributes",
    "part_revisions",
    "part_relationships",
    "documents",
    "file_versions",
    "audit_log",
)


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Expected e.g. "
            "postgresql+psycopg://hyperplm_app:<password>@localhost:5432/hyperplm"
        )
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Process-wide SQLAlchemy engine with a connection pool."""
    return create_engine(
        database_url(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        future=True,
    )
