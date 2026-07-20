from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

LAB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_ROOT))

from callback_lab.main import ORDER_AMOUNT_CENTS, PAYMENT_EVENT_ID, app  # noqa: E402


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://lab") as instance:
        yield instance


async def new_run(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/runs")
    assert response.status_code == 201
    state = response.json()
    assert state["merchant_credit_cents"] == 0
    return state["run_id"]


@pytest.mark.asyncio
async def test_sequential_duplicate_callback_is_rejected(client: httpx.AsyncClient) -> None:
    run_id = await new_run(client)
    payload = {"event_id": PAYMENT_EVENT_ID, "amount_cents": ORDER_AMOUNT_CENTS}

    first = await client.post(f"/api/runs/{run_id}/payment-callback", json=payload)
    second = await client.post(f"/api/runs/{run_id}/payment-callback", json=payload)

    assert first.status_code == 200
    assert second.status_code == 409
    state = (await client.get(f"/api/runs/{run_id}/state")).json()
    assert state["merchant_credit_cents"] == ORDER_AMOUNT_CENTS
    assert state["payment_apply_count"] == 1
    assert state["duplicate_callback_observed"] is False


@pytest.mark.asyncio
async def test_concurrent_duplicate_callbacks_apply_twice(client: httpx.AsyncClient) -> None:
    run_id = await new_run(client)
    payload = {"event_id": PAYMENT_EVENT_ID, "amount_cents": ORDER_AMOUNT_CENTS}

    responses = await asyncio.gather(
        client.post(f"/api/runs/{run_id}/payment-callback", json=payload),
        client.post(f"/api/runs/{run_id}/payment-callback", json=payload),
    )

    assert [response.status_code for response in responses] == [200, 200]
    state = (await client.get(f"/api/runs/{run_id}/state")).json()
    assert state["merchant_credit_cents"] == ORDER_AMOUNT_CENTS * 2
    assert state["payment_apply_count"] == 2
    assert state["duplicate_callback_observed"] is True