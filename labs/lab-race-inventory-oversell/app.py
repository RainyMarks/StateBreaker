"""Vulnerable lab: inventory oversell with a check-then-act race.

Business invariant: ``sold`` must never exceed ``stock``. The buy handler
checks ``sold < stock`` and only later increments ``sold`` after an ``await``.
Concurrent buyers can all pass the stale check, so the product oversells.
This is an intentional local teaching vulnerability.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

RACE_WINDOW_SECONDS = 0.05


class StockRequest(BaseModel):
    stock: int = 1


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory product state."""
    app = FastAPI(title="lab-race-inventory-oversell")

    products: dict[str, dict[str, Any]] = {}
    write_lock = asyncio.Lock()

    @app.post("/products/{sku}/stock", status_code=201, response_model=None)
    async def initialize_stock(sku: str, body: StockRequest) -> dict[str, Any] | JSONResponse:
        if body.stock < 0:
            return JSONResponse(status_code=422, content={"detail": "stock must be non-negative"})
        async with write_lock:
            products[sku] = {"sku": sku, "stock": body.stock, "sold": 0, "buyers": []}
            product = products[sku].copy()
        product.pop("buyers")
        return {"product": product}

    @app.post("/products/{sku}/buy", response_model=None)
    async def buy_product(
        sku: str,
        x_user_id: Annotated[str, Header()] = "anonymous",
    ) -> dict[str, Any] | JSONResponse:
        product = products.get(sku)
        if product is None:
            return JSONResponse(status_code=404, content={"detail": "unknown product"})
        if product["sold"] >= product["stock"]:
            return JSONResponse(
                status_code=409,
                content={"bought": False, "reason": "sold_out", "sold": product["sold"]},
            )
        # INTENTIONAL VULNERABILITY (check-then-act): the stock check above is
        # separated from the increment below, letting concurrent buyers oversell.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        async with write_lock:
            product["sold"] += 1
            product["buyers"].append(x_user_id)
            sold = product["sold"]
            stock = product["stock"]
        return {"bought": True, "sku": sku, "sold": sold, "stock": stock}

    @app.get("/products/{sku}", response_model=None)
    async def get_product(sku: str) -> dict[str, Any] | JSONResponse:
        product = products.get(sku)
        if product is None:
            return JSONResponse(status_code=404, content={"detail": "unknown product"})
        return {"product": {k: v for k, v in product.items() if k != "buyers"}}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            products.clear()
        return {"reset": True}

    return app


app = create_app()
