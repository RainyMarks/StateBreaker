"""Vulnerable lab: one-shot perk redemption with a check-then-act race.

Business invariant: a perk code can be claimed exactly once. The claim handler
checks ``status == "fresh"`` and only later writes ``"spent"`` — with an
``await`` in between — so two concurrent claims both pass the check and the
account is credited twice. This is an intentional teaching vulnerability.
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
    credit: int = 50


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory state."""
    app = FastAPI(title="lab-oneshot-redemption")

    perks: dict[str, dict[str, Any]] = {}
    accounts: dict[str, dict[str, Any]] = {}
    write_lock = asyncio.Lock()

    @app.post("/perks/issue", status_code=201, response_model=None)
    async def issue_perk(body: IssueRequest) -> dict[str, Any] | JSONResponse:
        code = body.code or uuid.uuid4().hex
        async with write_lock:
            if code in perks:
                return JSONResponse(status_code=409, content={"detail": "duplicate"})
            perks[code] = {"code": code, "credit": body.credit, "status": "fresh", "owner": None}
        return {"perk": {"code": code, "credit": body.credit, "status": "fresh"}}

    @app.post("/perks/{code}/claim", response_model=None)
    async def claim_perk(
        code: str, x_user_id: Annotated[str, Header()] = "anonymous"
    ) -> dict[str, Any] | JSONResponse:
        perk = perks.get(code)
        if perk is None:
            return JSONResponse(status_code=404, content={"detail": "unknown perk"})
        if perk["status"] != "fresh":
            return JSONResponse(status_code=409, content={"claimed": False, "reason": "spent"})
        # INTENTIONAL VULNERABILITY (check-then-act): the status check above and
        # the mutation below are separated by an await, and only the mutation is
        # lock-protected. Two concurrent claims both observe "fresh", both pass
        # the check, and both credit the account.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        async with write_lock:
            perk["status"] = "spent"
            perk["owner"] = x_user_id
            account = accounts.setdefault(
                x_user_id, {"user": x_user_id, "credit_total": 0, "claim_count": 0}
            )
            account["credit_total"] += perk["credit"]
            account["claim_count"] += 1
        return {"claimed": True, "credit": perk["credit"], "owner": x_user_id}

    @app.get("/perks/{code}", response_model=None)
    async def get_perk(code: str) -> dict[str, Any] | JSONResponse:
        perk = perks.get(code)
        if perk is None:
            return JSONResponse(status_code=404, content={"detail": "unknown perk"})
        return {"perk": perk}

    @app.get("/accounts/{user_id}")
    async def get_account(user_id: str) -> dict[str, Any]:
        account = accounts.get(user_id)
        if account is None:
            account = {"user": user_id, "credit_total": 0, "claim_count": 0}
        return {"account": account}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            perks.clear()
            accounts.clear()
        return {"reset": True}

    return app


app = create_app()
