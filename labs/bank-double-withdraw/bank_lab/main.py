"""A tiny banking lab intentionally vulnerable to concurrent withdrawals."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

INITIAL_BALANCE_CENTS = 10_000
DEFAULT_WITHDRAW_CENTS = 10_000
RACE_WINDOW_SECONDS = 0.150
MAX_RUNS = 100

app = FastAPI(
    title="StateBreaker bank double-withdraw race lab",
    version="0.1.0",
    description="Local balance race lab for authorized business-logic experiments.",
)


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class LabEvent(BaseModel):
    sequence: int
    kind: Literal[
        "account.created",
        "withdraw.checked",
        "withdraw.committed",
        "withdraw.rejected",
    ]
    request_id: str
    timestamp: str
    monotonic_ns: int
    message: str
    snapshot: dict[str, Any]


class AccountState:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.account_id = run_id
        self.balance_cents = INITIAL_BALANCE_CENTS
        self.successful_withdrawals = 0
        self.created_at = utc_iso()
        self.events: list[LabEvent] = []
        self._sequence = 0
        self.record("account.created", request_id="system", message="Account opened")

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "account_id": self.account_id,
            "balance_cents": self.balance_cents,
            "initial_balance_cents": INITIAL_BALANCE_CENTS,
            "successful_withdrawals": self.successful_withdrawals,
            "overdraft": self.balance_cents < 0,
            "created_at": self.created_at,
        }

    def record(
        self,
        kind: Literal[
            "account.created",
            "withdraw.checked",
            "withdraw.committed",
            "withdraw.rejected",
        ],
        *,
        request_id: str,
        message: str,
    ) -> None:
        self._sequence += 1
        self.events.append(
            LabEvent(
                sequence=self._sequence,
                kind=kind,
                request_id=request_id,
                timestamp=utc_iso(),
                monotonic_ns=time.perf_counter_ns(),
                message=message,
                snapshot=self.snapshot(),
            )
        )


class AccountView(BaseModel):
    run_id: str
    account_id: str
    balance_cents: int
    initial_balance_cents: int
    successful_withdrawals: int
    overdraft: bool
    created_at: str


class WithdrawRequest(BaseModel):
    amount_cents: int = Field(default=DEFAULT_WITHDRAW_CENTS, gt=0, le=INITIAL_BALANCE_CENTS)


class EventsView(BaseModel):
    run_id: str
    events: list[LabEvent]


RUNS: OrderedDict[str, AccountState] = OrderedDict()


def get_account(run_id: str) -> AccountState:
    account = RUNS.get(run_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return account


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "lab": "bank-double-withdraw"}


@app.post("/api/runs", response_model=AccountView, status_code=status.HTTP_201_CREATED)
async def create_run() -> dict[str, Any]:
    while len(RUNS) >= MAX_RUNS:
        RUNS.popitem(last=False)
    run_id = uuid.uuid4().hex
    account = AccountState(run_id)
    RUNS[run_id] = account
    return account.snapshot()


@app.get("/api/runs/{run_id}/state", response_model=AccountView)
async def read_state(run_id: str) -> dict[str, Any]:
    return get_account(run_id).snapshot()


@app.get("/api/runs/{run_id}/events", response_model=EventsView)
async def read_events(run_id: str) -> dict[str, Any]:
    account = get_account(run_id)
    return {"run_id": run_id, "events": account.events}


@app.post("/api/runs/{run_id}/withdraw", response_model=AccountView)
async def withdraw(run_id: str, payload: WithdrawRequest, request: Request) -> dict[str, Any]:
    account = get_account(run_id)
    request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)

    # Intentionally vulnerable TOCTOU: balance is checked before a delay, then used later
    # without an atomic compare-and-set or a lock.
    if account.balance_cents < payload.amount_cents:
        account.record(
            "withdraw.rejected",
            request_id=request_id,
            message="Insufficient funds at check time",
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="insufficient funds")

    account.record(
        "withdraw.checked",
        request_id=request_id,
        message="Balance looked sufficient before the race window",
    )
    await asyncio.sleep(RACE_WINDOW_SECONDS)

    account.balance_cents -= payload.amount_cents
    account.successful_withdrawals += 1
    account.record(
        "withdraw.committed",
        request_id=request_id,
        message="Withdrawal committed without rechecking balance",
    )
    return account.snapshot()