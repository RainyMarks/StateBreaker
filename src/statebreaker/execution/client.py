"""Sequential, scope-guarded, rate-limited, budget-counted HTTP sending."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode, urlsplit

import anyio
import httpx

from statebreaker.config.loader import ScopeGuard
from statebreaker.errors import BudgetExhaustedError, ExecutionError
from statebreaker.execution.sessions import SessionManager
from statebreaker.models.capture import BodyEncoding, HttpExchange
from statebreaker.models.execution import HttpResponseRecord, ScanBudget


def append_query(url: str, query: Mapping[str, Any] | None) -> str:
    """Append ``query`` params to ``url``, preserving any existing query string.

    Values are stringified with ``str()``. Shared by the sequential sender and
    the raw-transport request builder so URL assembly stays identical.
    """
    if not query:
        return url
    separator = "&" if "?" in url else "?"
    return url + separator + urlencode({key: str(value) for key, value in query.items()})


class BudgetTracker:
    """Counts spend against a :class:`ScanBudget`; raises when exceeded."""

    def __init__(self, budget: ScanBudget) -> None:
        self.budget = budget
        self.requests_used = 0
        self.trials_used = 0
        self.started_monotonic = time.monotonic()

    def count_request(self, n: int = 1) -> None:
        self.requests_used += n
        if self.requests_used > self.budget.maximum_requests:
            raise BudgetExhaustedError(
                f"request budget exceeded ({self.requests_used} > "
                f"{self.budget.maximum_requests})"
            )

    def count_trial(self) -> None:
        self.trials_used += 1
        if self.trials_used > self.budget.maximum_trial_rounds:
            raise BudgetExhaustedError("trial round budget exceeded")

    def check_time(self) -> None:
        elapsed_minutes = (time.monotonic() - self.started_monotonic) / 60.0
        if elapsed_minutes > self.budget.maximum_minutes:
            raise BudgetExhaustedError("time budget exceeded")

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_monotonic


class HttpSender:
    """Fires single requests through per-session clients with guard rails."""

    def __init__(
        self,
        sessions: SessionManager,
        scope: ScopeGuard,
        *,
        budget: BudgetTracker | None = None,
        requests_per_second: float = 10.0,
    ) -> None:
        self._sessions = sessions
        self._scope = scope
        self._budget = budget
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._throttle_lock = anyio.Lock()
        self._last_sent = 0.0

    async def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._throttle_lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_sent)
            if wait > 0:
                await anyio.sleep(wait)
            self._last_sent = time.monotonic()

    def absolute_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        if path_or_url.startswith("/"):
            parsed = urlsplit(self._sessions.base_url)
            return f"{parsed.scheme}://{parsed.netloc}{path_or_url}"
        return self._sessions.base_url + path_or_url

    def session_headers(self, session_id: str) -> dict[str, str]:
        """Identity headers of a session (for raw-transport request building)."""
        return self._sessions.session_headers(session_id)

    async def send(
        self,
        *,
        session_id: str,
        method: str,
        path_or_url: str,
        query: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        exchange_id: str = "",
    ) -> HttpExchange:
        """Send one request and return the full normalized exchange."""
        url = append_query(self.absolute_url(path_or_url), query)
        self._scope.check_url(url)
        if self._budget is not None:
            self._budget.count_request()
        await self._throttle()

        client = self._sessions.client_for(session_id)
        started_ns = time.perf_counter_ns()
        try:
            response = await client.request(
                method.upper(), url, headers=headers or {}, content=content
            )
        except httpx.HTTPError as exc:
            raise ExecutionError(f"request failed: {method} {url}: {exc}") from exc
        completed_ns = time.perf_counter_ns()

        body: Any = None
        encoding: BodyEncoding = "none"
        content_type = response.headers.get("content-type", "")
        if response.content:
            if "json" in content_type:
                try:
                    body = response.json()
                    encoding = "json"
                except ValueError:
                    body = response.text
                    encoding = "raw"
            else:
                body = response.text
                encoding = "raw"

        return HttpExchange(
            exchange_id=exchange_id or f"sent-{started_ns}",
            session_id=session_id,
            method=method.upper(),
            url=url,
            request_headers=dict(headers or {}),
            request_body=content.decode(errors="replace") if content else None,
            request_body_encoding="raw" if content else "none",
            response_status=response.status_code,
            response_headers={k.lower(): v for k, v in response.headers.items()},
            response_body=body,
            response_body_encoding=encoding,
            started_at_ns=started_ns,
            completed_at_ns=completed_ns,
        )


def exchange_to_record(exchange: HttpExchange, instance_id: str) -> HttpResponseRecord:
    """Project a full exchange into the trial-level response record."""
    return HttpResponseRecord(
        instance_id=instance_id,
        status=exchange.response_status,
        headers=exchange.response_headers,
        body=exchange.response_body,
        started_at_ns=exchange.started_at_ns,
        completed_at_ns=exchange.completed_at_ns,
    )
