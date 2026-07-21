"""Black-box normal-flow recorders for advanced local labs.

The helpers below know only public HTTP traffic that a normal user would send:
create/setup, status probe, one mutating action, and a post-action status probe.
They do not import lab modules, inspect private state, or construct target
objects directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from support.recorder import FlowRecorder


@dataclass(frozen=True)
class BlackBoxFlowSpec:
    lab: str
    setup_path: str
    setup_body: dict[str, object]
    response_key: str
    probe_path: str
    action_path: str
    action_body: dict[str, object]
    action_headers: dict[str, str]


ADVANCED_FLOW_SPECS: tuple[BlackBoxFlowSpec, ...] = (
    BlackBoxFlowSpec(
        "lab-advanced-cart-bundle",
        "/carts",
        {"items": [{"sku": "kit", "quantity": 1}]},
        "cart",
        "/carts/{id}",
        "/carts/{id}/bundles/apply",
        {"user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-approval-chain",
        "/requests",
        {"required_roles": ["manager"]},
        "request",
        "/requests/{id}",
        "/requests/{id}/approve",
        {"role": "manager", "user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-graph-edge",
        "/graphs",
        {"nodes": ["left", "right"]},
        "graph",
        "/graphs/{id}",
        "/graphs/{id}/edges/link",
        {"from_node": "left", "to_node": "right", "user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-tree-quota",
        "/orgs",
        {"root_budget": 10},
        "org",
        "/orgs/{id}",
        "/orgs/{id}/nodes/team-a/allocate",
        {"units": 7, "user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-ledger-transfer",
        "/ledgers",
        {"cash": 100},
        "ledger",
        "/ledgers/{id}",
        "/ledgers/{id}/transfers",
        {"amount": 60, "user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-reservation-hold",
        "/rooms",
        {"slot": "2026-07-21T09"},
        "room",
        "/rooms/{id}",
        "/rooms/{id}/holds",
        {"slot": "2026-07-21T09", "user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-onboarding-workflow",
        "/workflows",
        {},
        "workflow",
        "/workflows/{id}",
        "/workflows/{id}/steps/verify-email/complete",
        {"user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-header-body-quota",
        "/quotas",
        {"tenant": "tenant-a", "window": "morning", "limit": 5},
        "quota",
        "/quotas/{id}",
        "/quotas/{id}/consume?window=morning",
        {"units": 3, "user": "alice"},
        {"X-User-Id": "alice", "X-Tenant-Id": "tenant-a"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-shortcode-redeem",
        "/codes",
        {"credits": 25},
        "code",
        "/codes/{id}",
        "/codes/{id}/redeem",
        {"user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-composite-lock",
        "/locks",
        {},
        "lockspace",
        "/locks/{id}",
        "/locks/{id}/acquire?region=eu",
        {"resource": "camera", "user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-dedup-batch",
        "/imports",
        {},
        "import_job",
        "/imports/{id}",
        "/imports/{id}/rows",
        {"row_id": "row-1", "payload": "alpha"},
        {"X-User-Id": "alice", "Idempotency-Key": "row-1"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-state-machine",
        "/shipments",
        {},
        "shipment",
        "/shipments/{id}",
        "/shipments/{id}/dispatch",
        {"user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-window-limit",
        "/windows",
        {"bucket": "2026-07-21T01:00", "limit": 1},
        "window",
        "/windows/{id}",
        "/windows/{id}/events?bucket=2026-07-21T01:00",
        {"units": 1, "user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-batch-settlement",
        "/settlements",
        {"amount": 40},
        "settlement",
        "/settlements/{id}",
        "/settlements/{id}/close",
        {"batch_id": "batch-a", "user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-sharded-stock",
        "/stock",
        {"quantity": 5},
        "stock",
        "/stock/{id}",
        "/stock/{id}/reserve?shard=0",
        {"quantity": 3, "user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-cas-profile",
        "/profiles",
        {"email": "a@example.test"},
        "profile",
        "/profiles/{id}",
        "/profiles/{id}/update",
        {"expected_version": 1, "email": "new@example.test", "user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-linked-resource",
        "/projects",
        {"quota": 5},
        "project",
        "/projects/{id}",
        "/projects/{id}/tasks",
        {"task_id": "task-a", "cost": 3, "user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-waitlist-queue",
        "/waitlists",
        {"capacity": 1, "queue": ["alice"]},
        "waitlist",
        "/waitlists/{id}",
        "/waitlists/{id}/promote",
        {"candidate": "alice", "user": "operator"},
        {"X-User-Id": "operator"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-set-membership",
        "/clubs",
        {"pending": ["alice"]},
        "club",
        "/clubs/{id}",
        "/clubs/{id}/join",
        {"user": "alice"},
        {"X-User-Id": "alice"},
    ),
    BlackBoxFlowSpec(
        "lab-advanced-priority-claim",
        "/claims",
        {},
        "claim_pool",
        "/claims/{id}",
        "/claims/{id}/claim?priority=gold",
        {"user": "alice"},
        {"X-User-Id": "alice"},
    ),
)


ADVANCED_FLOW_BY_LAB = {spec.lab: spec for spec in ADVANCED_FLOW_SPECS}


async def record_advanced_blackbox_flow(recorder: FlowRecorder, spec: BlackBoxFlowSpec) -> None:
    created = await recorder.record(
        "POST",
        spec.setup_path,
        headers={"X-User-Id": "alice"},
        json_body=spec.setup_body,
    )
    resource_id = str(created.response_body[spec.response_key]["id"])
    await recorder.record("GET", spec.probe_path.format(id=resource_id))
    await recorder.record(
        "POST",
        spec.action_path.format(id=resource_id),
        headers=spec.action_headers,
        json_body=spec.action_body,
    )
    await recorder.record("GET", spec.probe_path.format(id=resource_id))
