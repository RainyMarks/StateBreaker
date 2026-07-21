"""Vulnerable lab: idempotency-key reuse with a check-then-act race.

Business invariant: one idempotency key may create at most one order. The order
handler checks that the key has not been used and only later records it - with
an ``await`` in between - so two concurrent requests with the same key both
create orders. This is an intentional teaching vulnerability.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

RACE_WINDOW_SECONDS = 0.05


class OrderRequest(BaseModel):
    sku: str = "widget"
    quantity: int = 1


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory state."""
    app = FastAPI(title="lab-race-idempotency-reuse")

    orders: dict[str, dict[str, Any]] = {}
    idempotency_keys: dict[str, dict[str, Any]] = {}
    write_lock = asyncio.Lock()

    @app.post("/orders", status_code=201, response_model=None)
    async def create_order(
        body: OrderRequest,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any] | JSONResponse:
        if not idempotency_key:
            return JSONResponse(
                status_code=400,
                content={"detail": "missing Idempotency-Key header"},
            )
        if idempotency_key in idempotency_keys:
            return JSONResponse(
                status_code=409,
                content={
                    "created": False,
                    "reason": "idempotency_key_reused",
                    "key": idempotency_key,
                    "order_ids": idempotency_keys[idempotency_key]["order_ids"],
                },
            )
        # INTENTIONAL VULNERABILITY (check-then-act): the idempotency key is
        # checked above but only recorded after this await. Concurrent requests
        # with the same key both observe it as unused and both create orders.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        async with write_lock:
            order_id = f"ord-{uuid.uuid4().hex[:8]}"
            order = {
                "id": order_id,
                "sku": body.sku,
                "quantity": body.quantity,
                "idempotency_key": idempotency_key,
            }
            orders[order_id] = order
            record = idempotency_keys.setdefault(
                idempotency_key,
                {"key": idempotency_key, "uses": 0, "order_ids": []},
            )
            record["uses"] += 1
            record["order_ids"].append(order_id)
        return {"created": True, "order": order}

    @app.get("/orders")
    async def list_orders() -> dict[str, list[dict[str, Any]]]:
        return {"orders": list(orders.values())}

    @app.get("/idempotency/{key}", response_model=None)
    async def get_idempotency(key: str) -> dict[str, Any] | JSONResponse:
        record = idempotency_keys.get(key)
        if record is None:
            return JSONResponse(status_code=404, content={"detail": "unknown idempotency key"})
        return {"idempotency": record}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            orders.clear()
            idempotency_keys.clear()
        return {"reset": True}

    return app


app = create_app()
