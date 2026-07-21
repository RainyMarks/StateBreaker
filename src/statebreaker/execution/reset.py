"""Experiment isolation: reset strategies prepare a clean slate per trial."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from statebreaker.execution.client import HttpSender
from statebreaker.models.execution import TrialContext


@runtime_checkable
class ResetStrategy(Protocol):
    """Prepares (and optionally cleans up) isolated state for one trial."""

    strategy_id: str

    async def prepare_trial(self, context_id: str) -> TrialContext:
        ...

    async def cleanup_trial(self, context: TrialContext) -> None:
        ...


class NoResetStrategy:
    """No isolation; trials share target state (weakest evidence)."""

    strategy_id = "none"

    async def prepare_trial(self, context_id: str) -> TrialContext:
        return TrialContext(context_id=context_id)

    async def cleanup_trial(self, context: TrialContext) -> None:
        return None


class ApiResetStrategy:
    """Call a configured reset endpoint before every trial."""

    strategy_id = "api"

    def __init__(self, sender: HttpSender, endpoint: str, *, session_id: str = "default") -> None:
        self._sender = sender
        self._endpoint = endpoint
        self._session_id = session_id

    async def prepare_trial(self, context_id: str) -> TrialContext:
        exchange = await self._sender.send(
            session_id=self._session_id,
            method="POST",
            path_or_url=self._endpoint,
        )
        ok = exchange.response_status < 400
        return TrialContext(
            context_id=context_id,
            metadata={"reset_endpoint": self._endpoint, "reset_ok": ok},
        )

    async def cleanup_trial(self, context: TrialContext) -> None:
        return None


class FreshResourceResetStrategy:
    """Rely on the workflow's own resource-creation steps for isolation.

    The recorded variables of a trial (fresh resource ids, tokens) are kept in
    the context so the trial can re-bind templates to the new resources.
    """

    strategy_id = "fresh-resource"

    def __init__(self, setup_variables: dict[str, Any] | None = None) -> None:
        self._setup_variables = dict(setup_variables or {})

    async def prepare_trial(self, context_id: str) -> TrialContext:
        return TrialContext(context_id=context_id, variables=dict(self._setup_variables))

    async def cleanup_trial(self, context: TrialContext) -> None:
        return None
