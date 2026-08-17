import json
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import Firmware
from .settings import config


class SortedJSONResponse(JSONResponse):
    """Sorts keys, as Flask's jsonify did, so responses stay byte-identical for
    clients already in the field."""

    def render(self, content: Any) -> bytes:
        return json.dumps(content, sort_keys=True, separators=(",", ":")).encode()


router = APIRouter(default_response_class=SortedJSONResponse)


async def optional_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict | None:
    rebble_auth_host = config["REBBLE_AUTH"]
    if not authorization or rebble_auth_host is None:
        return None
    result = await request.app.state.http.get(
        f"{rebble_auth_host}/api/v1/me", headers={"Authorization": authorization}
    )
    if result.status_code != 200:
        raise HTTPException(401)
    return result.json()


@dataclass
class CohortRequest:
    """Everything a generator is allowed to look at, resolved once per request."""

    session: AsyncSession
    hardware: str | None
    include_recovery: bool


async def _latest_firmware(session, hardware, kind):
    return await session.scalar(
        select(Firmware)
        .filter_by(hardware=hardware, kind=kind)
        .order_by(Firmware.timestamp.desc())
        .limit(1)
    )


async def _all_firmware(session):
    result = await session.scalars(
        select(Firmware).order_by(Firmware.kind, Firmware.timestamp.desc())
    )
    return result.all()


async def generate_pipeline_api(req: CohortRequest):
    return {"host": "pipeline-api.rebble.io"}


async def generate_linked_services(req: CohortRequest):
    return {"enabled_providers": []}


async def generate_health_insights(req: CohortRequest):
    return {
        "url": "https://binaries.rebble.io/health-insights/v11/insights.pbhi",
        "version": 11,
    }


async def generate_fw(req: CohortRequest):
    if req.hardware is None:
        raise HTTPException(400)
    kinds = ("normal", "recovery") if req.include_recovery else ("normal",)

    response = {}
    for kind in kinds:
        row = await _latest_firmware(req.session, req.hardware, kind)
        if row is not None:
            response[kind] = row.to_json()
    if not response:
        raise HTTPException(400)
    return response


async def generate_fw_all(req: CohortRequest):
    return [row.to_json(archival=True) for row in await _all_firmware(req.session)]


generators = {
    "pipeline-api": generate_pipeline_api,
    "linked-services": generate_linked_services,
    "health-insights": generate_health_insights,
    "fw": generate_fw,
    "fw-all": generate_fw_all,
}


@router.get("/cohort", dependencies=[Depends(optional_auth)])
async def cohort(
    session: Annotated[AsyncSession, Depends(get_session)],
    select: str | None = None,
    hardware: str | None = None,
    includeRecovery: str | None = None,
):
    # These are 400s rather than FastAPI's default 422 because watches in the
    # field expect the Flask behaviour of a missing query arg being a 400.
    if select is None:
        raise HTTPException(400)
    req = CohortRequest(
        session=session,
        hardware=hardware,
        include_recovery=includeRecovery == "true",
    )

    response = {}
    for entry in select.split(","):
        if entry not in generators:
            raise HTTPException(400)
        response[entry] = await generators[entry](req)
    return response


@router.get("/heartbeat", response_class=PlainTextResponse)
@router.get("/cohorts/heartbeat", response_class=PlainTextResponse)
async def heartbeat():
    return "ok"
