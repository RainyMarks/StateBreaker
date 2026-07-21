"""Vulnerable lab: daily quota use with a check-then-act race.

Business invariant: ``used`` must never exceed ``limit`` for a user's daily
quota. The use handler checks ``used < limit`` and only later increments —
with an ``await`` in between — so concurrent uses can both pass the check and
push usage over the limit. Intentional teaching vulnerability.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

RACE_WINDOW_SECONDS = 0.05


class SetQuotaRequest(BaseModel):
    limit: int = 1


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory state."""
    app = FastAPI(title="lab-race-quota-bypass")

    quotas: dict[str, dict[str, Any]] = {}
    write_lock = asyncio.Lock()

    @app.post("/quotas/{user}", status_code=201, response_model=None)
    async def set_quota(user: str, body: SetQuotaRequest) -> dict[str, Any] | JSONResponse:
        if body.limit < 0:
            return JSONResponse(status_code=422, content={"detail": "limit must be non-negative"})
        async with write_lock:
            quotas[user] = {"user": user, "limit": body.limit, "used": 0}
        return {"quota": quotas[user]}

    @app.post("/quotas/{user}/use", response_model=None)
    async def use_quota(user: str) -> dict[str, Any] | JSONResponse:
        quota = quotas.get(user)
        if quota is None:
            return JSONResponse(status_code=404, content={"detail": "unknown quota"})
        if quota["used"] >= quota["limit"]:
            return JSONResponse(
                status_code=409,
                content={"used": False, "reason": "quota_exhausted", "quota": quota},
            )
        # INTENTIONAL VULNERABILITY (check-then-act): the quota check above is
        # separated from the increment below by an await, so concurrent calls can
        # all observe spare quota before any write lands.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        async with write_lock:
            quota["used"] += 1
            used = quota["used"]
        return {"used": True, "quota": {"user": user, "limit": quota["limit"], "used": used}}

    @app.get("/quotas/{user}", response_model=None)
    async def get_quota(user: str) -> dict[str, Any] | JSONResponse:
        quota = quotas.get(user)
        if quota is None:
            return JSONResponse(status_code=404, content={"detail": "unknown quota"})
        return {"quota": quota}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            quotas.clear()
        return {"reset": True}

    return app


app = create_app()
