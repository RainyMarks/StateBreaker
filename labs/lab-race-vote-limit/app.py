"""Vulnerable lab: one-vote-per-user poll with a race window.

Business invariant: each user may vote at most once in a poll. The vote handler
checks whether the user has voted and only later appends the vote record — with
an ``await`` in between — so concurrent requests from the same user can all pass
the check and be counted. This is an intentional teaching vulnerability.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

RACE_WINDOW_SECONDS = 0.05


class PollRequest(BaseModel):
    """Both fields optional: defaults keep the lab quick to exercise locally."""

    question: str | None = None
    choices: list[str] | None = None


class VoteRequest(BaseModel):
    choice: str = "yes"


def _public_poll(poll: dict[str, Any]) -> dict[str, Any]:
    counts = {choice: 0 for choice in poll["choices"]}
    for vote in poll["votes"]:
        counts[vote["choice"]] = counts.get(vote["choice"], 0) + 1
    return {
        "poll_id": poll["poll_id"],
        "question": poll["question"],
        "choices": poll["choices"],
        "vote_count": len(poll["votes"]),
        "votes_by_choice": counts,
        "voters": [vote["user"] for vote in poll["votes"]],
        "unique_voters": sorted({vote["user"] for vote in poll["votes"]}),
    }


def create_app() -> FastAPI:
    """Build a fresh app instance with empty in-memory state."""
    app = FastAPI(title="lab-race-vote-limit")

    polls: dict[str, dict[str, Any]] = {}
    write_lock = asyncio.Lock()

    @app.post("/polls/{poll_id}", status_code=201, response_model=None)
    async def create_poll(poll_id: str, body: PollRequest) -> dict[str, Any] | JSONResponse:
        choices = body.choices or ["yes", "no"]
        if not choices:
            return JSONResponse(status_code=400, content={"detail": "choices required"})
        async with write_lock:
            if poll_id in polls:
                return JSONResponse(status_code=409, content={"detail": "duplicate"})
            poll = {
                "poll_id": poll_id,
                "question": body.question or "Race condition poll",
                "choices": choices,
                "votes": [],
            }
            polls[poll_id] = poll
        return {"poll": _public_poll(poll)}

    @app.post("/polls/{poll_id}/vote", response_model=None)
    async def vote_poll(
        poll_id: str,
        body: VoteRequest,
        x_user_id: Annotated[str, Header()] = "anonymous",
    ) -> dict[str, Any] | JSONResponse:
        poll = polls.get(poll_id)
        if poll is None:
            return JSONResponse(status_code=404, content={"detail": "unknown poll"})
        if body.choice not in poll["choices"]:
            return JSONResponse(status_code=400, content={"detail": "unknown choice"})
        if x_user_id in {vote["user"] for vote in poll["votes"]}:
            return JSONResponse(status_code=409, content={"voted": False, "reason": "already_voted"})
        # INTENTIONAL VULNERABILITY (check-then-act): the user-voted check and
        # the append are separated by an await, so same-user concurrent votes
        # all pass the invariant check before any one of them is recorded.
        await asyncio.sleep(RACE_WINDOW_SECONDS)
        async with write_lock:
            poll["votes"].append({"user": x_user_id, "choice": body.choice})
        return {"voted": True, "vote_count": len(poll["votes"])}

    @app.get("/polls/{poll_id}", response_model=None)
    async def get_poll(poll_id: str) -> dict[str, Any] | JSONResponse:
        poll = polls.get(poll_id)
        if poll is None:
            return JSONResponse(status_code=404, content={"detail": "unknown poll"})
        return {"poll": _public_poll(poll)}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            polls.clear()
        return {"reset": True}

    return app


app = create_app()
