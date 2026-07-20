"""A tiny order lab intentionally vulnerable to refund-vs-fulfillment races."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel

ORDER_AMOUNT_CENTS = 12_000
RACE_WINDOW_SECONDS = 0.150
MAX_RUNS = 100

app = FastAPI(
    title="StateBreaker refund vs fulfill race lab",
    version="0.1.0",
    description="Local refund/fulfillment race lab for authorized experiments.",
)


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class LabEvent(BaseModel):
    sequence: int
    kind: Literal[
        "order.created",
        "refund.checked",
        "refund.committed",
        "refund.rejected",
        "fulfill.checked",
        "fulfill.committed",
        "fulfill.rejected",
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
        self.payment_status = "PAID"
        self.refund_status = "NONE"
        self.fulfillment_status = "PENDING"
        self.amount_cents = ORDER_AMOUNT_CENTS
        self.refund_count = 0
        self.fulfill_count = 0
        self.created_at = utc_iso()
        self.events: list[LabEvent] = []
        self._sequence = 0
        self.record("order.created", request_id="system", message="Paid order created")

    def snapshot(self) -> dict[str, Any]:
        refunded_and_fulfilled = (
            self.refund_status == "REFUNDED" and self.fulfillment_status == "FULFILLED"
        )
        return {
            "run_id": self.run_id,
            "order_id": self.order_id,
            "payment_status": self.payment_status,
            "refund_status": self.refund_status,
            "fulfillment_status": self.fulfillment_status,
            "amount_cents": self.amount_cents,
            "refund_count": self.refund_count,
            "fulfill_count": self.fulfill_count,
            "refunded_and_fulfilled": refunded_and_fulfilled,
            "created_at": self.created_at,
        }

    def record(
        self,
        kind: Literal[
            "order.created",
            "refund.checked",
            "refund.committed",
            "refund.rejected",
            "fulfill.checked",
            "fulfill.committed",
            "fulfill.rejected",
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
    payment_status: Literal["PAID"]
    refund_status: Literal["NONE", "REFUNDED"]
    fulfillment_status: Literal["PENDING", "FULFILLED"]
    amount_cents: int
    refund_count: int
    fulfill_count: int
    refunded_and_fulfilled: bool
    created_at: str


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
    return {"status": "ok", "lab": "refund-vs-fulfill-race"}


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


@app.post("/api/runs/{run_id}/refund", response_model=RunView)
async def refund_order(run_id: str, request: Request) -> dict[str, Any]:
    run = get_run(run_id)
    request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
    if run.fulfillment_status == "FULFILLED" or run.refund_status == "REFUNDED":
        run.record("refund.rejected", request_id=request_id, message="Refund rejected")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="cannot refund order")

    run.record("refund.checked", request_id=request_id, message="Refund looked allowed")
    await asyncio.sleep(RACE_WINDOW_SECONDS)

    run.refund_status = "REFUNDED"
    run.refund_count += 1
    run.record("refund.committed", request_id=request_id, message="Refund committed")
    return run.snapshot()


@app.post("/api/runs/{run_id}/fulfill", response_model=RunView)
async def fulfill_order(run_id: str, request: Request) -> dict[str, Any]:
    run = get_run(run_id)
    request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
    if run.refund_status == "REFUNDED" or run.fulfillment_status == "FULFILLED":
        run.record("fulfill.rejected", request_id=request_id, message="Fulfillment rejected")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="cannot fulfill order")

    run.record("fulfill.checked", request_id=request_id, message="Fulfillment looked allowed")
    await asyncio.sleep(RACE_WINDOW_SECONDS)

    run.fulfillment_status = "FULFILLED"
    run.fulfill_count += 1
    run.record("fulfill.committed", request_id=request_id, message="Fulfillment committed")
    return run.snapshot()