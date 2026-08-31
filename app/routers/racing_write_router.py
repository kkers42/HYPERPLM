"""
HYPERPLM — Racing write routes.

Separate module from the read router (rule 3) because the gating differs: reads
need `view`, writes need `write`, and publishing to the fan side needs `release`
— publishing is a deliberate act, so it sits behind the same ability that
releases a part.

Every insert goes through app/racing.py, which sets org_id from the tenant
session; RLS WITH CHECK rejects a cross-tenant write at the database.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import racing
from ..deps import RequestContext, require_ability

router = APIRouter(prefix="/api/racing", tags=["racing-write"])


class TeamIn(BaseModel):
    team_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    series: str = ""
    car_number: str = ""
    class_: str = Field("", alias="class")
    model_config = {"populate_by_name": True}


class CarIn(BaseModel):
    team_id: int
    chassis: str
    model: str = ""
    homologation: str = ""


class DriverIn(BaseModel):
    team_id: int
    name: str
    country: str = ""


class EventIn(BaseModel):
    team_id: int
    track_id: int
    name: str
    round: Optional[int] = None
    starts_on: Optional[str] = None
    series_id: Optional[int] = None


class SessionIn(BaseModel):
    event_id: int
    car_id: Optional[int] = None
    session_type: str
    session_date: Optional[str] = None
    visibility: str = "public"


class LapIn(BaseModel):
    run_session_id: int
    lap_no: int
    lap_time_ms: int = Field(gt=0)
    driver_id: Optional[int] = None
    tire_compound: str = ""


class SetupIn(BaseModel):
    setup_key: str
    team_id: int
    track_id: int
    baseline: str = ""
    revision_label: str = "A"
    notes: str = ""


class ValueIn(BaseModel):
    attr_key: str
    attr_value: str
    group_name: str = ""
    unit: str = ""


class ChecklistIn(BaseModel):
    team_id: int
    name: str
    items: list[str] = []
    is_template: bool = False


class VisibilityIn(BaseModel):
    visibility: str = Field(pattern="^(public|team)$")


class PublicIn(BaseModel):
    is_public: bool


class HoursIn(BaseModel):
    hours_used: float = Field(ge=0)


@router.post("/teams", status_code=201)
async def create_team(body: TeamIn, ctx: RequestContext = Depends(require_ability("write"))):
    return racing.create_team(ctx.db, body.model_dump(by_alias=True))


@router.post("/cars", status_code=201)
async def create_car(body: CarIn, ctx: RequestContext = Depends(require_ability("write"))):
    return racing.create_car(ctx.db, body.model_dump())


@router.post("/drivers", status_code=201)
async def create_driver(body: DriverIn, ctx: RequestContext = Depends(require_ability("write"))):
    return racing.create_driver(ctx.db, body.model_dump())


@router.post("/events", status_code=201)
async def create_event(body: EventIn, ctx: RequestContext = Depends(require_ability("write"))):
    return racing.create_event(ctx.db, body.model_dump())


@router.post("/sessions", status_code=201)
async def create_session(body: SessionIn, ctx: RequestContext = Depends(require_ability("write"))):
    return racing.create_session(ctx.db, body.model_dump(), ctx.user["id"])


@router.post("/laps", status_code=201)
async def add_lap(body: LapIn, ctx: RequestContext = Depends(require_ability("write"))):
    """Log a lap. PB/fastest flags are recomputed for the session automatically."""
    return racing.add_lap(ctx.db, body.model_dump())


@router.patch("/sessions/{session_id}/visibility")
async def session_visibility(session_id: int, body: VisibilityIn,
                             ctx: RequestContext = Depends(require_ability("release"))):
    racing.set_session_visibility(ctx.db, session_id, body.visibility)
    return {"message": "ok", "visibility": body.visibility}


@router.post("/setups", status_code=201)
async def create_setup(body: SetupIn, ctx: RequestContext = Depends(require_ability("write"))):
    return racing.create_setup(ctx.db, body.model_dump(), ctx.user["id"])


@router.put("/setups/{setup_id}/values")
async def set_value(setup_id: int, body: ValueIn,
                    ctx: RequestContext = Depends(require_ability("write"))):
    racing.set_setup_value(ctx.db, setup_id, body.attr_key, body.attr_value,
                           body.group_name, body.unit)
    return {"message": "ok"}


@router.post("/setups/{setup_key}/clone", status_code=201)
async def clone_setup(setup_key: str, new_key: str,
                      ctx: RequestContext = Depends(require_ability("write"))):
    out = racing.clone_setup(ctx.db, setup_key, new_key, ctx.user["id"])
    if not out:
        raise HTTPException(404, "Setup not found")
    return out


@router.patch("/setups/{setup_id}/visibility")
async def setup_visibility(setup_id: int, body: VisibilityIn,
                           ctx: RequestContext = Depends(require_ability("release"))):
    racing.set_setup_visibility(ctx.db, setup_id, body.visibility)
    return {"message": "ok", "visibility": body.visibility}


@router.post("/checklists", status_code=201)
async def create_checklist(body: ChecklistIn,
                           ctx: RequestContext = Depends(require_ability("write"))):
    return racing.create_checklist(ctx.db, body.model_dump())


@router.patch("/checklist-items/{item_id}")
async def tick_item(item_id: int, done: bool = True,
                    ctx: RequestContext = Depends(require_ability("write"))):
    """Sign-off records who and when."""
    racing.toggle_checklist_item(ctx.db, item_id, done, ctx.user["id"])
    return {"message": "ok", "is_done": done}


@router.patch("/parts/{part_usage_id}/hours")
async def log_hours(part_usage_id: int, body: HoursIn,
                    ctx: RequestContext = Depends(require_ability("write"))):
    """status (ok/service_soon/over) is derived by a DB trigger, not sent in."""
    racing.log_part_hours(ctx.db, part_usage_id, body.hours_used)
    return {"message": "ok"}


@router.patch("/teams/{team_id}/public")
async def publish_team(team_id: int, body: PublicIn,
                       ctx: RequestContext = Depends(require_ability("release"))):
    """Publish a team to the fan side. Opt-in, and reversible."""
    try:
        racing.set_team_public(ctx.db, team_id, body.is_public)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"message": "ok", "is_public": body.is_public}


# ── Prediction questions (issue #7) ───────────────────────────────────────────
# Posing a question is a write; resolving one pays out points to fans, so it
# needs `release` — the same bar as publishing.

class QuestionIn(BaseModel):
    run_session_id: int
    prompt: str = Field(min_length=3, max_length=300)
    options: list[str] = Field(min_length=2)
    points: int = Field(100, ge=1, le=1000)


class ResolveIn(BaseModel):
    correct_answer: str


class StatusIn(BaseModel):
    status: str = Field(pattern="^(open|locked)$")


@router.get("/questions")
async def list_questions(team_id: int, ctx: RequestContext = Depends(require_ability("view"))):
    from .. import predictions
    try:
        return predictions.questions_for_team(ctx.db, team_id)
    except predictions.PredictionError as e:
        raise HTTPException(404, str(e))


@router.post("/questions", status_code=201)
async def create_question(body: QuestionIn, team_id: int,
                          ctx: RequestContext = Depends(require_ability("write"))):
    from .. import predictions
    try:
        return predictions.create_question(ctx.db, body.run_session_id, team_id,
                                           body.prompt, body.options, body.points)
    except predictions.PredictionError as e:
        raise HTTPException(400, str(e))


@router.patch("/questions/{qid}/status")
async def question_status(qid: int, team_id: int, body: StatusIn,
                          ctx: RequestContext = Depends(require_ability("write"))):
    from .. import predictions
    try:
        return predictions.set_status(ctx.db, qid, team_id, body.status)
    except predictions.PredictionError as e:
        raise HTTPException(400, str(e))


@router.post("/questions/{qid}/resolve")
async def resolve_question(qid: int, team_id: int, body: ResolveIn,
                           ctx: RequestContext = Depends(require_ability("release"))):
    """Set the answer and pay every correct pick. Idempotent: never pays twice."""
    from .. import predictions
    try:
        return predictions.resolve(ctx.db, qid, team_id, body.correct_answer)
    except predictions.PredictionError as e:
        raise HTTPException(400, str(e))


class TemplateField(BaseModel):
    group_name: str = ""
    attr_key: str
    unit: str = ""
    default_value: str = ""


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    series_id: Optional[int] = None
    description: str = ""
    fields: list[TemplateField] = []


class StarterIn(BaseModel):
    kind: str
    series_id: Optional[int] = None


@router.post("/templates", status_code=201)
async def create_template(body: TemplateIn,
                          ctx: RequestContext = Depends(require_ability("write"))):
    return racing.create_template(
        ctx.db, body.name, [f.model_dump() for f in body.fields],
        body.series_id, body.description, ctx.user["id"])


@router.post("/templates/starter", status_code=201)
async def create_starter(body: StarterIn,
                         ctx: RequestContext = Depends(require_ability("write"))):
    """Instantiate a built-in field list as this org's own editable template."""
    try:
        return racing.create_starter_template(ctx.db, body.kind, body.series_id,
                                              ctx.user["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/setups/{setup_id}/apply-template/{template_id}")
async def apply_template(setup_id: int, template_id: int,
                         ctx: RequestContext = Depends(require_ability("write"))):
    """Lay a template's fields onto a sheet, ready to fill in."""
    n = racing.apply_template(ctx.db, setup_id, template_id)
    return {"message": "ok", "fields_added": n}

