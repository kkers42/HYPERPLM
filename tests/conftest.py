"""
HYPERPLM — pytest fixtures for the tenant-isolation suite (Phase 2, step 7).

Runs against a real PostgreSQL migrated to head. Required environment:
  DATABASE_URL          app runtime role (hyperplm_app) — RLS applies
  ALEMBIC_DATABASE_URL  DB owner — used only to TRUNCATE between tests
  SECRET_KEY            any >= 32 chars

Each test starts from an empty database (TRUNCATE ... CASCADE as the owner).
"""
import os
import tempfile

import pytest

# Must be set before importing any app module (config reads env at import time).
os.environ.setdefault("AUTH_MODE", "local")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-chars-long-000")
os.environ.setdefault("FILES_ROOT", tempfile.mkdtemp(prefix="hpz_test_files_"))

from sqlalchemy import create_engine, text  # noqa: E402

_OWNER_URL = os.environ.get("ALEMBIC_DATABASE_URL")


@pytest.fixture(scope="session")
def owner_engine():
    if not _OWNER_URL or not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL and ALEMBIC_DATABASE_URL must be set for the isolation suite")
    eng = create_engine(_OWNER_URL)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean(owner_engine):
    """Empty every table before each test (owner bypasses RLS) and reset in-process
    rate-limiter state so registrations from the shared test IP don't accumulate."""
    with owner_engine.begin() as c:
        c.execute(text("TRUNCATE organizations, users RESTART IDENTITY CASCADE"))
    from app import security
    security._login_limiter._hits.clear()
    security._register_limiter._hits.clear()
    yield


@pytest.fixture
def make_client():
    """Factory for fresh TestClients (each has its own cookie jar = its own session)."""
    from fastapi.testclient import TestClient
    from app.main import app
    clients = []

    def _new():
        tc = TestClient(app)
        clients.append(tc)
        return tc
    yield _new
    for tc in clients:
        tc.close()


@pytest.fixture
def reg():
    """Register a user + their org on a client; return the active org id."""
    def _reg(client, username, org_name):
        r = client.post("/auth/register", json={
            "username": username, "password": "hunter2pass", "org_name": org_name,
        })
        assert r.status_code == 200, r.text
        return r.json()["active_org_id"]
    return _reg


@pytest.fixture
def part():
    """Create a part on a client; return its dict."""
    def _part(client, number, name="Widget"):
        r = client.post("/api/parts", json={"part_number": number, "part_name": name})
        assert r.status_code == 201, r.text
        return r.json()
    return _part
