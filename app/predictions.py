"""
HYPERPLM — Prediction questions and scoring (Phase 3, issue #7).

One module, one responsibility (rule 3): the predict-and-score loop that turns
Paddock Points from a follow bonus into an actual game.

Shape of it:
  a team poses a question on one of its sessions  ->  fans pick while it is open
  ->  the team locks it  ->  the team resolves it with the correct answer
  ->  every correct pick is paid once, and the ledger records why.

Two rules are enforced here rather than trusted to callers:
  * a question only ever pays out once (resolving twice is a no-op), and
  * a fan may only pick while the question is `open`, and only from its options.

Questions live in a GLOBAL table because anonymous fans read them with no active
org; the public routes only expose questions whose session is published.
"""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import func, insert, select, update

from . import fans
from .db import prediction_questions as PQ
from .db import predictions as P
from .db import run_sessions, team_directory, teams
from .tenancy import global_session, tenant_session

BADGE_TEN_CORRECT = "ten_correct"
CORRECT_FOR_BADGE = 10


class PredictionError(ValueError):
    """Something the caller should see verbatim."""


def _assert_owns_team(db, team_id: int) -> None:
    """Prove the caller's org owns this team by looking it up through the tenant
    session. RLS scopes the query, so another org's team simply is not there.
    A caller-supplied team_id is never trusted on its own."""
    if not db.execute(select(teams.c.id).where(teams.c.id == team_id)).first():
        raise PredictionError("Question not found")


def _row(r) -> Optional[dict]:
    return dict(r._mapping) if r else None


def _decode(q: dict) -> dict:
    try:
        q["options"] = json.loads(q.get("options") or "[]")
    except ValueError:
        q["options"] = []
    return q


# ── team side (called inside a tenant session) ────────────────────────────────

def create_question(db, run_session_id: int, team_id: int, prompt: str,
                    options: list[str], points: int = 100) -> dict:
    """Pose a question on one of this team's sessions.

    The session is looked up through the tenant session, so a team can only ever
    attach a question to a session RLS already lets it see.
    """
    owns = db.execute(select(run_sessions.c.id)
                      .where(run_sessions.c.id == run_session_id)).first()
    if not owns:
        raise PredictionError("Session not found")
    if not prompt.strip():
        raise PredictionError("The question needs a prompt")
    opts = [o.strip() for o in options if o and o.strip()]
    if len(opts) < 2:
        raise PredictionError("Give at least two options")
    with global_session() as c:
        r = c.execute(insert(PQ).values(
            run_session_id=run_session_id, team_id=team_id, prompt=prompt.strip(),
            options=json.dumps(opts), points=points, status="open",
        ).returning(PQ)).first()
    return _decode(_row(r))


def set_status(db, question_id: int, team_id: int, status: str) -> dict:
    _assert_owns_team(db, team_id)
    if status not in ("open", "locked"):
        raise PredictionError("Use resolve() to close a question")
    with global_session() as c:
        q = _row(c.execute(select(PQ).where(PQ.c.id == question_id)).first())
        if not q or q["team_id"] != team_id:
            raise PredictionError("Question not found")
        if q["status"] == "resolved":
            raise PredictionError("That question is already resolved")
        c.execute(update(PQ).where(PQ.c.id == question_id).values(status=status))
    q["status"] = status
    return _decode(q)


def resolve(db, question_id: int, team_id: int, correct_answer: str) -> dict:
    """Set the answer and pay every correct pick — exactly once."""
    _assert_owns_team(db, team_id)
    with global_session() as c:
        q = _row(c.execute(select(PQ).where(PQ.c.id == question_id)).first())
        if not q or q["team_id"] != team_id:
            raise PredictionError("Question not found")
        if q["status"] == "resolved":
            # Idempotent on purpose: a double-click must not pay twice.
            return _decode(q) | {"already_resolved": True, "awarded": 0}
        opts = json.loads(q["options"] or "[]")
        if opts and correct_answer not in opts:
            raise PredictionError("The answer must be one of the options")

        c.execute(update(PQ).where(PQ.c.id == question_id).values(
            status="resolved", correct_answer=correct_answer, resolved_at=func.now()))
        picks = c.execute(select(P.c.id, P.c.user_id, P.c.answer)
                          .where(P.c.question_id == question_id)).fetchall()

    awarded = 0
    for pick in picks:
        d = dict(pick._mapping)
        right = d["answer"] == correct_answer
        with global_session() as c:
            c.execute(update(P).where(P.c.id == d["id"]).values(
                is_correct=1 if right else 0,
                points_awarded=q["points"] if right else 0))
        if right:
            fans.award(d["user_id"], q["points"],
                       f"Correct call: {q['prompt'][:60]}", "prediction", question_id)
            awarded += 1
            _maybe_badge(d["user_id"])
    q.update(status="resolved", correct_answer=correct_answer)
    return _decode(q) | {"already_resolved": False, "awarded": awarded,
                         "picks": len(picks)}


def _maybe_badge(user_id: int) -> None:
    with global_session() as c:
        n = c.execute(select(func.count()).select_from(P)
                      .where(P.c.user_id == user_id, P.c.is_correct == 1)).scalar_one()
    if n >= CORRECT_FOR_BADGE:
        fans.grant_badge(user_id, BADGE_TEN_CORRECT)


def questions_for_team(db, team_id: int) -> list[dict]:
    _assert_owns_team(db, team_id)
    with global_session() as c:
        rows = c.execute(
            select(PQ, func.count(P.c.id).label("picks"))
            .select_from(PQ.outerjoin(P, P.c.question_id == PQ.c.id))
            .where(PQ.c.team_id == team_id)
            .group_by(PQ.c.id).order_by(PQ.c.created_at.desc())).fetchall()
    return [_decode(dict(r._mapping)) for r in rows]


# ── public / fan side ─────────────────────────────────────────────────────────

def open_questions() -> list[dict]:
    """Questions fans can answer right now, on PUBLISHED sessions only."""
    with global_session() as c:
        rows = c.execute(
            select(PQ, team_directory.c.slug, team_directory.c.name.label("team"),
                   team_directory.c.car_number)
            .select_from(PQ.join(team_directory, team_directory.c.team_id == PQ.c.team_id))
            .where(PQ.c.status == "open", team_directory.c.is_public == 1)
            .order_by(PQ.c.created_at.desc())).fetchall()
    out = []
    for r in rows:
        q = _decode(dict(r._mapping))
        # never leak the answer key while the question is live
        q.pop("correct_answer", None)
        out.append(q)
    return out


def submit(user_id: int, question_id: int, answer: str) -> dict:
    with global_session() as c:
        q = _row(c.execute(select(PQ).where(PQ.c.id == question_id)).first())
        if not q:
            raise PredictionError("Question not found")
        if q["status"] != "open":
            raise PredictionError("That question is closed")
        opts = json.loads(q["options"] or "[]")
        if opts and answer not in opts:
            raise PredictionError("Pick one of the options")
        existing = c.execute(select(P.c.id).where(
            P.c.user_id == user_id, P.c.question_id == question_id)).first()
        changed = bool(existing)
        if existing:
            c.execute(update(P).where(P.c.id == existing[0]).values(answer=answer))
        else:
            c.execute(insert(P).values(user_id=user_id, question_id=question_id,
                                       run_session_id=q["run_session_id"],
                                       question=q["prompt"], answer=answer))
    return {"question_id": question_id, "answer": answer, "changed": changed}


def my_predictions(user_id: int) -> list[dict]:
    with global_session() as c:
        rows = c.execute(
            select(P.c.id, P.c.answer, P.c.is_correct, P.c.points_awarded,
                   PQ.c.prompt, PQ.c.status, PQ.c.correct_answer, PQ.c.points,
                   team_directory.c.name.label("team"), team_directory.c.slug)
            .select_from(P.join(PQ, PQ.c.id == P.c.question_id)
                          .join(team_directory, team_directory.c.team_id == PQ.c.team_id))
            .where(P.c.user_id == user_id)
            .order_by(P.c.created_at.desc()).limit(25)).fetchall()
    return [dict(r._mapping) for r in rows]
