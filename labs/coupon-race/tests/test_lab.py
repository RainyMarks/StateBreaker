from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

LAB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_ROOT))

from app.main import COUPON_CODE, app  # noqa: E402


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://lab") as instance:
        yield instance


async def new_run(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/runs")
    assert response.status_code == 201
    state = response.json()
    assert state["discount_yuan"] == 0
    assert state["coupon_used"] is False
    return state["run_id"]


@pytest.mark.asyncio
async def test_sequential_redemption_only_applies_once(client: httpx.AsyncClient) -> None:
    run_id = await new_run(client)
    first = await client.post(
        f"/api/runs/{run_id}/redeem", json={"coupon_code": COUPON_CODE}
    )
    second = await client.post(
        f"/api/runs/{run_id}/redeem", json={"coupon_code": COUPON_CODE}
    )

    assert first.status_code == 200
    assert second.status_code == 409
    state = (await client.get(f"/api/runs/{run_id}/state")).json()
    assert state["discount_yuan"] == 50
    assert state["successful_redemptions"] == 1


@pytest.mark.asyncio
async def test_concurrent_redemption_breaks_real_state_ten_times(
    client: httpx.AsyncClient,
) -> None:
    for attempt in range(10):
        run_id = await new_run(client)
        responses = await asyncio.gather(
            client.post(
                f"/api/runs/{run_id}/redeem",
                json={"coupon_code": COUPON_CODE},
                headers={"X-Request-ID": f"left-{attempt}"},
            ),
            client.post(
                f"/api/runs/{run_id}/redeem",
                json={"coupon_code": COUPON_CODE},
                headers={"X-Request-ID": f"right-{attempt}"},
            ),
        )
        assert [response.status_code for response in responses] == [200, 200]

        state = (await client.get(f"/api/runs/{run_id}/state")).json()
        assert state["discount_yuan"] == 100
        assert state["successful_redemptions"] == 2

        events = (await client.get(f"/api/runs/{run_id}/events")).json()["events"]
        kinds = [event["kind"] for event in events]
        assert kinds[1:3] == ["coupon.checked", "coupon.checked"]
        assert kinds[3:5] == ["coupon.committed", "coupon.committed"]


@pytest.mark.asyncio
async def test_unknown_run_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/runs/not-a-real-run/state")
    assert response.status_code == 404
