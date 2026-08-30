"""
HYPERPLM — Fan accounts (Phase 3, issue #6).

One module, one responsibility (rule 3): everything about a *fan* — an account
that follows teams but belongs to no organization.

This is the other half of the association model. A **member** has an
`org_members` row and reaches the app through the tenant path (RLS, roles,
abilities). A **fan** has a `fan_profiles` row and no membership at all, so the
tenant path refuses them by design (`get_principal` 403s with "No organization
for this account"). Fans therefore use their own dependency and touch only
global tables — plus published team data, which is read through the same
`team_directory` → tenant_session → `visibility='public'` route the anonymous
fan pages use.

A person can be both: a race engineer may follow rival teams. `fan_type` records
which they primarily are; it never grants access on its own.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, func, insert, select, update

from . import repo
from .auth import hash_password, verify_password
from .db import (
    badges, fan_profiles, follows, org_members, point_ledger, team_directory,
    user_badges, users,
)
from .tenancy import global_session

# Points awarded for milestones the fan layer recognises today.
POINTS_FIRST_FOLLOW = 50
POINTS_FOLLOW = 10


class FanError(ValueError):
    """Registration/validation problem the caller should surface verbatim."""


# ── registration ──────────────────────────────────────────────────────────────

def register_fan(username: str, password: str, display_name: str = "",
                 email: Optional[str] = None) -> dict:
    """Create a spectator: a user with a fan profile and NO organization.

    Deliberately does not call accounts.register_local_user, which provisions an
    org — that would make every fan a tenant and defeat the whole distinction.
    """
    username = (username or "").strip()
    if len(username) < 3:
        raise FanError("Username must be at least 3 characters")
    if len(password or "") < 8:
        raise FanError("Password must be at least 8 characters")

    with global_session() as c:
        if repo.get_user_by_username(c, username):
            raise FanError("Username already exists")
        if email and repo.get_user_by_email(c, email):
            raise FanError("Email already registered")
        user = repo.create_user(c, username, email, hash_password(password))
        c.execute(insert(fan_profiles).values(
            user_id=user["id"],
            display_name=(display_name or username).strip()[:80],
            fan_type="spectator",
            points=0,
        ))
    return user


def profile_for(user_id: int) -> Optional[dict]:
    """The fan profile, or None if this user has never been a fan."""
    with global_session() as c:
        row = c.execute(select(fan_profiles).where(
            fan_profiles.c.user_id == user_id)).first()
    return dict(row._mapping) if row else None


def ensure_profile(user_id: int, display_name: str = "") -> dict:
    """Give an existing account a fan profile (a team member choosing to follow).
    Their `fan_type` is 'member' — they still belong to an org."""
    existing = profile_for(user_id)
    if existing:
        return existing
    with global_session() as c:
        is_member = c.execute(
            select(func.count()).select_from(org_members)
            .where(org_members.c.user_id == user_id)).scalar_one() > 0
        c.execute(insert(fan_profiles).values(
            user_id=user_id, display_name=display_name[:80],
            fan_type="member" if is_member else "spectator", points=0))
    return profile_for(user_id)


# ── points ────────────────────────────────────────────────────────────────────

def award(user_id: int, delta: int, reason: str, ref_type: str = "",
          ref_id: Optional[int] = None) -> None:
    """Append to the ledger and keep the denormalised total in step."""
    with global_session() as c:
        c.execute(insert(point_ledger).values(
            user_id=user_id, delta=delta, reason=reason,
            ref_type=ref_type, ref_id=ref_id))
        c.execute(update(fan_profiles)
                  .where(fan_profiles.c.user_id == user_id)
                  .values(points=fan_profiles.c.points + delta))


def grant_badge(user_id: int, code: str) -> bool:
    """Award a badge once. Returns True if it was newly granted."""
    with global_session() as c:
        badge = c.execute(select(badges.c.id).where(badges.c.code == code)).first()
        if not badge:
            return False
        already = c.execute(select(user_badges.c.id).where(
            user_badges.c.user_id == user_id,
            user_badges.c.badge_id == badge[0])).first()
        if already:
            return False
        c.execute(insert(user_badges).values(user_id=user_id, badge_id=badge[0]))
    return True


# ── following ─────────────────────────────────────────────────────────────────

def _published_team(slug: str) -> Optional[dict]:
    """Resolve a slug to a PUBLISHED team. You cannot follow a private team —
    that would leak the fact it exists."""
    with global_session() as c:
        row = c.execute(select(team_directory).where(
            team_directory.c.slug == slug,
            team_directory.c.is_public == 1)).first()
    return dict(row._mapping) if row else None


def follow(user_id: int, slug: str) -> dict:
    team = _published_team(slug)
    if not team:
        raise FanError("Team not found")
    with global_session() as c:
        existing = c.execute(select(follows.c.id).where(
            follows.c.user_id == user_id,
            follows.c.team_id == team["team_id"])).first()
        if existing:
            return {"following": True, "already": True}
        c.execute(insert(follows).values(
            user_id=user_id, team_id=team["team_id"]))
        first = c.execute(
            select(func.count()).select_from(follows)
            .where(follows.c.user_id == user_id)).scalar_one() == 1

    if first:
        award(user_id, POINTS_FIRST_FOLLOW, "First team followed", "follow")
        grant_badge(user_id, "first_follow")
    else:
        award(user_id, POINTS_FOLLOW, f"Followed {team['name']}", "follow")
    return {"following": True, "already": False, "first_follow": first}


def unfollow(user_id: int, slug: str) -> dict:
    team = _published_team(slug)
    if not team:
        raise FanError("Team not found")
    with global_session() as c:
        c.execute(delete(follows).where(
            follows.c.user_id == user_id,
            follows.c.team_id == team["team_id"]))
    # Points already earned are not clawed back — a ledger is a history.
    return {"following": False}


def following(user_id: int) -> list[dict]:
    """Published teams this fan follows (directory only — no tenant data)."""
    with global_session() as c:
        rows = c.execute(
            select(team_directory.c.slug, team_directory.c.name,
                   team_directory.c.car_number, team_directory.c.series,
                   team_directory.c["class"])
            .select_from(follows.join(
                team_directory, team_directory.c.team_id == follows.c.team_id))
            .where(follows.c.user_id == user_id,
                   team_directory.c.is_public == 1)
            .order_by(team_directory.c.car_number)
        ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── the fan's own view ────────────────────────────────────────────────────────

def home(user_id: int) -> dict:
    prof = profile_for(user_id)
    if not prof:
        return {}
    with global_session() as c:
        earned = c.execute(
            select(badges.c.code, badges.c.name, badges.c.description,
                   user_badges.c.earned_at)
            .select_from(user_badges.join(badges, badges.c.id == user_badges.c.badge_id))
            .where(user_badges.c.user_id == user_id)
            .order_by(user_badges.c.earned_at)).fetchall()
        all_badges = c.execute(
            select(badges.c.code, badges.c.name, badges.c.description)
            .order_by(badges.c.id)).fetchall()
        ledger = c.execute(
            select(point_ledger.c.delta, point_ledger.c.reason,
                   point_ledger.c.created_at)
            .where(point_ledger.c.user_id == user_id)
            .order_by(point_ledger.c.created_at.desc()).limit(10)).fetchall()
        is_member = c.execute(
            select(func.count()).select_from(org_members)
            .where(org_members.c.user_id == user_id)).scalar_one() > 0

    earned_codes = {r._mapping["code"] for r in earned}
    return {
        "profile": prof,
        "is_member": is_member,
        "following": following(user_id),
        "badges_earned": [dict(r._mapping) for r in earned],
        "badges_available": [
            dict(r._mapping) | {"earned": r._mapping["code"] in earned_codes}
            for r in all_badges
        ],
        "recent_points": [dict(r._mapping) for r in ledger],
    }
