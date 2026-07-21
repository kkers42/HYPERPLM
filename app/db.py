"""
HYPERPLM — PostgreSQL data layer (SQLAlchemy Core).

Phase 2, step 1: schema metadata + engine only. This ports the existing
(single-tenant) schema to PostgreSQL. Tenancy (organizations, org_id columns) and
row-level security are added in step 2; the per-request org-scoped connection and
the query rewrite land in steps 3-4. Until then the running app still uses the
SQLite layer in database.py — this module is additive and not yet wired in.

The connection URL comes from the DATABASE_URL environment variable, e.g.
    postgresql+psycopg://hyperplm:<password>@localhost:5432/hyperplm
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
    Integer,
    MetaData,
    String,
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


# ── Roles ─────────────────────────────────────────────────────────────────────
roles = Table(
    "roles", metadata,
    _pk(),
    Column("name", Text, nullable=False, unique=True),
    Column("can_release", Integer, nullable=False, server_default=text("1")),
    Column("can_view", Integer, nullable=False, server_default=text("1")),
    Column("can_write", Integer, nullable=False, server_default=text("1")),
    Column("can_upload", Integer, nullable=False, server_default=text("1")),
    Column("can_checkout", Integer, nullable=False, server_default=text("1")),
    Column("can_admin", Integer, nullable=False, server_default=text("0")),
    _created(),
)

# ── Users ─────────────────────────────────────────────────────────────────────
users = Table(
    "users", metadata,
    _pk(),
    Column("username", Text, nullable=False, unique=True),
    Column("email", Text, unique=True),
    Column("password_hash", Text),
    Column("role_id", BigInteger, ForeignKey("roles.id", ondelete="SET NULL")),
    Column("is_active", Integer, nullable=False, server_default=text("1")),
    Column("must_change_password", Integer, nullable=False, server_default=text("0")),
    _created(),
    Column("last_active", TIMESTAMP(timezone=True)),
)

# ── Parts ─────────────────────────────────────────────────────────────────────
parts = Table(
    "parts", metadata,
    _pk(),
    Column("part_number", Text, nullable=False, unique=True),
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
)

# ── Part Attributes ───────────────────────────────────────────────────────────
part_attributes = Table(
    "part_attributes", metadata,
    _pk(),
    Column("part_id", BigInteger, ForeignKey("parts.id", ondelete="CASCADE"), nullable=False),
    Column("attr_key", Text, nullable=False),
    Column("attr_value", Text),
    Column("attr_order", Integer, nullable=False, server_default=text("0")),
    _created(),
    UniqueConstraint("part_id", "attr_key", name="uq_part_attributes_part_id_attr_key"),
)

# ── Part Revisions ────────────────────────────────────────────────────────────
part_revisions = Table(
    "part_revisions", metadata,
    _pk(),
    Column("part_id", BigInteger, ForeignKey("parts.id", ondelete="CASCADE"), nullable=False),
    Column("revision_label", Text, nullable=False),
    Column("description", Text),
    Column("changed_by", BigInteger, ForeignKey("users.id"), nullable=False),
    Column("changed_at", TIMESTAMP(timezone=True), server_default=func.now()),
    Column("snapshot_json", Text),
)

# ── Part Relationships ────────────────────────────────────────────────────────
part_relationships = Table(
    "part_relationships", metadata,
    _pk(),
    Column("parent_part_id", BigInteger, ForeignKey("parts.id", ondelete="CASCADE"), nullable=False),
    Column("child_part_id", BigInteger, ForeignKey("parts.id", ondelete="CASCADE"), nullable=False),
    Column("quantity", Float, nullable=False, server_default=text("1.0")),
    Column("relationship_type", Text, nullable=False, server_default=text("'assembly'")),
    Column("notes", Text),
    _created(),
    UniqueConstraint("parent_part_id", "child_part_id", name="uq_part_relationships_parent_child"),
)

# ── Documents ─────────────────────────────────────────────────────────────────
documents = Table(
    "documents", metadata,
    _pk(),
    Column("part_id", BigInteger, ForeignKey("parts.id", ondelete="SET NULL")),
    Column("filename", Text, nullable=False),
    Column("stored_path", Text, nullable=False),
    Column("file_type", Text, nullable=False),
    Column("description", Text),
    Column("uploaded_by", BigInteger, ForeignKey("users.id"), nullable=False),
    Column("uploaded_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

# ── File Versions ─────────────────────────────────────────────────────────────
file_versions = Table(
    "file_versions", metadata,
    _pk(),
    Column("document_id", BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("version_label", Text, nullable=False),
    Column("backup_path", Text, nullable=False),
    Column("file_size", BigInteger),
    Column("saved_by", BigInteger, ForeignKey("users.id", ondelete="SET NULL")),
    Column("saved_at", TIMESTAMP(timezone=True), server_default=func.now()),
    Column("is_current", Integer, nullable=False, server_default=text("0")),
)

# ── Audit Log ─────────────────────────────────────────────────────────────────
audit_log = Table(
    "audit_log", metadata,
    _pk(),
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="SET NULL")),
    Column("action", Text, nullable=False),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", BigInteger),
    Column("detail_json", Text),
    Column("timestamp", TIMESTAMP(timezone=True), server_default=func.now()),
)


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Expected e.g. "
            "postgresql+psycopg://hyperplm:<password>@localhost:5432/hyperplm"
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
