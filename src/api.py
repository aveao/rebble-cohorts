from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import firmware

# s-maxage caches at the edge for five minutes; max-age=0 leaves clients
# uncached, so watches keep asking and only the edge holds a copy. Firmware
# changes at most hourly, when the cron publishes, so this bounds how long a new
# release can sit unseen behind a cached response.
CACHE_CONTROL = "public, max-age=0, s-maxage=300"

# No docs routes: this API has three endpoints and no consumer for a schema, and
# keeping them out spares the route table and the deploy-time snapshot.
app = FastAPI(
    title="cohorts",
    description="The Rebble cohorts API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@dataclass
class CohortRequest:
    """Everything a generator is allowed to look at, resolved once per request."""

    db: Any
    hardware: str | None
    include_recovery: bool
    source: str | None


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

    rows = await firmware.latest_by_kind(req.db, req.hardware, kinds, req.source)
    response = {kind: firmware.to_json(rows[kind]) for kind in kinds if kind in rows}
    if not response:
        raise HTTPException(400)
    return response


async def generate_fw_all(req: CohortRequest):
    rows = await firmware.all_rows(req.db, req.source)
    return [firmware.to_json(row, archival=True) for row in rows]


generators = {
    "pipeline-api": generate_pipeline_api,
    "linked-services": generate_linked_services,
    "health-insights": generate_health_insights,
    "fw": generate_fw,
    "fw-all": generate_fw_all,
}


@app.get("/cohort")
async def cohort(
    request: Request,
    select: str | None = None,
    hardware: str | None = None,
    includeRecovery: str | None = None,
    source: str | None = None,
):
    # These are 400s rather than FastAPI's default 422 because watches in the
    # field expect the Flask behaviour of a missing query arg being a 400.
    if select is None:
        raise HTTPException(400)
    # An absent source means the canonical firmware, stored with source NULL.
    req = CohortRequest(
        db=request.scope["env"].DB,
        hardware=hardware,
        include_recovery=includeRecovery == "true",
        source=source,
    )

    response = {}
    for entry in select.split(","):
        if entry not in generators:
            raise HTTPException(400)
        response[entry] = await generators[entry](req)
    # Returning a Response rather than a dict skips FastAPI's jsonable_encoder
    # and response-model handling, which is worth about a millisecond on the 68
    # rows of fw-all. The Cache-Control header is what opts this response into
    # the edge cache in entry.py.
    return JSONResponse(response, headers={"Cache-Control": CACHE_CONTROL})


@app.get("/heartbeat")
@app.get("/cohorts/heartbeat")
async def heartbeat():
    return PlainTextResponse("ok")
