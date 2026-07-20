from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

LAB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_ROOT))

from payment_lab.main import app, set_payment_guard  # noqa: E402


@pytest.fixture(autouse=True)
def reset_guard() -> None:
    set_payment_guard(False)


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://lab") as instance:
        yield instance


async def new_order(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/orders", json={})
    assert response.status_code == 201
    state = response.json()
    assert state["payment_status"] == "UNPAID"
    assert state["confirmed_without_payment"] is False
    return state["order_id"]


@pytest.mark.asyncio
async def test_normal_pay_then_confirm_is_consistent(client: httpx.AsyncClient) -> None:
    order_id = await new_order(client)

    paid = await client.post(f"/api/orders/{order_id}/pay")
    confirmed = await client.post(f"/api/orders/{order_id}/confirm")

    assert paid.status_code == 200
    assert confirmed.status_code == 200
    state = (await client.get(f"/api/orders/{order_id}/state")).json()
    assert state["payment_status"] == "PAID"
    assert state["order_status"] == "CONFIRMED"
    assert state["confirmed_without_payment"] is False


@pytest.mark.asyncio
async def test_vulnerable_confirm_allows_payment_step_skip(client: httpx.AsyncClient) -> None:
    order_id = await new_order(client)

    confirmed = await client.post(f"/api/orders/{order_id}/confirm")

    assert confirmed.status_code == 200
    state = (await client.get(f"/api/orders/{order_id}/state")).json()
    assert state["payment_status"] == "UNPAID"
    assert state["order_status"] == "CONFIRMED"
    assert state["confirmed_without_payment"] is True


@pytest.mark.asyncio
async def test_guarded_confirm_rejects_payment_step_skip(client: httpx.AsyncClient) -> None:
    set_payment_guard(True)
    order_id = await new_order(client)

    confirmed = await client.post(f"/api/orders/{order_id}/confirm")

    assert confirmed.status_code == 409
    state = (await client.get(f"/api/orders/{order_id}/state")).json()
    assert state["payment_status"] == "UNPAID"
    assert state["order_status"] == "CREATED"
    assert state["confirmed_without_payment"] is False