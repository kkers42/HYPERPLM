"""
HYPERPLM — account & organization provisioning (service layer).

Orchestrates the global and tenant sessions to register users and stand up orgs.
On signup a user gets their own org, seeded with the default editable role set, and
an Owner membership. Google/Windows first-login auto-provisions the same way.
"""
from __future__ import annotations

import re
from typing import Optional

from . import repo
from .auth import hash_password, verify_password
from .tenancy import global_session, tenant_session


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "org"


def _unique_slug(conn, base: str) -> str:
    slug = _slugify(base)
    candidate, n = slug, 1
    while repo.get_org_by_slug(conn, candidate) is not None:
        n += 1
        candidate = f"{slug}-{n}"
    return candidate


def provision_org(user_id: int, org_name: str) -> int:
    """Create an org owned by user_id: seed default roles + Owner membership. Returns org_id."""
    with global_session() as c:
        slug = _unique_slug(c, org_name)
        org = repo.create_org(c, name=org_name, slug=slug, owner_user_id=user_id)
    org_id = org["id"]
    with tenant_session(org_id) as db:
        repo.seed_default_roles(db)
        owner_role = repo.get_role_by_name(db, "Owner")
    with global_session() as c:
        repo.add_member(c, org_id, user_id, owner_role["id"] if owner_role else None)
    return org_id


def register_local_user(username: str, email: Optional[str], password: str,
                        org_name: Optional[str] = None) -> tuple[dict, int]:
    """Create a local user and their first org. Raises ValueError on conflict."""
    with global_session() as c:
        if repo.get_user_by_username(c, username):
            raise ValueError("Username already exists")
        if email and repo.get_user_by_email(c, email):
            raise ValueError("Email already registered")
        user = repo.create_user(c, username, email, hash_password(password))
    org_id = provision_org(user["id"], org_name or f"{username}'s Team")
    return user, org_id


def verify_local_login(username: str, password: str) -> Optional[dict]:
    with global_session() as c:
        user = repo.get_user_by_username(c, username)
    if not user or not user.get("is_active"):
        return None
    if not verify_password(password, user.get("password_hash")):
        return None
    return user


def get_or_provision_external_user(email: str, username: str) -> dict:
    """For Google/Windows: return the existing user or create one with a personal org."""
    with global_session() as c:
        existing = repo.get_user_by_email(c, email) if email else repo.get_user_by_username(c, username)
    if existing:
        return existing
    with global_session() as c:
        user = repo.create_user(c, username, email or None, None)
    provision_org(user["id"], f"{username}'s Team")
    return user


def default_org_for(user_id: int) -> Optional[int]:
    """The user's active org on login = their first membership (or None if none)."""
    with global_session() as c:
        orgs = _list_user_orgs(c, user_id)
    return orgs[0]["org_id"] if orgs else None


def _list_user_orgs(conn, user_id: int):
    from .tenancy import list_user_orgs
    return list_user_orgs(conn, user_id)
