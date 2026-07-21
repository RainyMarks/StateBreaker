"""Vulnerable lab: duplicate seat reservation with a check-then-act race.

Business invariant: one show seat can have at most one successful reservation.
The reserve handler checks that the seat is available and only later writes the
holder — with an ``await`` in between — so concurrent reservations for the same
seat can all succeed and create multiple reservation records. Intentional
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


class SeatsRequest(BaseModel):
    seats: list[str] = ["A1"]


def _public_seat(show_id: str, seat: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "show_id": show_id,
        "seat": seat,
        "available": record["holder"] is None,
        "holder": record["holder"],
        "reservation_count": len(record["reservations"]),
        "reservations": list(record["reservations"]),
    }


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory state."""
    app = FastAPI(title="lab-race-seat-reservation")

    shows: dict[str, dict[str, dict[str, Any]]] = {}
    write_lock = asyncio.Lock()

    @app.post("/shows/{show_id}/seats", status_code=201, response_model=None)
    async def initialize_seats(show_id: str, body: SeatsRequest) -> dict[str, Any] | JSONResponse:
        if not body.seats:
            return JSONResponse(status_code=422, content={"detail": "at least one seat required"})
        async with write_lock:
            if show_id in shows:
                return JSONResponse(status_code=409, content={"detail": "duplicate show"})
            shows[show_id] = {
                seat: {"holder": None, "reservations": []} for seat in body.seats
            }
        return {"show": {"id": show_id, "seats": body.seats}}

    @app.post("/shows/{show_id}/seats/{seat}/reserve", response_model=None)
    async def reserve_seat(
        show_id: str, seat: str, x_user_id: Annotated[str, Header()] = "anonymous"
    ) -> dict[str, Any] | JSONResponse:
        show = shows.get(show_id)
        if show is None:
            return JSONResponse(status_code=404, content={"detail": "unknown show"})
        seat_record = show.get(seat)
        if seat_record is None:
            return JSONResponse(status_code=404, content={"detail": "unknown seat"})
        if seat_record["holder"] is not None:
            return JSONResponse(status_code=409, content={"reserved": False, "reason": "taken"})
        # INTENTIONAL VULNERABILITY (check-then-act): availability is checked
        # before the await, but the holder and reservation record are written later.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        reservation = {
            "id": uuid.uuid4().hex,
            "show_id": show_id,
            "seat": seat,
            "holder": x_user_id,
        }
        async with write_lock:
            seat_record["holder"] = x_user_id
            seat_record["reservations"].append(reservation)
        return {
            "reserved": True,
            "reservation": reservation,
            "reservation_count": len(seat_record["reservations"]),
        }

    @app.get("/shows/{show_id}/seats/{seat}", response_model=None)
    async def get_seat(show_id: str, seat: str) -> dict[str, Any] | JSONResponse:
        show = shows.get(show_id)
        if show is None:
            return JSONResponse(status_code=404, content={"detail": "unknown show"})
        seat_record = show.get(seat)
        if seat_record is None:
            return JSONResponse(status_code=404, content={"detail": "unknown seat"})
        return {"seat": _public_seat(show_id, seat, seat_record)}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            shows.clear()
        return {"reset": True}

    return app


app = create_app()
