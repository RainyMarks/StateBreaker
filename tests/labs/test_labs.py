"""Lab behavior contracts: normal flow, sequential control, concurrent anomaly.

Each lab must show: one effect sequentially, doubled effect concurrently —
that is what the scanner later has to rediscover on its own.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

import httpx

LABS_ROOT = Path(__file__).resolve().parents[2] / "labs"


def _load_app_module(lab_dir: str) -> Any:
    spec = importlib.util.spec_from_file_location(
        lab_dir.replace("-", "_"), LABS_ROOT / lab_dir / "app.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://lab.local"
    )


# --- lab-oneshot-redemption -------------------------------------------------


async def test_oneshot_normal_flow() -> None:
    module = _load_app_module("lab-oneshot-redemption")
    async with _client(module.create_app()) as client:
        issued = await client.post("/perks/issue", json={"credit": 50})
        assert issued.status_code == 201
        code = issued.json()["perk"]["code"]

        claimed = await client.post(f"/perks/{code}/claim", headers={"X-User-Id": "alice"})
        assert claimed.status_code == 200
        assert claimed.json()["claimed"] is True

        perk = await client.get(f"/perks/{code}")
        assert perk.json()["perk"]["status"] == "spent"

        account = await client.get("/accounts/alice")
        assert account.json()["account"]["credit_total"] == 50
        assert account.json()["account"]["claim_count"] == 1


async def test_oneshot_sequential_control_single_effect() -> None:
    module = _load_app_module("lab-oneshot-redemption")
    async with _client(module.create_app()) as client:
        code = (await client.post("/perks/issue", json={"credit": 50})).json()["perk"]["code"]
        first = await client.post(f"/perks/{code}/claim", headers={"X-User-Id": "alice"})
        second = await client.post(f"/perks/{code}/claim", headers={"X-User-Id": "alice"})
        assert first.status_code == 200
        assert second.status_code == 409
        account = (await client.get("/accounts/alice")).json()["account"]
        assert account["credit_total"] == 50
        assert account["claim_count"] == 1


async def test_oneshot_concurrent_double_effect() -> None:
    module = _load_app_module("lab-oneshot-redemption")
    async with _client(module.create_app()) as client:
        code = (await client.post("/perks/issue", json={"credit": 50})).json()["perk"]["code"]
        first, second = await asyncio.gather(
            client.post(f"/perks/{code}/claim", headers={"X-User-Id": "alice"}),
            client.post(f"/perks/{code}/claim", headers={"X-User-Id": "alice"}),
        )
        assert first.status_code == 200
        assert second.status_code == 200
        account = (await client.get("/accounts/alice")).json()["account"]
        assert account["credit_total"] == 100  # anomaly: double credit
        assert account["claim_count"] == 2


async def test_oneshot_reset_and_unknown_code() -> None:
    module = _load_app_module("lab-oneshot-redemption")
    async with _client(module.create_app()) as client:
        await client.post("/perks/issue", json={"code": "FIXED1", "credit": 10})
        assert (await client.post("/perks/issue", json={"code": "FIXED1"})).status_code == 409
        await client.post("/__test__/reset")
        assert (await client.get("/perks/FIXED1")).status_code == 404
        assert (await client.post("/perks/NOPE/claim")).status_code == 404


# --- lab-overdraw -----------------------------------------------------------


async def test_overdraw_normal_flow() -> None:
    module = _load_app_module("lab-overdraw")
    async with _client(module.create_app()) as client:
        opened = await client.post("/wallets/open", json={"holder": "alice", "opening": 100})
        wallet_id = opened.json()["wallet"]["id"]

        debited = await client.post(
            f"/wallets/{wallet_id}/debit",
            json={"amount": 60},
            headers={"X-User-Id": "alice"},
        )
        assert debited.status_code == 200
        assert debited.json()["balance"] == 40

        wallet = (await client.get(f"/wallets/{wallet_id}")).json()["wallet"]
        assert wallet["balance"] == 40


async def test_overdraw_sequential_control_rejected() -> None:
    module = _load_app_module("lab-overdraw")
    async with _client(module.create_app()) as client:
        wallet_id = (
            await client.post("/wallets/open", json={"holder": "alice", "opening": 100})
        ).json()["wallet"]["id"]
        first = await client.post(
            f"/wallets/{wallet_id}/debit", json={"amount": 60}, headers={"X-User-Id": "alice"}
        )
        second = await client.post(
            f"/wallets/{wallet_id}/debit", json={"amount": 60}, headers={"X-User-Id": "alice"}
        )
        assert first.status_code == 200
        assert second.status_code == 422
        wallet = (await client.get(f"/wallets/{wallet_id}")).json()["wallet"]
        assert wallet["balance"] == 40


async def test_overdraw_concurrent_negative_balance() -> None:
    module = _load_app_module("lab-overdraw")
    async with _client(module.create_app()) as client:
        wallet_id = (
            await client.post("/wallets/open", json={"holder": "alice", "opening": 100})
        ).json()["wallet"]["id"]
        first, second = await asyncio.gather(
            client.post(
                f"/wallets/{wallet_id}/debit",
                json={"amount": 60},
                headers={"X-User-Id": "alice"},
            ),
            client.post(
                f"/wallets/{wallet_id}/debit",
                json={"amount": 60},
                headers={"X-User-Id": "alice"},
            ),
        )
        assert first.status_code == 200
        assert second.status_code == 200
        wallet = (await client.get(f"/wallets/{wallet_id}")).json()["wallet"]
        assert wallet["balance"] == -20  # anomaly: below zero


async def test_overdraw_forbidden_and_reset() -> None:
    module = _load_app_module("lab-overdraw")
    async with _client(module.create_app()) as client:
        wallet_id = (
            await client.post("/wallets/open", json={"holder": "alice", "opening": 100})
        ).json()["wallet"]["id"]
        forbidden = await client.post(
            f"/wallets/{wallet_id}/debit", json={"amount": 10}, headers={"X-User-Id": "bob"}
        )
        assert forbidden.status_code == 403
        await client.post("/__test__/reset")
        assert (await client.get(f"/wallets/{wallet_id}")).status_code == 404
