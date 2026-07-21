"""Recorded normal flows for each lab — the only lab-specific test data."""

from __future__ import annotations

from support.recorder import FlowRecorder


async def record_oneshot_flow(recorder: FlowRecorder) -> None:
    issued = await recorder.record("POST", "/perks/issue", json_body={"credit": 50})
    code = issued.response_body["perk"]["code"]
    await recorder.record("POST", f"/perks/{code}/claim", headers={"X-User-Id": "alice"})
    await recorder.record("GET", f"/perks/{code}")
    await recorder.record("GET", "/accounts/alice")


async def record_overdraw_flow(recorder: FlowRecorder) -> None:
    opened = await recorder.record(
        "POST", "/wallets/open", json_body={"holder": "alice", "opening": 100}
    )
    wallet_id = opened.response_body["wallet"]["id"]
    await recorder.record(
        "POST",
        f"/wallets/{wallet_id}/debit",
        headers={"X-User-Id": "alice"},
        json_body={"amount": 60},
    )
    await recorder.record("GET", f"/wallets/{wallet_id}")


async def record_crossuser_flow(recorder: FlowRecorder) -> None:
    minted = await recorder.record("POST", "/invites/mint", json_body={"bonus": 25})
    slug = minted.response_body["invite"]["slug"]
    await recorder.record(
        "POST", f"/invites/{slug}/accept", headers={"X-User-Id": "alice"}
    )
    await recorder.record("GET", f"/invites/{slug}")
    await recorder.record("GET", "/members/alice")


async def record_token_reuse_flow(recorder: FlowRecorder) -> None:
    begun = await recorder.record("POST", "/recoveries/begin", json_body={})
    ticket = begun.response_body["recovery"]["ticket"]
    await recorder.record(
        "POST", f"/recoveries/{ticket}/finish", json_body={"secret": "s3cure"}
    )
    await recorder.record("GET", f"/recoveries/{ticket}")


async def record_quota_flow(recorder: FlowRecorder) -> None:
    opened = await recorder.record("POST", "/drops/open", json_body={"seats": 1})
    sku = opened.response_body["drop"]["sku"]
    await recorder.record("POST", f"/drops/{sku}/buy", headers={"X-User-Id": "alice"})
    await recorder.record("GET", f"/drops/{sku}")
