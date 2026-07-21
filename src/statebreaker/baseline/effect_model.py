"""Response fingerprints and repeat-behavior classification."""

from __future__ import annotations

import hashlib
from typing import Any

from statebreaker.intelligence.jsondiff import flatten_state
from statebreaker.models.state import FieldChange, RepeatBehavior, ResponseSignature


def body_shape(body: Any) -> str:
    """A stable fingerprint of a response body's structure (paths, not values)."""
    paths = sorted(flatten_state(body))
    return hashlib.sha1("|".join(paths).encode()).hexdigest()[:12]


def response_signature(status: int, body: Any) -> ResponseSignature:
    shape = body_shape(body)
    return ResponseSignature(
        status=status,
        body_shape=shape,
        semantic_class=f"{status}:{shape}",
    )


def same_semantic_class(first: ResponseSignature | None, second: ResponseSignature | None) -> bool:
    if first is None or second is None:
        return False
    return first.semantic_class == second.semantic_class


def classify_repeat(
    *,
    first_changes: list[FieldChange],
    second_changes: list[FieldChange],
    first_signature: ResponseSignature | None,
    second_signature: ResponseSignature | None,
) -> RepeatBehavior:
    """Classify what a sequential A -> A experiment says about an action.

    - ``once``: the second execution changes nothing and the service answers
      differently (a refusal class) — one-shot or limit-reached semantics.
    - ``idempotent``: the second execution changes nothing and gets the same
      response class.
    - ``limited``: the second execution still changes state (repeatable with
      bounded effects).
    - ``unstable``: anything inconsistent.
    """
    if not first_changes and not second_changes:
        same = same_semantic_class(first_signature, second_signature)
        return "idempotent" if same else "unstable"
    if not second_changes:
        if same_semantic_class(first_signature, second_signature):
            return "idempotent"
        return "once"
    first_paths = {change.path for change in first_changes}
    second_paths = {change.path for change in second_changes}
    if first_paths == second_paths:
        return "limited"
    return "unstable"
