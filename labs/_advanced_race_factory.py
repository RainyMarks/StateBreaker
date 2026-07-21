"""Shared FastAPI factory for advanced local race-condition labs.

The scenarios in this file are intentionally business flavored because they live
under ``labs/``. The core ``statebreaker`` package remains target agnostic.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

RACE_WINDOW_SECONDS = 0.03

State = dict[str, Any]
Context = dict[str, Any]
Reject = tuple[int, dict[str, Any]]


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    setup_path: str
    action_path: str
    query_path: str
    response_key: str
    id_prefix: str
    initial: Callable[[str, State], State]
    validate: Callable[[State, Context], Reject | None]
    apply: Callable[[State, Context], None]
    serialize: Callable[[str, State], State]


def _generated_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _identity(payload: State, field: str, prefix: str) -> str:
    value = payload.get(field) or payload.get("id")
    return str(value) if value else _generated_id(prefix)


def _reject(reason: str, *, status: int = 409, **extra: Any) -> Reject:
    return status, {"accepted": False, "reason": reason, **extra}


def _actor(context: Context) -> str:
    return str(context["body"].get("user") or context["x_user_id"])


def _units(context: Context, default: int = 1) -> int:
    return int(context["body"].get("units") or context["body"].get("amount") or default)


def _cart_initial(item_id: str, body: State) -> State:
    return {
        "id": item_id,
        "items": body.get("items", [{"sku": "kit", "quantity": 1}]),
        "promotions": {"bundle": {"limit": 1, "claims": []}},
        "discount_total": 0,
        "adjustments": [],
    }


def _cart_validate(state: State, context: Context) -> Reject | None:
    if len(state["promotions"]["bundle"]["claims"]) >= state["promotions"]["bundle"]["limit"]:
        return _reject("bundle_already_applied")
    return None


def _cart_apply(state: State, context: Context) -> None:
    state["promotions"]["bundle"]["claims"].append(_actor(context))
    state["discount_total"] += 15
    state["adjustments"].append({"kind": "bundle", "actor": _actor(context), "amount": 15})


def _cart_serialize(item_id: str, state: State) -> State:
    return {
        "id": item_id,
        "items": state["items"],
        "promotions": state["promotions"],
        "discount_total": state["discount_total"],
        "adjustment_count": len(state["adjustments"]),
        "adjustments": state["adjustments"],
    }


def _approval_initial(item_id: str, body: State) -> State:
    return {
        "id": item_id,
        "required_roles": body.get("required_roles", ["manager"]),
        "approvals": [],
        "status": "pending",
        "grant_count": 0,
    }


def _approval_validate(state: State, context: Context) -> Reject | None:
    role = str(context["body"].get("role", "manager"))
    if state["status"] != "pending":
        return _reject("request_closed")
    if any(approval["role"] == role for approval in state["approvals"]):
        return _reject("role_already_approved")
    return None


def _approval_apply(state: State, context: Context) -> None:
    role = str(context["body"].get("role", "manager"))
    state["approvals"].append({"role": role, "actor": _actor(context)})
    approved_roles = {approval["role"] for approval in state["approvals"]}
    if set(state["required_roles"]).issubset(approved_roles):
        state["status"] = "approved"
        state["grant_count"] += 1


def _approval_serialize(item_id: str, state: State) -> State:
    return {**state, "approval_count": len(state["approvals"])}


def _graph_initial(item_id: str, body: State) -> State:
    nodes = body.get("nodes", ["left", "right"])
    return {
        "id": item_id,
        "nodes": set(nodes),
        "edges": [],
        "edge_keys": set(),
        "degree": {node: 0 for node in nodes},
    }


def _graph_validate(state: State, context: Context) -> Reject | None:
    source = str(context["body"].get("from_node", "left"))
    target = str(context["body"].get("to_node", "right"))
    key = f"{source}->{target}"
    if key in state["edge_keys"]:
        return _reject("edge_exists")
    if state["degree"].get(source, 0) >= 1:
        return _reject("source_degree_exhausted")
    return None


def _graph_apply(state: State, context: Context) -> None:
    source = str(context["body"].get("from_node", "left"))
    target = str(context["body"].get("to_node", "right"))
    key = f"{source}->{target}"
    state["edge_keys"].add(key)
    state["degree"][source] = state["degree"].get(source, 0) + 1
    state["edges"].append({"from": source, "to": target, "key": key})


def _graph_serialize(item_id: str, state: State) -> State:
    return {
        "id": item_id,
        "nodes": sorted(state["nodes"]),
        "edges": state["edges"],
        "edge_count": len(state["edges"]),
        "degree": state["degree"],
    }


def _tree_initial(item_id: str, body: State) -> State:
    budget = int(body.get("root_budget", 10))
    return {
        "id": item_id,
        "tree": {
            "root": {
                "budget": budget,
                "used": 0,
                "children": {"team-a": {"used": 0, "allocations": []}},
            }
        },
    }


def _tree_validate(state: State, context: Context) -> Reject | None:
    root = state["tree"]["root"]
    amount = _units(context, 7)
    if root["used"] + amount > root["budget"]:
        return _reject("tree_budget_exhausted", used=root["used"], budget=root["budget"])
    return None


def _tree_apply(state: State, context: Context) -> None:
    root = state["tree"]["root"]
    amount = _units(context, 7)
    root["used"] += amount
    root["children"]["team-a"]["used"] += amount
    root["children"]["team-a"]["allocations"].append({"amount": amount, "actor": _actor(context)})


def _tree_serialize(item_id: str, state: State) -> State:
    root = state["tree"]["root"]
    return {
        "id": item_id,
        "tree": state["tree"],
        "remaining": root["budget"] - root["used"],
        "allocation_count": len(root["children"]["team-a"]["allocations"]),
    }


def _ledger_initial(item_id: str, body: State) -> State:
    return {
        "id": item_id,
        "accounts": {"cash": int(body.get("cash", 100)), "escrow": 0},
        "events": [{"type": "opened", "amount": int(body.get("cash", 100))}],
    }


def _ledger_validate(state: State, context: Context) -> Reject | None:
    amount = _units(context, 60)
    if state["accounts"]["cash"] < amount:
        return _reject("insufficient_cash", balance=state["accounts"]["cash"])
    return None


def _ledger_apply(state: State, context: Context) -> None:
    amount = _units(context, 60)
    state["accounts"]["cash"] -= amount
    state["accounts"]["escrow"] += amount
    state["events"].append({"type": "transfer", "amount": amount, "actor": _actor(context)})


def _ledger_serialize(item_id: str, state: State) -> State:
    return {**state, "event_count": len(state["events"])}


def _hold_initial(item_id: str, body: State) -> State:
    slot = str(body.get("slot", "2026-07-21T09"))
    return {"id": item_id, "slots": {slot: {"capacity": 1, "holds": []}}}


def _hold_validate(state: State, context: Context) -> Reject | None:
    slot = str(context["body"].get("slot", "2026-07-21T09"))
    record = state["slots"].setdefault(slot, {"capacity": 1, "holds": []})
    if len(record["holds"]) >= record["capacity"]:
        return _reject("slot_full")
    return None


def _hold_apply(state: State, context: Context) -> None:
    slot = str(context["body"].get("slot", "2026-07-21T09"))
    record = state["slots"].setdefault(slot, {"capacity": 1, "holds": []})
    record["holds"].append({"user": _actor(context), "hold_id": uuid.uuid4().hex[:8]})


def _hold_serialize(item_id: str, state: State) -> State:
    holds = sum(len(slot["holds"]) for slot in state["slots"].values())
    return {**state, "hold_count": holds}


def _workflow_initial(item_id: str, body: State) -> State:
    return {
        "id": item_id,
        "steps": [
            {"name": "verify-email", "status": "pending"},
            {"name": "accept-policy", "status": "pending"},
        ],
        "completed": set(),
        "reward_count": 0,
    }


def _workflow_validate(state: State, context: Context) -> Reject | None:
    if "verify-email" in state["completed"]:
        return _reject("step_already_completed")
    return None


def _workflow_apply(state: State, context: Context) -> None:
    state["completed"].add("verify-email")
    state["reward_count"] += 1
    for step in state["steps"]:
        if step["name"] == "verify-email":
            step["status"] = "completed"


def _workflow_serialize(item_id: str, state: State) -> State:
    return {**state, "completed": sorted(state["completed"])}


def _quota_initial(item_id: str, body: State) -> State:
    tenant = str(body.get("tenant", "tenant-a"))
    window = str(body.get("window", "morning"))
    return {"id": item_id, "limit": int(body.get("limit", 5)), "usage": {tenant: {window: []}}}


def _quota_bucket(state: State, context: Context) -> list[State]:
    tenant = str(context["x_tenant_id"])
    window = str(context["window"] or context["body"].get("window") or "morning")
    return state["usage"].setdefault(tenant, {}).setdefault(window, [])


def _quota_validate(state: State, context: Context) -> Reject | None:
    bucket = _quota_bucket(state, context)
    if sum(item["units"] for item in bucket) + _units(context, 3) > state["limit"]:
        return _reject("tenant_window_limit")
    return None


def _quota_apply(state: State, context: Context) -> None:
    _quota_bucket(state, context).append({"user": _actor(context), "units": _units(context, 3)})


def _quota_serialize(item_id: str, state: State) -> State:
    total = sum(
        item["units"]
        for tenant in state["usage"].values()
        for bucket in tenant.values()
        for item in bucket
    )
    return {**state, "total_units": total}


def _code_initial(item_id: str, body: State) -> State:
    return {
        "id": item_id,
        "status": "unused",
        "credits": int(body.get("credits", 25)),
        "claims": [],
    }


def _code_validate(state: State, context: Context) -> Reject | None:
    if state["status"] != "unused":
        return _reject("short_code_used")
    return None


def _code_apply(state: State, context: Context) -> None:
    state["status"] = "used"
    state["claims"].append({"user": _actor(context), "credits": state["credits"]})


def _code_serialize(item_id: str, state: State) -> State:
    return {**state, "claim_count": len(state["claims"])}


def _lock_initial(item_id: str, body: State) -> State:
    return {"id": item_id, "locks": {}, "acquire_count": 0}


def _lock_key(context: Context) -> str:
    resource = str(context["body"].get("resource", "camera"))
    region = str(context["region"] or context["body"].get("region") or "eu")
    return f"{resource}:{region}"


def _lock_validate(state: State, context: Context) -> Reject | None:
    if _lock_key(context) in state["locks"]:
        return _reject("composite_lock_held")
    return None


def _lock_apply(state: State, context: Context) -> None:
    key = _lock_key(context)
    state["locks"][key] = {"owner": _actor(context)}
    state["acquire_count"] += 1


def _lock_serialize(item_id: str, state: State) -> State:
    return {**state, "held_keys": sorted(state["locks"])}


def _dedup_initial(item_id: str, body: State) -> State:
    return {"id": item_id, "seen": set(), "rows": [], "accepted": 0}


def _dedup_key(context: Context) -> str:
    return str(context["idempotency_key"] or context["body"].get("row_id") or "row-1")


def _dedup_validate(state: State, context: Context) -> Reject | None:
    if _dedup_key(context) in state["seen"]:
        return _reject("duplicate_row")
    return None


def _dedup_apply(state: State, context: Context) -> None:
    key = _dedup_key(context)
    state["seen"].add(key)
    state["rows"].append({"row_id": key, "payload": context["body"].get("payload", "alpha")})
    state["accepted"] += 1


def _dedup_serialize(item_id: str, state: State) -> State:
    return {**state, "seen": sorted(state["seen"]), "row_count": len(state["rows"])}


def _machine_initial(item_id: str, body: State) -> State:
    return {"id": item_id, "status": "packed", "transition_count": 0, "history": ["packed"]}


def _machine_validate(state: State, context: Context) -> Reject | None:
    if state["status"] != "packed":
        return _reject("invalid_transition")
    return None


def _machine_apply(state: State, context: Context) -> None:
    state["status"] = "dispatched"
    state["transition_count"] += 1
    state["history"].append("dispatched")


def _machine_serialize(item_id: str, state: State) -> State:
    return state


def _window_initial(item_id: str, body: State) -> State:
    bucket = str(body.get("bucket", "2026-07-21T01:00"))
    return {"id": item_id, "limit": int(body.get("limit", 1)), "windows": {bucket: []}}


def _window_bucket(state: State, context: Context) -> list[State]:
    bucket = str(context["bucket"] or context["body"].get("bucket") or "2026-07-21T01:00")
    return state["windows"].setdefault(bucket, [])


def _window_validate(state: State, context: Context) -> Reject | None:
    if len(_window_bucket(state, context)) >= state["limit"]:
        return _reject("window_limit_exhausted")
    return None


def _window_apply(state: State, context: Context) -> None:
    _window_bucket(state, context).append({"actor": _actor(context), "units": _units(context)})


def _window_serialize(item_id: str, state: State) -> State:
    total = sum(len(events) for events in state["windows"].values())
    return {**state, "event_count": total}


def _settlement_initial(item_id: str, body: State) -> State:
    amount = int(body.get("amount", 40))
    return {
        "id": item_id,
        "batches": {"batch-a": {"items": [amount], "status": "open"}},
        "settled_total": 0,
        "close_count": 0,
    }


def _settlement_validate(state: State, context: Context) -> Reject | None:
    batch_id = str(context["body"].get("batch_id", "batch-a"))
    if state["batches"][batch_id]["status"] != "open":
        return _reject("batch_closed")
    return None


def _settlement_apply(state: State, context: Context) -> None:
    batch_id = str(context["body"].get("batch_id", "batch-a"))
    batch = state["batches"][batch_id]
    batch["status"] = "closed"
    state["settled_total"] += sum(batch["items"])
    state["close_count"] += 1


def _settlement_serialize(item_id: str, state: State) -> State:
    return state


def _stock_initial(item_id: str, body: State) -> State:
    return {
        "id": item_id,
        "shards": [{"id": "0", "quantity": int(body.get("quantity", 5))}],
        "reservations": [],
    }


def _stock_shard(state: State, context: Context) -> State:
    shard_id = str(context["shard"] or context["body"].get("shard") or "0")
    return next(shard for shard in state["shards"] if shard["id"] == shard_id)


def _stock_validate(state: State, context: Context) -> Reject | None:
    shard = _stock_shard(state, context)
    quantity = int(context["body"].get("quantity", 3))
    if shard["quantity"] < quantity:
        return _reject("shard_stock_exhausted", remaining=shard["quantity"])
    return None


def _stock_apply(state: State, context: Context) -> None:
    shard = _stock_shard(state, context)
    quantity = int(context["body"].get("quantity", 3))
    shard["quantity"] -= quantity
    state["reservations"].append(
        {"shard": shard["id"], "quantity": quantity, "user": _actor(context)}
    )


def _stock_serialize(item_id: str, state: State) -> State:
    return {**state, "reserved_count": len(state["reservations"])}


def _profile_initial(item_id: str, body: State) -> State:
    return {
        "id": item_id,
        "version": 1,
        "email": body.get("email", "a@example.test"),
        "history": [],
    }


def _profile_validate(state: State, context: Context) -> Reject | None:
    expected = int(context["body"].get("expected_version", 1))
    if expected != state["version"]:
        return _reject("version_conflict", current=state["version"])
    return None


def _profile_apply(state: State, context: Context) -> None:
    state["email"] = context["body"].get("email", "new@example.test")
    state["version"] += 1
    state["history"].append({"version": state["version"], "actor": _actor(context)})


def _profile_serialize(item_id: str, state: State) -> State:
    return {**state, "change_count": len(state["history"])}


def _project_initial(item_id: str, body: State) -> State:
    return {"id": item_id, "quota": int(body.get("quota", 5)), "tasks": {}, "events": []}


def _project_validate(state: State, context: Context) -> Reject | None:
    task_id = str(context["body"].get("task_id", "task-a"))
    cost = int(context["body"].get("cost", 3))
    if task_id in state["tasks"]:
        return _reject("task_exists")
    if state["quota"] < cost:
        return _reject("project_quota_exhausted", quota=state["quota"])
    return None


def _project_apply(state: State, context: Context) -> None:
    task_id = str(context["body"].get("task_id", "task-a"))
    cost = int(context["body"].get("cost", 3))
    state["tasks"][task_id] = {"cost": cost, "owner": _actor(context)}
    state["quota"] -= cost
    state["events"].append({"type": "task-created", "task_id": task_id, "cost": cost})


def _project_serialize(item_id: str, state: State) -> State:
    return {**state, "task_count": len(state["tasks"]), "event_count": len(state["events"])}


def _waitlist_initial(item_id: str, body: State) -> State:
    return {
        "id": item_id,
        "capacity": int(body.get("capacity", 1)),
        "queue": list(body.get("queue", ["alice"])),
        "seats": [],
        "promotion_count": 0,
    }


def _waitlist_validate(state: State, context: Context) -> Reject | None:
    candidate = str(context["body"].get("candidate", "alice"))
    if len(state["seats"]) >= state["capacity"]:
        return _reject("waitlist_capacity_full")
    if candidate not in state["queue"]:
        return _reject("candidate_not_waiting")
    return None


def _waitlist_apply(state: State, context: Context) -> None:
    candidate = str(context["body"].get("candidate", "alice"))
    if candidate in state["queue"]:
        state["queue"].remove(candidate)
    state["seats"].append({"candidate": candidate, "by": _actor(context)})
    state["promotion_count"] += 1


def _waitlist_serialize(item_id: str, state: State) -> State:
    return state


def _club_initial(item_id: str, body: State) -> State:
    pending = set(body.get("pending", ["alice"]))
    return {"id": item_id, "pending": pending, "members": set(), "grants": [], "credits": 0}


def _club_validate(state: State, context: Context) -> Reject | None:
    user = _actor(context)
    if user not in state["pending"]:
        return _reject("not_invited")
    if user in state["members"]:
        return _reject("already_member")
    return None


def _club_apply(state: State, context: Context) -> None:
    user = _actor(context)
    state["members"].add(user)
    state["pending"].discard(user)
    state["grants"].append({"user": user, "credit": 10})
    state["credits"] += 10


def _club_serialize(item_id: str, state: State) -> State:
    return {
        "id": item_id,
        "pending": sorted(state["pending"]),
        "members": sorted(state["members"]),
        "grants": state["grants"],
        "credits": state["credits"],
        "grant_count": len(state["grants"]),
    }


def _claim_initial(item_id: str, body: State) -> State:
    return {"id": item_id, "claimed": set(), "claims": [], "awarded": 0}


def _claim_validate(state: State, context: Context) -> Reject | None:
    priority = str(context["priority"] or context["body"].get("priority") or "gold")
    if priority in state["claimed"]:
        return _reject("priority_claimed")
    return None


def _claim_apply(state: State, context: Context) -> None:
    priority = str(context["priority"] or context["body"].get("priority") or "gold")
    state["claimed"].add(priority)
    state["claims"].append({"priority": priority, "user": _actor(context)})
    state["awarded"] += 1


def _claim_serialize(item_id: str, state: State) -> State:
    return {**state, "claimed": sorted(state["claimed"]), "claim_count": len(state["claims"])}


SCENARIOS: dict[str, Scenario] = {
    "cart-bundle": Scenario(
        "cart-bundle",
        "lab-advanced-cart-bundle",
        "/carts",
        "/carts/{item_id}/bundles/apply",
        "/carts/{item_id}",
        "cart",
        "cart",
        _cart_initial,
        _cart_validate,
        _cart_apply,
        _cart_serialize,
    ),
    "approval-chain": Scenario(
        "approval-chain",
        "lab-advanced-approval-chain",
        "/requests",
        "/requests/{item_id}/approve",
        "/requests/{item_id}",
        "request",
        "req",
        _approval_initial,
        _approval_validate,
        _approval_apply,
        _approval_serialize,
    ),
    "graph-edge": Scenario(
        "graph-edge",
        "lab-advanced-graph-edge",
        "/graphs",
        "/graphs/{item_id}/edges/link",
        "/graphs/{item_id}",
        "graph",
        "graph",
        _graph_initial,
        _graph_validate,
        _graph_apply,
        _graph_serialize,
    ),
    "tree-quota": Scenario(
        "tree-quota",
        "lab-advanced-tree-quota",
        "/orgs",
        "/orgs/{item_id}/nodes/team-a/allocate",
        "/orgs/{item_id}",
        "org",
        "org",
        _tree_initial,
        _tree_validate,
        _tree_apply,
        _tree_serialize,
    ),
    "ledger-transfer": Scenario(
        "ledger-transfer",
        "lab-advanced-ledger-transfer",
        "/ledgers",
        "/ledgers/{item_id}/transfers",
        "/ledgers/{item_id}",
        "ledger",
        "ledger",
        _ledger_initial,
        _ledger_validate,
        _ledger_apply,
        _ledger_serialize,
    ),
    "reservation-hold": Scenario(
        "reservation-hold",
        "lab-advanced-reservation-hold",
        "/rooms",
        "/rooms/{item_id}/holds",
        "/rooms/{item_id}",
        "room",
        "room",
        _hold_initial,
        _hold_validate,
        _hold_apply,
        _hold_serialize,
    ),
    "onboarding-workflow": Scenario(
        "onboarding-workflow",
        "lab-advanced-onboarding-workflow",
        "/workflows",
        "/workflows/{item_id}/steps/verify-email/complete",
        "/workflows/{item_id}",
        "workflow",
        "flow",
        _workflow_initial,
        _workflow_validate,
        _workflow_apply,
        _workflow_serialize,
    ),
    "header-body-quota": Scenario(
        "header-body-quota",
        "lab-advanced-header-body-quota",
        "/quotas",
        "/quotas/{item_id}/consume",
        "/quotas/{item_id}",
        "quota",
        "quota",
        _quota_initial,
        _quota_validate,
        _quota_apply,
        _quota_serialize,
    ),
    "shortcode-redeem": Scenario(
        "shortcode-redeem",
        "lab-advanced-shortcode-redeem",
        "/codes",
        "/codes/{item_id}/redeem",
        "/codes/{item_id}",
        "code",
        "AB",
        _code_initial,
        _code_validate,
        _code_apply,
        _code_serialize,
    ),
    "composite-lock": Scenario(
        "composite-lock",
        "lab-advanced-composite-lock",
        "/locks",
        "/locks/{item_id}/acquire",
        "/locks/{item_id}",
        "lockspace",
        "lock",
        _lock_initial,
        _lock_validate,
        _lock_apply,
        _lock_serialize,
    ),
    "dedup-batch": Scenario(
        "dedup-batch",
        "lab-advanced-dedup-batch",
        "/imports",
        "/imports/{item_id}/rows",
        "/imports/{item_id}",
        "import_job",
        "imp",
        _dedup_initial,
        _dedup_validate,
        _dedup_apply,
        _dedup_serialize,
    ),
    "state-machine": Scenario(
        "state-machine",
        "lab-advanced-state-machine",
        "/shipments",
        "/shipments/{item_id}/dispatch",
        "/shipments/{item_id}",
        "shipment",
        "ship",
        _machine_initial,
        _machine_validate,
        _machine_apply,
        _machine_serialize,
    ),
    "window-limit": Scenario(
        "window-limit",
        "lab-advanced-window-limit",
        "/windows",
        "/windows/{item_id}/events",
        "/windows/{item_id}",
        "window",
        "win",
        _window_initial,
        _window_validate,
        _window_apply,
        _window_serialize,
    ),
    "batch-settlement": Scenario(
        "batch-settlement",
        "lab-advanced-batch-settlement",
        "/settlements",
        "/settlements/{item_id}/close",
        "/settlements/{item_id}",
        "settlement",
        "set",
        _settlement_initial,
        _settlement_validate,
        _settlement_apply,
        _settlement_serialize,
    ),
    "sharded-stock": Scenario(
        "sharded-stock",
        "lab-advanced-sharded-stock",
        "/stock",
        "/stock/{item_id}/reserve",
        "/stock/{item_id}",
        "stock",
        "stock",
        _stock_initial,
        _stock_validate,
        _stock_apply,
        _stock_serialize,
    ),
    "cas-profile": Scenario(
        "cas-profile",
        "lab-advanced-cas-profile",
        "/profiles",
        "/profiles/{item_id}/update",
        "/profiles/{item_id}",
        "profile",
        "prof",
        _profile_initial,
        _profile_validate,
        _profile_apply,
        _profile_serialize,
    ),
    "linked-resource": Scenario(
        "linked-resource",
        "lab-advanced-linked-resource",
        "/projects",
        "/projects/{item_id}/tasks",
        "/projects/{item_id}",
        "project",
        "proj",
        _project_initial,
        _project_validate,
        _project_apply,
        _project_serialize,
    ),
    "waitlist-queue": Scenario(
        "waitlist-queue",
        "lab-advanced-waitlist-queue",
        "/waitlists",
        "/waitlists/{item_id}/promote",
        "/waitlists/{item_id}",
        "waitlist",
        "wait",
        _waitlist_initial,
        _waitlist_validate,
        _waitlist_apply,
        _waitlist_serialize,
    ),
    "set-membership": Scenario(
        "set-membership",
        "lab-advanced-set-membership",
        "/clubs",
        "/clubs/{item_id}/join",
        "/clubs/{item_id}",
        "club",
        "club",
        _club_initial,
        _club_validate,
        _club_apply,
        _club_serialize,
    ),
    "priority-claim": Scenario(
        "priority-claim",
        "lab-advanced-priority-claim",
        "/claims",
        "/claims/{item_id}/claim",
        "/claims/{item_id}",
        "claim_pool",
        "claim",
        _claim_initial,
        _claim_validate,
        _claim_apply,
        _claim_serialize,
    ),
}


def create_advanced_app(key: str) -> FastAPI:
    scenario = SCENARIOS[key]
    app = FastAPI(title=scenario.title)
    records: dict[str, State] = {}
    write_lock = asyncio.Lock()

    @app.post(scenario.setup_path, status_code=201, response_model=None)
    async def setup_resource(body: State) -> dict[str, Any] | JSONResponse:
        item_id = _identity(body, f"{scenario.key.split('-')[0]}_id", scenario.id_prefix)
        async with write_lock:
            if item_id in records:
                return JSONResponse(status_code=409, content={"detail": "duplicate resource"})
            records[item_id] = scenario.initial(item_id, body)
            snapshot = scenario.serialize(item_id, records[item_id])
        return {scenario.response_key: snapshot}

    @app.post(scenario.action_path, response_model=None)
    async def mutate_resource(
        item_id: str,
        body: State | None = None,
        x_user_id: Annotated[str, Header()] = "alice",
        x_tenant_id: Annotated[str, Header()] = "tenant-a",
        idempotency_key: Annotated[str | None, Header()] = None,
        window: str | None = None,
        region: str | None = None,
        shard: str | None = None,
        bucket: str | None = None,
        priority: str | None = None,
    ) -> dict[str, Any] | JSONResponse:
        state = records.get(item_id)
        if state is None:
            return JSONResponse(status_code=404, content={"detail": "unknown resource"})
        context = {
            "body": body or {},
            "x_user_id": x_user_id,
            "x_tenant_id": x_tenant_id,
            "idempotency_key": idempotency_key,
            "window": window,
            "region": region,
            "shard": shard,
            "bucket": bucket,
            "priority": priority,
        }
        rejected = scenario.validate(state, context)
        if rejected is not None:
            status, content = rejected
            return JSONResponse(status_code=status, content=content)

        await asyncio.sleep(RACE_WINDOW_SECONDS)

        async with write_lock:
            scenario.apply(state, context)
            snapshot = scenario.serialize(item_id, state)
        return {"accepted": True, scenario.response_key: snapshot}

    @app.get(scenario.query_path, response_model=None)
    async def get_resource(item_id: str) -> dict[str, Any] | JSONResponse:
        state = records.get(item_id)
        if state is None:
            return JSONResponse(status_code=404, content={"detail": "unknown resource"})
        return {scenario.response_key: scenario.serialize(item_id, state)}

    @app.post("/__test__/reset")
    async def reset_state() -> dict[str, bool]:
        async with write_lock:
            records.clear()
        return {"reset": True}

    return app
