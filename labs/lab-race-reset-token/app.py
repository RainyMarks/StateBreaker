"""Vulnerable lab: password reset token reuse with a check-then-act race.

Business invariant: a reset token can be used exactly once. The use handler
checks ``status == "unused"`` and only later writes ``"used"`` — with an
``await`` in between — so concurrent uses can all pass the check and apply the
password reset more than once. This is an intentional teaching vulnerability.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

RACE_WINDOW_SECONDS = 0.05


class IssueRequest(BaseModel):
    """Both fields optional: the server generates a token when none is given."""

    user: str = "alice"
    token: str | None = None


class UseRequest(BaseModel):
    new_password: str = "changed-password"


def _empty_user(user: str) -> dict[str, Any]:
    return {
        "user": user,
        "password_version": 0,
        "reset_count": 0,
        "last_reset_token": None,
        "password_marker": "initial",
    }


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory state."""
    app = FastAPI(title="lab-race-reset-token")

    tokens: dict[str, dict[str, Any]] = {}
    users: dict[str, dict[str, Any]] = {}
    write_lock = asyncio.Lock()

    @app.post("/tokens/issue", status_code=201, response_model=None)
    async def issue_token(body: IssueRequest) -> dict[str, Any] | JSONResponse:
        token = body.token or uuid.uuid4().hex
        async with write_lock:
            if token in tokens:
                return JSONResponse(status_code=409, content={"detail": "duplicate"})
            user = users.setdefault(body.user, _empty_user(body.user))
            tokens[token] = {
                "token": token,
                "user": body.user,
                "status": "unused",
                "use_count": 0,
            }
            user["last_reset_token"] = token
        return {"token": {"token": token, "user": body.user, "status": "unused", "use_count": 0}}

    @app.post("/tokens/{token}/use", response_model=None)
    async def use_token(token: str, body: UseRequest) -> dict[str, Any] | JSONResponse:
        reset_token = tokens.get(token)
        if reset_token is None:
            return JSONResponse(status_code=404, content={"detail": "unknown token"})
        if reset_token["status"] != "unused":
            return JSONResponse(status_code=409, content={"used": False, "reason": "used"})
        # INTENTIONAL VULNERABILITY (check-then-act): the unused check above and
        # the mutation below are separated by an await. Two concurrent uses both
        # observe "unused", both pass the check, and both reset the user's password.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        async with write_lock:
            reset_token["status"] = "used"
            reset_token["use_count"] += 1
            user = users.setdefault(reset_token["user"], _empty_user(reset_token["user"]))
            user["password_version"] += 1
            user["reset_count"] += 1
            user["last_reset_token"] = token
            user["password_marker"] = body.new_password
        return {
            "used": True,
            "user": reset_token["user"],
            "password_version": user["password_version"],
            "use_count": reset_token["use_count"],
        }

    @app.get("/tokens/{token}", response_model=None)
    async def get_token(token: str) -> dict[str, Any] | JSONResponse:
        reset_token = tokens.get(token)
        if reset_token is None:
            return JSONResponse(status_code=404, content={"detail": "unknown token"})
        return {"token": reset_token}

    @app.get("/users/{user}")
    async def get_user(user: str) -> dict[str, Any]:
        return {"user": users.get(user, _empty_user(user))}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            tokens.clear()
            users.clear()
        return {"reset": True}

    return app


app = create_app()
