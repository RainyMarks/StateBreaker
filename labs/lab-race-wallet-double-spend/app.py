"""Vulnerable lab: wallet withdrawals with a double-spend race.

Business invariant: a user's wallet balance should never drop below zero, and
total withdrawals should never exceed the funds that were deposited. The
withdraw handler checks the available balance, then awaits before recording the
withdrawal, so concurrent withdrawals can both pass the same stale check. This
is an intentional teaching vulnerability for local race-condition testing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

RACE_WINDOW_SECONDS = 0.05


class MoneyRequest(BaseModel):
    amount: int = Field(gt=0)


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory wallet state."""
    app = FastAPI(title="lab-race-wallet-double-spend")

    accounts: dict[str, dict[str, int]] = {}
    write_lock = asyncio.Lock()

    def get_or_create_account(user: str) -> dict[str, int]:
        if user not in accounts:
            accounts[user] = {
                "balance": 0,
                "total_deposited": 0,
                "total_withdrawn": 0,
            }
        return accounts[user]

    def serialize_account(user: str, account: dict[str, int]) -> dict[str, Any]:
        return {"user": user, **account}

    @app.post("/accounts/{user}/deposit")
    async def deposit(user: str, body: MoneyRequest) -> dict[str, Any]:
        async with write_lock:
            account = get_or_create_account(user)
            account["balance"] += body.amount
            account["total_deposited"] += body.amount
            snapshot = serialize_account(user, account)
        return {"deposited": body.amount, "account": snapshot}

    @app.post("/accounts/{user}/withdraw", response_model=None)
    async def withdraw(user: str, body: MoneyRequest) -> dict[str, Any] | JSONResponse:
        account = accounts.get(user)
        if account is None:
            return JSONResponse(status_code=404, content={"detail": "unknown account"})
        if body.amount > account["balance"]:
            return JSONResponse(
                status_code=422,
                content={
                    "withdrawn": 0,
                    "reason": "insufficient_funds",
                    "balance": account["balance"],
                },
            )

        # INTENTIONAL VULNERABILITY (check-then-act): the balance check above is
        # separated from the deduction below by an await. Concurrent withdrawals
        # can both pass against the same stale balance and overdraw the wallet.
        await asyncio.sleep(RACE_WINDOW_SECONDS)

        async with write_lock:
            account["balance"] -= body.amount
            account["total_withdrawn"] += body.amount
            snapshot = serialize_account(user, account)
        return {"withdrawn": body.amount, "account": snapshot}

    @app.get("/accounts/{user}", response_model=None)
    async def get_account(user: str) -> dict[str, Any] | JSONResponse:
        account = accounts.get(user)
        if account is None:
            return JSONResponse(status_code=404, content={"detail": "unknown account"})
        return {"account": serialize_account(user, account)}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            accounts.clear()
        return {"reset": True}

    return app


app = create_app()
