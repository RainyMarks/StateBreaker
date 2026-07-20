"""A tiny payment lab with intentional authorization and binding flaws."""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

ORDER_AMOUNT_CENTS = 12_500
MAX_RUNS = 100

app = FastAPI(
    title="StateBreaker payment binding mismatch lab",
    version="0.1.0",
    description=(
        "Local authorization/binding lab for authorized business-logic experiments. "
        "It is intentionally vulnerable."
    ),
)


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class LabEvent(BaseModel):
    sequence: int
    kind: Literal[
        "run.created",
        "token.created",
        "payment.checked",
        "payment.committed",
        "payment.rejected",
    ]
    request_id: str
    timestamp: str
    monotonic_ns: int
    actor: str
    message: str
    snapshot: dict[str, Any]


class OrderState(BaseModel):
    order_id: str
    owner: Literal["alice", "bob"]
    amount_cents: int = ORDER_AMOUNT_CENTS
    payment_status: Literal["UNPAID", "PAID"] = "UNPAID"
    paid_by: str | None = None
    payment_token_owner: str | None = None
    payment_token_order_id: str | None = None


class TokenRecord(BaseModel):
    token: str
    owner: str
    order_id: str


class RunState:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.alice_order = OrderState(order_id=f"ord-alice-{run_id[:8]}", owner="alice")
        self.bob_order = OrderState(order_id=f"ord-bob-{run_id[:8]}", owner="bob")
        self.tokens: dict[str, TokenRecord] = {}
        self.created_at = utc_iso()
        self.events: list[LabEvent] = []
        self._sequence = 0
        self.record("run.created", actor="system", request_id="system", message="Run created")

    def order_by_id(self, order_id: str) -> OrderState:
        if order_id == self.alice_order.order_id:
            return self.alice_order
        if order_id == self.bob_order.order_id:
            return self.bob_order
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")

    def snapshot(self) -> dict[str, Any]:
        alice = self.alice_order.model_dump(mode="python")
        bob = self.bob_order.model_dump(mode="python")
        bob["bob_paid_by_alice"] = self.bob_order.paid_by == "alice"
        bob["paid_with_alice_token"] = self.bob_order.payment_token_owner == "alice"
        bob["paid_with_wrong_order_token"] = (
            self.bob_order.payment_token_order_id is not None
            and self.bob_order.payment_token_order_id != self.bob_order.order_id
        )
        bob["bob_paid_with_alice_token"] = (
            bob["paid_with_alice_token"] and bob["paid_with_wrong_order_token"]
        )
        alice["alice_paid_by_bob"] = self.alice_order.paid_by == "bob"
        return {
            "run_id": self.run_id,
            "alice_order": alice,
            "bob_order": bob,
            "token_count": len(self.tokens),
            "created_at": self.created_at,
        }

    def record(
        self,
        kind: Literal[
            "run.created",
            "token.created",
            "payment.checked",
            "payment.committed",
            "payment.rejected",
        ],
        *,
        actor: str,
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
                actor=actor,
                message=message,
                snapshot=self.snapshot(),
            )
        )


class RunView(BaseModel):
    run_id: str
    alice_order: dict[str, Any]
    bob_order: dict[str, Any]
    token_count: int
    created_at: str


class TokenRequest(BaseModel):
    order_id: str = Field(min_length=1)


class TokenView(BaseModel):
    run_id: str
    payment_token: str
    token_owner: str
    token_order_id: str


class PayRequest(BaseModel):
    payment_token: str | None = None


class EventsView(BaseModel):
    run_id: str
    events: list[LabEvent]


RUNS: OrderedDict[str, RunState] = OrderedDict()


def actor_from(request: Request) -> str:
    actor = request.headers.get("X-User", "alice").strip().lower()
    if actor not in {"alice", "bob"}:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown user")
    return actor


def request_id_from(request: Request) -> str:
    return request.headers.get("X-Request-ID", uuid.uuid4().hex)


def get_run(run_id: str) -> RunState:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return run


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "lab": "payment-binding-mismatch"}


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


@app.post("/api/runs/{run_id}/payment-tokens", response_model=TokenView)
async def create_payment_token(
    run_id: str, payload: TokenRequest, request: Request
) -> dict[str, str]:
    run = get_run(run_id)
    actor = actor_from(request)
    request_id = request_id_from(request)
    order = run.order_by_id(payload.order_id)
    if order.owner != actor:
        run.record(
            "payment.rejected",
            actor=actor,
            request_id=request_id,
            message="Token creation rejected because the actor does not own the order",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="order not owned")

    token = f"paytok-{uuid.uuid4().hex}"
    run.tokens[token] = TokenRecord(token=token, owner=actor, order_id=order.order_id)
    run.record(
        "token.created",
        actor=actor,
        request_id=request_id,
        message="Payment token created and bound to one order",
    )
    return {
        "run_id": run_id,
        "payment_token": token,
        "token_owner": actor,
        "token_order_id": order.order_id,
    }


@app.post("/api/runs/{run_id}/orders/{order_id}/pay", response_model=RunView)
async def pay_order(
    run_id: str, order_id: str, payload: PayRequest, request: Request
) -> dict[str, Any]:
    run = get_run(run_id)
    actor = actor_from(request)
    request_id = request_id_from(request)
    order = run.order_by_id(order_id)

    # Intentionally vulnerable authorization check:
    # the server verifies that the user is authenticated, but it never verifies
    # order.owner == actor.
    run.record(
        "payment.checked",
        actor=actor,
        request_id=request_id,
        message="Actor accepted for payment attempt without checking target order owner",
    )

    token_record: TokenRecord | None = None
    if payload.payment_token is not None:
        token_record = run.tokens.get(payload.payment_token)
        if token_record is None:
            run.record(
                "payment.rejected",
                actor=actor,
                request_id=request_id,
                message="Unknown payment token rejected",
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid token")
        if token_record.owner != actor:
            run.record(
                "payment.rejected",
                actor=actor,
                request_id=request_id,
                message="Payment token owner mismatch rejected",
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="token not owned")
        # Intentionally vulnerable binding check:
        # token_record.order_id is not compared with order.order_id before committing payment.

    if order.payment_status == "PAID":
        run.record(
            "payment.rejected",
            actor=actor,
            request_id=request_id,
            message="Order was already paid",
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="order already paid")

    order.payment_status = "PAID"
    order.paid_by = actor
    if token_record is not None:
        order.payment_token_owner = token_record.owner
        order.payment_token_order_id = token_record.order_id
    run.record(
        "payment.committed",
        actor=actor,
        request_id=request_id,
        message="Payment committed after incomplete ownership/binding checks",
    )
    return run.snapshot()
