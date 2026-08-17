from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from workers import fetch

import firmware
from config import var


async def optional_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict | None:
    env = request.scope["env"]
    rebble_auth_host = var(env, "REBBLE_AUTH")
    if not authorization or not rebble_auth_host:
        return None
    result = await fetch(f"{rebble_auth_host}/api/v1/me", headers={"Authorization": authorization})
    if result.status != 200:
        raise HTTPException(401)
    return await result.json()


app = FastAPI(title="cohorts", description="The Rebble cohorts API")


@dataclass
class CohortRequest:
    """Everything a generator is allowed to look at, resolved once per request."""

    db: Any
    hardware: str | None
    include_recovery: bool


async def generate_pipeline_api(req: CohortRequest):
    return {"host": "pipeline-api.rebble.io"}


async def generate_linked_services(req: CohortRequest):
    return {"enabled_providers": []}


async def generate_health_insights(req: CohortRequest):
    return {
        "url": "https://cohorts-storage.lavate.ch/health-insights/v11/insights.pbhi",
        "version": 11,
    }


async def generate_fw(req: CohortRequest):
    if req.hardware is None:
        raise HTTPException(400)
    kinds = ("normal", "recovery") if req.include_recovery else ("normal",)

    rows = await firmware.latest_by_kind(req.db, req.hardware, kinds)
    response = {kind: firmware.to_json(rows[kind]) for kind in kinds if kind in rows}
    if not response:
        raise HTTPException(400)
    return response


async def generate_fw_all(req: CohortRequest):
    return [firmware.to_json(row, archival=True) for row in await firmware.all_rows(req.db)]


generators = {
    "pipeline-api": generate_pipeline_api,
    "linked-services": generate_linked_services,
    "health-insights": generate_health_insights,
    "fw": generate_fw,
    "fw-all": generate_fw_all,
}


@app.get("/cohort", dependencies=[Depends(optional_auth)])
async def cohort(
    request: Request,
    select: str | None = None,
    hardware: str | None = None,
    includeRecovery: str | None = None,
):
    # These are 400s rather than FastAPI's default 422 because watches in the
    # field expect the Flask behaviour of a missing query arg being a 400.
    if select is None:
        raise HTTPException(400)
    req = CohortRequest(
        db=request.scope["env"].DB,
        hardware=hardware,
        include_recovery=includeRecovery == "true",
    )

    response = {}
    for entry in select.split(","):
        if entry not in generators:
            raise HTTPException(400)
        response[entry] = await generators[entry](req)
    return response


@app.get("/heartbeat", response_class=PlainTextResponse)
@app.get("/cohorts/heartbeat", response_class=PlainTextResponse)
async def heartbeat():
    return "ok"
