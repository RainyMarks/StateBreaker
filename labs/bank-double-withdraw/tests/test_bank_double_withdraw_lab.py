from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

LAB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_ROOT))

from bank_lab.main import DEFAULT_WITHDRAW_CENTS, app  # noqa: E402


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://lab") as instance:
        yield instance


async def new_run(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/runs")
    assert response.status_code == 201
    state = response.json()
    assert state["balance_cents"] == DEFAULT_WITHDRAW_CENTS
    assert state["overdraft"] is False
    return state["run_id"]


@pytest.mark.asyncio
async def test_sequential_full_balance_withdrawal_only_succeeds_once(
    client: httpx.AsyncClient,
) -> None:
    run_id = await new_run(client)

    first = await client.post(
        f"/api/runs/{run_id}/withdraw", json={"amount_cents": DEFAULT_WITHDRAW_CENTS}
    )
    second = await client.post(
        f"/api/runs/{run_id}/withdraw", json={"amount_cents": DEFAULT_WITHDRAW_CENTS}
    )

    assert first.status_code == 200
    assert second.status_code == 409
    state = (await client.get(f"/api/runs/{run_id}/state")).json()
    assert state["balance_cents"] == 0
    assert state["successful_withdrawals"] == 1
    assert state["overdraft"] is False


@pytest.mark.asyncio
async def test_concurrent_full_balance_withdrawals_overdraw_account(
    client: httpx.AsyncClient,
) -> None:
    run_id = await new_run(client)

    responses = await asyncio.gather(
        client.post(
            f"/api/runs/{run_id}/withdraw",
            json={"amount_cents": DEFAULT_WITHDRAW_CENTS},
            headers={"X-Request-ID": "left"},
        ),
        client.post(
            f"/api/runs/{run_id}/withdraw",
            json={"amount_cents": DEFAULT_WITHDRAW_CENTS},
            headers={"X-Request-ID": "right"},
        ),
    )

    assert [response.status_code for response in responses] == [200, 200]
    state = (await client.get(f"/api/runs/{run_id}/state")).json()
    assert state["balance_cents"] == -DEFAULT_WITHDRAW_CENTS
    assert state["successful_withdrawals"] == 2
    assert state["overdraft"] is True

    events = (await client.get(f"/api/runs/{run_id}/events")).json()["events"]
    assert [event["kind"] for event in events][1:3] == [
        "withdraw.checked",
        "withdraw.checked",
    ]