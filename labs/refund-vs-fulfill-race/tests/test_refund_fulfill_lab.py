from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

LAB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_ROOT))

from refund_lab.main import app  # noqa: E402


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://lab") as instance:
        yield instance


async def new_run(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/runs")
    assert response.status_code == 201
    state = response.json()
    assert state["refunded_and_fulfilled"] is False
    return state["run_id"]


@pytest.mark.asyncio
async def test_sequential_refund_then_fulfill_is_rejected(client: httpx.AsyncClient) -> None:
    run_id = await new_run(client)

    refund = await client.post(f"/api/runs/{run_id}/refund")
    fulfill = await client.post(f"/api/runs/{run_id}/fulfill")

    assert refund.status_code == 200
    assert fulfill.status_code == 409
    state = (await client.get(f"/api/runs/{run_id}/state")).json()
    assert state["refund_status"] == "REFUNDED"
    assert state["fulfillment_status"] == "PENDING"
    assert state["refunded_and_fulfilled"] is False


@pytest.mark.asyncio
async def test_concurrent_refund_and_fulfill_reaches_conflicting_state(
    client: httpx.AsyncClient,
) -> None:
    run_id = await new_run(client)

    responses = await asyncio.gather(
        client.post(f"/api/runs/{run_id}/refund"),
        client.post(f"/api/runs/{run_id}/fulfill"),
    )

    assert [response.status_code for response in responses] == [200, 200]
    state = (await client.get(f"/api/runs/{run_id}/state")).json()
    assert state["refund_status"] == "REFUNDED"
    assert state["fulfillment_status"] == "FULFILLED"
    assert state["refunded_and_fulfilled"] is True