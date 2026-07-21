"""Vulnerable lab: one-time coupon claim with a check-then-act race.

Business invariant: a coupon can be claimed exactly once. The claim handler
checks ``status == "fresh"`` and only later writes ``"claimed"`` — with an
``await`` in between — so concurrent claims can all pass the check and all
credit their user records. This is an intentional teaching vulnerability.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

RACE_WINDOW_SECONDS = 0.05


class IssueRequest(BaseModel):
    """Both fields optional: the server generates a code when none is given."""

    code: str | None = None
    amount: int = 30


def _empty_user(user_id: str) -> dict[str, Any]:
    return {"user": user_id, "coupon_total": 0, "claim_count": 0, "claimed_codes": []}


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory state."""
    app = FastAPI(title="lab-race-coupon-claim")

    coupons: dict[str, dict[str, Any]] = {}
    users: dict[str, dict[str, Any]] = {}
    write_lock = asyncio.Lock()

    @app.post("/coupons/issue", status_code=201, response_model=None)
    async def issue_coupon(body: IssueRequest) -> dict[str, Any] | JSONResponse:
        code = body.code or uuid.uuid4().hex[:12]
        async with write_lock:
            if code in coupons:
                return JSONResponse(status_code=409, content={"detail": "duplicate"})
            coupons[code] = {
                "code": code,
                "amount": body.amount,
                "status": "fresh",
                "claimed_by": None,
                "claim_count": 0,
                "claimers": [],
            }
        return {"coupon": {"code": code, "amount": body.amount, "status": "fresh"}}

    @app.post("/coupons/{code}/claim", response_model=None)
    async def claim_coupon(
        code: str, x_user_id: Annotated[str, Header()] = "anonymous"
    ) -> dict[str, Any] | JSONResponse:
        coupon = coupons.get(code)
        if coupon is None:
            return JSONResponse(status_code=404, content={"detail": "unknown coupon"})
        if coupon["status"] != "fresh":
            return JSONResponse(status_code=409, content={"claimed": False, "reason": "claimed"})
        # INTENTIONAL VULNERABILITY (check-then-act): the freshness check above
        # happens before this await, and the write below does not re-check state.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        async with write_lock:
            coupon["status"] = "claimed"
            coupon["claimed_by"] = x_user_id
            coupon["claim_count"] += 1
            coupon["claimers"].append(x_user_id)
            user = users.setdefault(x_user_id, _empty_user(x_user_id))
            user["coupon_total"] += coupon["amount"]
            user["claim_count"] += 1
            user["claimed_codes"].append(code)
        return {
            "claimed": True,
            "code": code,
            "amount": coupon["amount"],
            "user": x_user_id,
        }

    @app.get("/coupons/{code}", response_model=None)
    async def get_coupon(code: str) -> dict[str, Any] | JSONResponse:
        coupon = coupons.get(code)
        if coupon is None:
            return JSONResponse(status_code=404, content={"detail": "unknown coupon"})
        return {"coupon": coupon}

    @app.get("/users/{user}")
    async def get_user(user: str) -> dict[str, Any]:
        return {"user": users.get(user, _empty_user(user))}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            coupons.clear()
            users.clear()
        return {"reset": True}

    return app


app = create_app()
