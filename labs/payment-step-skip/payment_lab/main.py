"""A tiny order-payment lab intentionally vulnerable to step skipping by default."""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

MAX_ORDERS = 100
ITEM_NAME = "DADA Security Lab Ticket"
ITEM_PRICE_YUAN = 99
CONFIRM_REQUIRES_PAYMENT = False

app = FastAPI(
    title="StateBreaker payment step-skip lab",
    version="0.1.0",
    description="Local payment-step bypass lab for authorized business-logic experiments.",
)


def set_payment_guard(enabled: bool) -> None:
    """Test hook: enable the fixed behavior without rebuilding the app."""

    global CONFIRM_REQUIRES_PAYMENT
    CONFIRM_REQUIRES_PAYMENT = enabled


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class LabEvent(BaseModel):
    sequence: int
    kind: Literal[
        "order.created",
        "order.paid",
        "order.confirmed",
        "order.confirm.rejected",
    ]
    request_id: str
    timestamp: str
    monotonic_ns: int
    message: str
    snapshot: dict[str, Any]


class OrderState:
    def __init__(self, order_id: str) -> None:
        self.order_id = order_id
        self.item = ITEM_NAME
        self.amount_yuan = ITEM_PRICE_YUAN
        self.payment_status = "UNPAID"
        self.order_status = "CREATED"
        self.confirm_count = 0
        self.created_at = utc_iso()
        self.events: list[LabEvent] = []
        self._sequence = 0
        self.record("order.created", request_id="system", message="Order created")

    def snapshot(self) -> dict[str, Any]:
        confirmed_without_payment = (
            self.order_status == "CONFIRMED" and self.payment_status != "PAID"
        )
        return {
            "order_id": self.order_id,
            "item": self.item,
            "amount_yuan": self.amount_yuan,
            "payment_status": self.payment_status,
            "order_status": self.order_status,
            "confirm_count": self.confirm_count,
            "confirmed_without_payment": confirmed_without_payment,
            "created_at": self.created_at,
        }

    def record(
        self,
        kind: Literal[
            "order.created",
            "order.paid",
            "order.confirmed",
            "order.confirm.rejected",
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


class OrderView(BaseModel):
    order_id: str
    item: str
    amount_yuan: int
    payment_status: Literal["UNPAID", "PAID"]
    order_status: Literal["CREATED", "CONFIRMED"]
    confirm_count: int
    confirmed_without_payment: bool
    created_at: str


class CreateOrderRequest(BaseModel):
    item: str = Field(default=ITEM_NAME, min_length=1)


class EventsView(BaseModel):
    order_id: str
    events: list[LabEvent]


ORDERS: OrderedDict[str, OrderState] = OrderedDict()


def get_order(order_id: str) -> OrderState:
    order = ORDERS.get(order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
    return order


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "lab": "payment-step-skip"}


@app.post("/api/orders", response_model=OrderView, status_code=status.HTTP_201_CREATED)
async def create_order(payload: CreateOrderRequest) -> dict[str, Any]:
    while len(ORDERS) >= MAX_ORDERS:
        ORDERS.popitem(last=False)
    order_id = uuid.uuid4().hex
    order = OrderState(order_id)
    order.item = payload.item
    ORDERS[order_id] = order
    return order.snapshot()


@app.get("/api/orders/{order_id}/state", response_model=OrderView)
async def read_state(order_id: str) -> dict[str, Any]:
    return get_order(order_id).snapshot()


@app.get("/api/orders/{order_id}/events", response_model=EventsView)
async def read_events(order_id: str) -> dict[str, Any]:
    order = get_order(order_id)
    return {"order_id": order_id, "events": order.events}


@app.post("/api/orders/{order_id}/pay", response_model=OrderView)
async def pay_order(order_id: str, request: Request) -> dict[str, Any]:
    order = get_order(order_id)
    request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
    order.payment_status = "PAID"
    order.record("order.paid", request_id=request_id, message="Payment accepted")
    return order.snapshot()


@app.post("/api/orders/{order_id}/confirm", response_model=OrderView)
async def confirm_order(order_id: str, request: Request) -> dict[str, Any]:
    order = get_order(order_id)
    request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)

    if CONFIRM_REQUIRES_PAYMENT and order.payment_status != "PAID":
        order.record(
            "order.confirm.rejected",
            request_id=request_id,
            message="Confirmation rejected because payment is missing",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="payment required before confirmation",
        )

    order.order_status = "CONFIRMED"
    order.confirm_count += 1
    order.record("order.confirmed", request_id=request_id, message="Order confirmed")
    return order.snapshot()