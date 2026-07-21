"""Test helpers: load labs in-process and record normal flows as traces."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Any

import httpx

from statebreaker.models.capture import CapturedTrace, HttpExchange

LABS_ROOT = Path(__file__).resolve().parents[2] / "labs"
LAB_BASE_URL = "http://lab.local"


def load_lab_app(lab_dir: str) -> Any:
    """Import ``labs/<lab_dir>/app.py`` and return a fresh FastAPI app."""
    spec = importlib.util.spec_from_file_location(
        lab_dir.replace("-", "_"), LABS_ROOT / lab_dir / "app.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load lab {lab_dir}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create_app()


def asgi_transport(app: Any) -> httpx.AsyncBaseTransport:
    return httpx.ASGITransport(app=app)


class FlowRecorder:
    """Send requests over ASGI and record them as a CapturedTrace.

    Stands in for a browser/HAR capture in tests: the scanner under test only
    ever sees the resulting trace, never the lab itself.
    """

    def __init__(self, app: Any, *, capture_id: str = "recorded") -> None:
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=LAB_BASE_URL
        )
        self._capture_id = capture_id
        self._exchanges: list[HttpExchange] = []

    async def record(
        self,
        method: str,
        path: str,
        *,
        session_id: str = "alice",
        headers: dict[str, str] | None = None,
        json_body: Any = None,
    ) -> HttpExchange:
        started = time.perf_counter_ns()
        response = await self._client.request(method, path, headers=headers, json=json_body)
        completed = time.perf_counter_ns()

        response_body: Any = None
        response_encoding = "none"
        if response.content:
            if "json" in response.headers.get("content-type", ""):
                response_body = response.json()
                response_encoding = "json"
            else:
                response_body = response.text
                response_encoding = "raw"

        exchange = HttpExchange(
            exchange_id=f"rec-{len(self._exchanges) + 1}",
            session_id=session_id,
            method=method.upper(),
            url=str(response.url),
            request_headers={k.lower(): v for k, v in (headers or {}).items()},
            request_body=json_body,
            request_body_encoding="json" if json_body is not None else "none",
            response_status=response.status_code,
            response_headers={k.lower(): v for k, v in response.headers.items()},
            response_body=response_body,
            response_body_encoding=response_encoding,  # type: ignore[arg-type]
            started_at_ns=started,
            completed_at_ns=completed,
        )
        self._exchanges.append(exchange)
        return exchange

    def trace(self, *, project: str = "lab") -> CapturedTrace:
        return CapturedTrace(
            capture_id=self._capture_id,
            source="manual",
            project=project,
            base_url=LAB_BASE_URL,
            sessions=sorted({e.session_id for e in self._exchanges}),
            exchanges=list(self._exchanges),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
