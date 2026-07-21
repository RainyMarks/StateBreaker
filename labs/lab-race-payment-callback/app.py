"""Vulnerable lab: payment callback double-credit race.

Business invariant: a payment callback for one order should credit the account
exactly once. The callback handler checks ``status == "unpaid"`` and only later
writes ``"paid"`` — with an ``await`` in between — so duplicate callbacks racing
together both pass the check and both credit the account. Intentional teaching
vulnerability.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

RACE_WINDOW_SECONDS = 0.05


class CreateOrderRequest(BaseModel):
    user: str = "alice"
    credit: int = 100


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory state."""
    app = FastAPI(title="lab-race-payment-callback")

    orders: dict[str, dict[str, Any]] = {}
    accounts: dict[str, dict[str, Any]] = {}
    write_lock = asyncio.Lock()

    @app.post("/orders/{order_id}", status_code=201, response_model=None)
    async def create_order(order_id: str, body: CreateOrderRequest) -> dict[str, Any] | JSONResponse:
        async with write_lock:
            if order_id in orders:
                return JSONResponse(status_code=409, content={"detail": "duplicate"})
            orders[order_id] = {
                "id": order_id,
                "user": body.user,
                "credit": body.credit,
                "status": "unpaid",
                "callbacks": 0,
            }
        return {"order": orders[order_id]}

    @app.post("/payments/{order_id}/callback", response_model=None)
    async def payment_callback(order_id: str) -> dict[str, Any] | JSONResponse:
        order = orders.get(order_id)
        if order is None:
            return JSONResponse(status_code=404, content={"detail": "unknown order"})
        if order["status"] != "unpaid":
            return JSONResponse(status_code=409, content={"credited": False, "reason": "paid"})
        # INTENTIONAL VULNERABILITY (check-then-act): duplicate callbacks can
        # both observe "unpaid" before either writes "paid" and credits the user.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        async with write_lock:
            order["status"] = "paid"
            order["callbacks"] += 1
            account = accounts.setdefault(
                order["user"], {"user": order["user"], "credit_total": 0, "payment_count": 0}
            )
            account["credit_total"] += order["credit"]
            account["payment_count"] += 1
        return {
            "credited": True,
            "order_id": order_id,
            "user": order["user"],
            "credit": order["credit"],
        }

    @app.get("/orders/{order_id}", response_model=None)
    async def get_order(order_id: str) -> dict[str, Any] | JSONResponse:
        order = orders.get(order_id)
        if order is None:
            return JSONResponse(status_code=404, content={"detail": "unknown order"})
        return {"order": order}

    @app.get("/accounts/{user}")
    async def get_account(user: str) -> dict[str, Any]:
        account = accounts.get(user)
        if account is None:
            account = {"user": user, "credit_total": 0, "payment_count": 0}
        return {"account": account}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            orders.clear()
            accounts.clear()
        return {"reset": True}

    return app


app = create_app()
