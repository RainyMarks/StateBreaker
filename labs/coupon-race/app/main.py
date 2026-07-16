"""A tiny, resettable TOCTOU lab. It is intentionally vulnerable by design."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

COUPON_CODE = "BUG50"
COUPON_VALUE = 50
RACE_WINDOW_SECONDS = 0.150
MAX_RUNS = 100
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="老王奶茶铺 · BUG50 竞态靶场",
    version="0.1.0",
    description="仅供本地和已授权的业务逻辑安全实验使用。",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class LabEvent(BaseModel):
    sequence: int
    kind: Literal["run.created", "coupon.checked", "coupon.committed", "coupon.rejected"]
    request_id: str
    timestamp: str
    monotonic_ns: int
    message: str
    snapshot: dict[str, Any]


class RunState:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.discount_yuan = 0
        self.coupon_used = False
        self.successful_redemptions = 0
        self.created_at = utc_iso()
        self.events: list[LabEvent] = []
        self._sequence = 0
        self.record(
            "run.created",
            request_id="system",
            message="新桌已开，BUG50 正在假装自己只能用一次。",
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "coupon_code": COUPON_CODE,
            "coupon_value": COUPON_VALUE,
            "discount_yuan": self.discount_yuan,
            "coupon_used": self.coupon_used,
            "successful_redemptions": self.successful_redemptions,
            "created_at": self.created_at,
        }

    def record(
        self,
        kind: Literal[
            "run.created", "coupon.checked", "coupon.committed", "coupon.rejected"
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
    coupon_code: str
    coupon_value: int
    discount_yuan: int
    coupon_used: bool
    successful_redemptions: int
    created_at: str


class RedeemRequest(BaseModel):
    coupon_code: str = Field(default=COUPON_CODE, min_length=1)


class EventsView(BaseModel):
    run_id: str
    events: list[LabEvent]


RUNS: OrderedDict[str, RunState] = OrderedDict()


def get_run(run_id: str) -> RunState:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实验不存在或已过期")
    return run


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "lab": "coupon-race"}


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


@app.post("/api/runs/{run_id}/redeem", response_model=RunView)
async def redeem_coupon(run_id: str, payload: RedeemRequest, request: Request) -> dict[str, Any]:
    run = get_run(run_id)
    request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
    if payload.coupon_code != COUPON_CODE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="这不是本店的券")

    # Intentionally vulnerable TOCTOU: there is no lock and no atomic compare-and-set.
    if run.coupon_used:
        run.record(
            "coupon.rejected",
            request_id=request_id,
            message="检查失败：券已经用过，老王这次反应过来了。",
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="优惠券已经使用")

    run.record(
        "coupon.checked",
        request_id=request_id,
        message="检查通过：此刻看起来还没用过。",
    )
    await asyncio.sleep(RACE_WINDOW_SECONDS)

    # Deliberately do not re-check coupon_used here. Concurrent requests all commit.
    run.discount_yuan += COUPON_VALUE
    run.successful_redemptions += 1
    run.coupon_used = True
    run.record(
        "coupon.committed",
        request_id=request_id,
        message=f"写入完成：优惠再加 {COUPON_VALUE} 元。",
    )
    return run.snapshot()
