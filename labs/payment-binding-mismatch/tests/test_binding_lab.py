from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

LAB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_ROOT))

from binding_lab.main import app  # noqa: E402


@pytest.mark.asyncio
async def test_alice_can_pay_bob_order_without_ownership_check() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/api/runs", headers={"X-User": "alice"})
        created.raise_for_status()
        body = created.json()
        run_id = body["run_id"]
        bob_order_id = body["bob_order"]["order_id"]

        paid = await client.post(
            f"/api/runs/{run_id}/orders/{bob_order_id}/pay",
            headers={"X-User": "alice"},
            json={},
        )
        paid.raise_for_status()

        state = (await client.get(f"/api/runs/{run_id}/state")).json()
        assert state["bob_order"]["payment_status"] == "PAID"
        assert state["bob_order"]["paid_by"] == "alice"
        assert state["bob_order"]["bob_paid_by_alice"] is True


@pytest.mark.asyncio
async def test_alice_token_for_alice_order_can_pay_bob_order() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/api/runs", headers={"X-User": "alice"})
        created.raise_for_status()
        body = created.json()
        run_id = body["run_id"]
        alice_order_id = body["alice_order"]["order_id"]
        bob_order_id = body["bob_order"]["order_id"]

        token_response = await client.post(
            f"/api/runs/{run_id}/payment-tokens",
            headers={"X-User": "alice"},
            json={"order_id": alice_order_id},
        )
        token_response.raise_for_status()
        token = token_response.json()["payment_token"]

        paid = await client.post(
            f"/api/runs/{run_id}/orders/{bob_order_id}/pay",
            headers={"X-User": "alice"},
            json={"payment_token": token},
        )
        paid.raise_for_status()

        state = (await client.get(f"/api/runs/{run_id}/state")).json()
        assert state["bob_order"]["payment_status"] == "PAID"
        assert state["bob_order"]["paid_by"] == "alice"
        assert state["bob_order"]["payment_token_owner"] == "alice"
        assert state["bob_order"]["payment_token_order_id"] == alice_order_id
        assert state["bob_order"]["bob_paid_with_alice_token"] is True
