"""Vulnerable lab: appointment slot booking with a capacity race.

Business invariant: ``booked`` never exceeds ``capacity``. The book handler
checks ``booked < capacity`` and only later appends the user — with an
``await`` in between — so concurrent bookings can overfill the slot.
Intentional teaching vulnerability.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

RACE_WINDOW_SECONDS = 0.05


class CreateSlotRequest(BaseModel):
    capacity: int = 1


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory state."""
    app = FastAPI(title="lab-race-appointment-slot")

    slots: dict[str, dict[str, Any]] = {}
    write_lock = asyncio.Lock()

    @app.post("/slots/{slot_id}", status_code=201, response_model=None)
    async def create_slot(slot_id: str, body: CreateSlotRequest) -> dict[str, Any] | JSONResponse:
        async with write_lock:
            if slot_id in slots:
                return JSONResponse(status_code=409, content={"detail": "duplicate"})
            slots[slot_id] = {"id": slot_id, "capacity": body.capacity, "bookings": []}
        return {"slot": {"id": slot_id, "capacity": body.capacity, "booked": 0}}

    @app.post("/slots/{slot_id}/book", response_model=None)
    async def book_slot(
        slot_id: str, x_user_id: Annotated[str, Header()] = "anonymous"
    ) -> dict[str, Any] | JSONResponse:
        slot = slots.get(slot_id)
        if slot is None:
            return JSONResponse(status_code=404, content={"detail": "unknown slot"})
        if len(slot["bookings"]) >= slot["capacity"]:
            return JSONResponse(status_code=409, content={"booked": False, "reason": "full"})
        # INTENTIONAL VULNERABILITY (check-then-act): the capacity check and the
        # booking append are separated by an await, so the slot can overbook.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        async with write_lock:
            slot["bookings"].append(x_user_id)
            booked = len(slot["bookings"])
        return {"booked": True, "count": booked}

    @app.get("/slots/{slot_id}", response_model=None)
    async def get_slot(slot_id: str) -> dict[str, Any] | JSONResponse:
        slot = slots.get(slot_id)
        if slot is None:
            return JSONResponse(status_code=404, content={"detail": "unknown slot"})
        return {
            "slot": {
                "id": slot["id"],
                "capacity": slot["capacity"],
                "booked": len(slot["bookings"]),
                "bookings": list(slot["bookings"]),
            }
        }

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            slots.clear()
        return {"reset": True}

    return app


app = create_app()
