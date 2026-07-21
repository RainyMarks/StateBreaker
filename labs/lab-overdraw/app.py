"""Vulnerable lab: stored-value wallet with a check-then-act overdraft race.

Business invariant: a wallet balance may never drop below zero. The debit
handler checks ``amount <= balance`` and only later subtracts — with an
``await`` in between — so two concurrent debits both pass the check and the
balance goes negative. This is an intentional teaching vulnerability.
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
    holder: str
    opening: int = 100


class DebitRequest(BaseModel):
    amount: int


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory state."""
    app = FastAPI(title="lab-overdraw")

    wallets: dict[str, dict[str, Any]] = {}
    write_lock = asyncio.Lock()

    @app.post("/wallets/open", status_code=201)
    async def open_wallet(body: OpenRequest) -> dict[str, Any]:
        wallet_id = f"w-{uuid.uuid4().hex[:8]}"
        async with write_lock:
            wallets[wallet_id] = {
                "id": wallet_id,
                "holder": body.holder,
                "balance": body.opening,
            }
        return {"wallet": wallets[wallet_id]}

    @app.post("/wallets/{wallet_id}/debit", response_model=None)
    async def debit_wallet(
        wallet_id: str,
        body: DebitRequest,
        x_user_id: Annotated[str, Header()] = "anonymous",
    ) -> dict[str, Any] | JSONResponse:
        wallet = wallets.get(wallet_id)
        if wallet is None:
            return JSONResponse(status_code=404, content={"detail": "unknown wallet"})
        if x_user_id != wallet["holder"]:
            return JSONResponse(status_code=403, content={"detail": "forbidden"})
        if body.amount > wallet["balance"]:
            return JSONResponse(
                status_code=422,
                content={"debited": 0, "reason": "insufficient", "balance": wallet["balance"]},
            )
        # INTENTIONAL VULNERABILITY (check-then-act): the balance check above
        # reads a value that the subtraction below re-reads after an await; only
        # the subtraction is lock-protected. Two concurrent debits both pass the
        # check against the old balance, so the balance goes negative.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        async with write_lock:
            wallet["balance"] -= body.amount
            new_balance = wallet["balance"]
        return {"debited": body.amount, "balance": new_balance}

    @app.get("/wallets/{wallet_id}", response_model=None)
    async def get_wallet(wallet_id: str) -> dict[str, Any] | JSONResponse:
        wallet = wallets.get(wallet_id)
        if wallet is None:
            return JSONResponse(status_code=404, content={"detail": "unknown wallet"})
        return {"wallet": wallet}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            wallets.clear()
        return {"reset": True}

    return app


app = create_app()
