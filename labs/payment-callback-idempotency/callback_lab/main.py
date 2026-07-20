"""A tiny payment callback lab intentionally vulnerable to duplicate events."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

ORDER_AMOUNT_CENTS = 10_000
PAYMENT_EVENT_ID = "evt-demo-paid-001"
RACE_WINDOW_SECONDS = 0.150
MAX_RUNS = 100

app = FastAPI(
    title="StateBreaker payment callback idempotency lab",
    version="0.1.0",
    description="Local duplicate-callback lab for authorized business-logic experiments.",
)


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class LabEvent(BaseModel):
    sequence: int
    kind: Literal[
        "order.created",
        "payment.callback.checked",
        "payment.callback.committed",
        "payment.callback.rejected",
    ]
    request_id: str
    timestamp: str
    monotonic_ns: int
    message: str
    snapshot: dict[str, Any]


class RunState:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.order_id = run_id
        self.order_status = "UNPAID"
        self.amount_cents = ORDER_AMOUNT_CENTS
        self.merchant_credit_cents = 0
        self.payment_apply_count = 0
        self.processed_event_ids: list[str] = []
        self.created_at = utc_iso()
        self.events: list[LabEvent] = []
        self._sequence = 0
        self.record("order.created", request_id="system", message="Order created")

    def snapshot(self) -> dict[str, Any]:
        duplicate_callback_observed = self.payment_apply_count > len(set(self.processed_event_ids))
        return {
            "run_id": self.run_id,
            "order_id": self.order_id,
            "order_status": self.order_status,
            "amount_cents": self.amount_cents,
            "merchant_credit_cents": self.merchant_credit_cents,
            "payment_apply_count": self.payment_apply_count,
            "processed_event_ids": list(self.processed_event_ids),
            "duplicate_callback_observed": duplicate_callback_observed,
            "created_at": self.created_at,
        }

    def record(
        self,
        kind: Literal[
            "order.created",
            "payment.callback.checked",
            "payment.callback.committed",
            "payment.callback.rejected",
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


class RunView(BaseModel):
    run_id: str
    order_id: str
    order_status: Literal["UNPAID", "PAID"]
    amount_cents: int
    merchant_credit_cents: int
    payment_apply_count: int
    processed_event_ids: list[str]
    duplicate_callback_observed: bool
    created_at: str


class PaymentCallbackRequest(BaseModel):
    event_id: str = Field(default=PAYMENT_EVENT_ID, min_length=1)
    amount_cents: int = Field(default=ORDER_AMOUNT_CENTS, gt=0)


class EventsView(BaseModel):
    run_id: str
    events: list[LabEvent]


RUNS: OrderedDict[str, RunState] = OrderedDict()


def get_run(run_id: str) -> RunState:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return run


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "lab": "payment-callback-idempotency"}


@app.post("/api/runs", response_model=RunView, status_code=status.HTTP_201_CREATED)
async def create_run() -> dict[str, Any]:
    while len(RUNS) >= MAX_RUNS:
        RUNS.popitem(last=False)
    run_id = uuid.uuid4().hex
    run = RunState(run_id)
    RUNS[run_id] = run
    return run.snapshot()


@app.get("/api/runs/{run_id}/state", response_model=RunView)
async def read_state(run_id: str) -> dict[str, Any]:
    return get_run(run_id).snapshot()


@app.get("/api/runs/{run_id}/events", response_model=EventsView)
async def read_events(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    return {"run_id": run_id, "events": run.events}


@app.post("/api/runs/{run_id}/payment-callback", response_model=RunView)
async def payment_callback(
    run_id: str, payload: PaymentCallbackRequest, request: Request
) -> dict[str, Any]:
    run = get_run(run_id)
    request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
    if payload.amount_cents != run.amount_cents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="amount mismatch")

    # Intentionally vulnerable TOCTOU: the idempotency check and event commit are separated.
    if payload.event_id in run.processed_event_ids:
        run.record(
            "payment.callback.rejected",
            request_id=request_id,
            message="Duplicate event rejected at check time",
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="event already processed")

    run.record(
        "payment.callback.checked",
        request_id=request_id,
        message="Payment event looked new before the race window",
    )
    await asyncio.sleep(RACE_WINDOW_SECONDS)

    run.processed_event_ids.append(payload.event_id)
    run.payment_apply_count += 1
    run.merchant_credit_cents += payload.amount_cents
    run.order_status = "PAID"
    run.record(
        "payment.callback.committed",
        request_id=request_id,
        message="Payment callback applied without rechecking event id",
    )
    return run.snapshot()