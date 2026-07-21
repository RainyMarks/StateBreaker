"""Vulnerable lab: cross-user invite acceptance with a check-then-act race.

Business invariant: an invite can be accepted by exactly one member. The
accept handler checks ``state == "open"`` and only later writes ``"taken"`` —
with an ``await`` in between — so two different users accepting concurrently
both pass the check and both get credited. Intentional teaching vulnerability.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

RACE_WINDOW_SECONDS = 0.05


class MintRequest(BaseModel):
    slug: str | None = None
    bonus: int = 25


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory state."""
    app = FastAPI(title="lab-crossuser-claim")

    invites: dict[str, dict[str, Any]] = {}
    members: dict[str, dict[str, Any]] = {}
    write_lock = asyncio.Lock()

    @app.post("/invites/mint", status_code=201, response_model=None)
    async def mint_invite(body: MintRequest) -> dict[str, Any] | JSONResponse:
        slug = body.slug or uuid.uuid4().hex[:12]
        async with write_lock:
            if slug in invites:
                return JSONResponse(status_code=409, content={"detail": "duplicate"})
            invites[slug] = {
                "slug": slug,
                "bonus": body.bonus,
                "state": "open",
                "holder": None,
            }
        return {"invite": {"slug": slug, "bonus": body.bonus, "state": "open"}}

    @app.post("/invites/{slug}/accept", response_model=None)
    async def accept_invite(
        slug: str, x_user_id: Annotated[str, Header()] = "anonymous"
    ) -> dict[str, Any] | JSONResponse:
        invite = invites.get(slug)
        if invite is None:
            return JSONResponse(status_code=404, content={"detail": "unknown invite"})
        if invite["state"] != "open":
            return JSONResponse(status_code=409, content={"accepted": False, "reason": "taken"})
        # INTENTIONAL VULNERABILITY (check-then-act): two different users can
        # both observe "open" before either write lands.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        async with write_lock:
            invite["state"] = "taken"
            invite["holder"] = x_user_id
            member = members.setdefault(
                x_user_id, {"user": x_user_id, "points": 0, "invites_accepted": 0}
            )
            member["points"] += invite["bonus"]
            member["invites_accepted"] += 1
        return {"accepted": True, "bonus": invite["bonus"], "holder": x_user_id}

    @app.get("/invites/{slug}", response_model=None)
    async def get_invite(slug: str) -> dict[str, Any] | JSONResponse:
        invite = invites.get(slug)
        if invite is None:
            return JSONResponse(status_code=404, content={"detail": "unknown invite"})
        return {"invite": invite}

    @app.get("/members/{user_id}")
    async def get_member(user_id: str) -> dict[str, Any]:
        member = members.get(user_id)
        if member is None:
            member = {"user": user_id, "points": 0, "invites_accepted": 0}
        return {"member": member}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            invites.clear()
            members.clear()
        return {"reset": True}

    return app


app = create_app()
