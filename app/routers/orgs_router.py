"""
HYPERPLM — Organization routes: list your orgs, create one, switch the active org.
"""
from fastapi import APIRouter, Depends, HTTPException, Response

from .. import repo
from ..accounts import provision_org
from ..auth import create_token, make_cookie_kwargs
from ..deps import get_principal
from ..models import MessageResponse, OrgCreate, SwitchOrgRequest
from ..tenancy import get_membership, global_session, list_user_orgs

router = APIRouter(prefix="/api/orgs", tags=["orgs"])


@router.get("")
async def my_orgs(principal: dict = Depends(get_principal)):
    with global_session() as c:
        orgs = list_user_orgs(c, principal["id"])
    return {"active_org_id": principal["active_org_id"], "orgs": orgs}


@router.get("/current")
async def current_org(principal: dict = Depends(get_principal)):
    with global_session() as c:
        org = repo.get_org(c, principal["active_org_id"])
    if not org:
        raise HTTPException(404, "Organization not found")
    return org


@router.post("", status_code=201)
async def create_org(body: OrgCreate, response: Response, principal: dict = Depends(get_principal)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Organization name is required")
    org_id = provision_org(principal["id"], name)
    # Switch the caller into the new org.
    token = create_token(principal["id"], principal["username"], org_id, principal["email"] or "")
    response.set_cookie(value=token, **make_cookie_kwargs())
    return {"org_id": org_id, "active_org_id": org_id}


@router.post("/switch")
async def switch_org(body: SwitchOrgRequest, response: Response, principal: dict = Depends(get_principal)):
    with global_session() as c:
        if not get_membership(c, principal["id"], body.org_id):
            raise HTTPException(403, "You are not a member of that organization")
    token = create_token(principal["id"], principal["username"], body.org_id, principal["email"] or "")
    response.set_cookie(value=token, **make_cookie_kwargs())
    return {"active_org_id": body.org_id}
