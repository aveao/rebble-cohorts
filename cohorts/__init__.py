from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from .api import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One pooled client for the lifetime of the process, so the auth lookup on
    # /cohort reuses connections instead of building a pool per request.
    async with httpx.AsyncClient() as client:
        app.state.http = client
        yield


app = FastAPI(title="cohorts", description="The Rebble cohorts API", lifespan=lifespan)
app.include_router(router)
