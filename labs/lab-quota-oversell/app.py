"""Vulnerable lab: limited-seat drop with an oversell race.

Business invariant: ``sold`` never exceeds ``seats``. The buy handler checks
``sold < seats`` and only later increments — with an ``await`` in between — so
concurrent buyers all pass the check and the drop oversells. Intentional
teaching vulnerability.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

RACE_WINDOW_SECONDS = 0.05


class OpenRequest(BaseModel):
    sku: str | None = None
    seats: int = 1


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory state."""
    app = FastAPI(title="lab-quota-oversell")

    drops: dict[str, dict[str, Any]] = {}
    write_lock = asyncio.Lock()

    @app.post("/drops/open", status_code=201, response_model=None)
    async def open_drop(body: OpenRequest) -> dict[str, Any] | JSONResponse:
        sku = body.sku or uuid.uuid4().hex[:10]
        async with write_lock:
            if sku in drops:
                return JSONResponse(status_code=409, content={"detail": "duplicate"})
            drops[sku] = {"sku": sku, "seats": body.seats, "sold": 0, "buyers": []}
        return {"drop": {"sku": sku, "seats": body.seats, "sold": 0}}

    @app.post("/drops/{sku}/buy", response_model=None)
    async def buy_drop(
        sku: str, x_user_id: Annotated[str, Header()] = "anonymous"
    ) -> dict[str, Any] | JSONResponse:
        drop = drops.get(sku)
        if drop is None:
            return JSONResponse(status_code=404, content={"detail": "unknown drop"})
        if drop["sold"] >= drop["seats"]:
            return JSONResponse(status_code=409, content={"bought": False, "reason": "sold_out"})
        # INTENTIONAL VULNERABILITY (check-then-act): the quota check and the
        # increment are separated by an await, so buyers oversell the drop.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        async with write_lock:
            drop["sold"] += 1
            drop["buyers"].append(x_user_id)
        return {"bought": True, "sold": drop["sold"]}

    @app.get("/drops/{sku}", response_model=None)
    async def get_drop(sku: str) -> dict[str, Any] | JSONResponse:
        drop = drops.get(sku)
        if drop is None:
            return JSONResponse(status_code=404, content={"detail": "unknown drop"})
        return {"drop": {k: v for k, v in drop.items() if k != "buyers"}}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            drops.clear()
        return {"reset": True}

    return app


app = create_app()
