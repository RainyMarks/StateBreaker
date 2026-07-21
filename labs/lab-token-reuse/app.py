"""Vulnerable lab: single-use recovery ticket with a check-then-act race.

Business invariant: a recovery ticket can be finished exactly once. The finish
handler checks ``state == "armed"`` and only later writes ``"consumed"`` — with
an ``await`` in between — so two concurrent finishes both apply. Intentional
teaching vulnerability.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

RACE_WINDOW_SECONDS = 0.05


class BeginRequest(BaseModel):
    ticket: str | None = None


class FinishRequest(BaseModel):
    secret: str


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory state."""
    app = FastAPI(title="lab-token-reuse")

    recoveries: dict[str, dict[str, Any]] = {}
    write_lock = asyncio.Lock()

    @app.post("/recoveries/begin", status_code=201, response_model=None)
    async def begin_recovery(body: BeginRequest) -> dict[str, Any] | JSONResponse:
        ticket = body.ticket or uuid.uuid4().hex
        async with write_lock:
            if ticket in recoveries:
                return JSONResponse(status_code=409, content={"detail": "duplicate"})
            recoveries[ticket] = {"ticket": ticket, "state": "armed", "applied": 0}
        return {"recovery": {"ticket": ticket, "state": "armed", "applied": 0}}

    @app.post("/recoveries/{ticket}/finish", response_model=None)
    async def finish_recovery(ticket: str, body: FinishRequest) -> dict[str, Any] | JSONResponse:
        recovery = recoveries.get(ticket)
        if recovery is None:
            return JSONResponse(status_code=404, content={"detail": "unknown ticket"})
        if recovery["state"] != "armed":
            return JSONResponse(status_code=409, content={"finished": False, "reason": "consumed"})
        # INTENTIONAL VULNERABILITY (check-then-act): the one-use guarantee is
        # checked here but only written after the await.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        async with write_lock:
            recovery["state"] = "consumed"
            recovery["applied"] += 1
        return {"finished": True, "applied": recovery["applied"]}

    @app.get("/recoveries/{ticket}", response_model=None)
    async def get_recovery(ticket: str) -> dict[str, Any] | JSONResponse:
        recovery = recoveries.get(ticket)
        if recovery is None:
            return JSONResponse(status_code=404, content={"detail": "unknown ticket"})
        return {"recovery": recovery}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            recoveries.clear()
        return {"reset": True}

    return app


app = create_app()
