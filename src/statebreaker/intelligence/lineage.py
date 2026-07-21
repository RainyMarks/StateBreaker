"""Producer→consumer value lineage across a captured trace.

A value observed in a response that reappears in a later request is a
candidate dependency. Matching covers exact equality, type coercion,
URL-encoding, and base64 wrapping — never business names.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, quote, urlparse

from statebreaker.intelligence.value_types import classify_value, is_dynamic_value
from statebreaker.models.capture import HttpExchange
from statebreaker.models.workflow import VariableBinding

_IGNORED_REQUEST_HEADERS = {
    "content-type",
    "content-length",
    "accept",
    "accept-encoding",
    "accept-language",
    "user-agent",
    "host",
    "connection",
    "origin",
    "referer",
    "cache-control",
    "pragma",
    "cookie",
}

_WORD_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _jsonpath_segment(key: str) -> str:
    if _WORD_KEY.match(key):
        return f".{key}"
    escaped = key.replace("\\", "\\\\").replace("'", "\\'")
    return f"['{escaped}']"


def iter_json_leaves(value: Any, path: str = "$") -> Iterator[tuple[str, Any]]:
    """Yield ``(jsonpath, scalar)`` for every leaf of a JSON-like value."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from iter_json_leaves(item, path + _jsonpath_segment(str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_json_leaves(item, f"{path}[{index}]")
    else:
        yield path, value


def _identifier_like(text: str) -> bool:
    """Short machine-ish strings: worth tracking even when not uuid/token."""
    if not (3 <= len(text) <= 128) or any(c.isspace() for c in text):
        return False
    if len(text) == 3 and not any(c in "_-" for c in text):
        return False
    return any(c.isdigit() or c.isupper() or c in "_-" for c in text)


def _is_trackable(value: Any) -> bool:
    if is_dynamic_value(value):
        return True
    return isinstance(value, str) and _identifier_like(value)


@dataclass
class ProducedValue:
    exchange_id: str
    selector: str
    value: Any
    value_type: str

    @property
    def canonical(self) -> str:
        return str(self.value)


@dataclass
class ConsumedSlot:
    exchange_id: str
    location: str
    value: Any


@dataclass
class _VariableGroup:
    variable_id: str
    producer: ProducedValue
    bindings: list[VariableBinding] = field(default_factory=list)


def iter_produced_values(exchange: HttpExchange) -> Iterator[ProducedValue]:
    """Scalar values a response introduces that later requests might reuse."""
    if exchange.response_body_encoding == "json":
        for path, leaf in iter_json_leaves(exchange.response_body):
            if _is_trackable(leaf):
                yield ProducedValue(
                    exchange_id=exchange.exchange_id,
                    selector=f"json:{path}",
                    value=leaf,
                    value_type=classify_value(leaf),
                )
    for name, header_value in exchange.response_headers.items():
        lowered = name.lower()
        if lowered in {"content-type", "content-length", "date", "server"}:
            continue
        if _is_trackable(header_value):
            yield ProducedValue(
                exchange_id=exchange.exchange_id,
                selector=f"header:{lowered}",
                value=header_value,
                value_type=classify_value(header_value),
            )


def iter_consumed_slots(exchange: HttpExchange) -> Iterator[ConsumedSlot]:
    """Scalar slots of a request where a produced value might reappear."""
    parsed = urlparse(exchange.url)
    for segment in parsed.path.split("/"):
        if segment and _is_trackable(segment):
            yield ConsumedSlot(exchange.exchange_id, "path", segment)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if value and _is_trackable(value):
            yield ConsumedSlot(exchange.exchange_id, f"query:{key}", value)
    for name, value in exchange.request_headers.items():
        lowered = name.lower()
        if lowered in _IGNORED_REQUEST_HEADERS or lowered.startswith("sec-"):
            continue
        if _is_trackable(value):
            yield ConsumedSlot(exchange.exchange_id, f"header:{lowered}", value)
    if exchange.request_body_encoding == "json":
        for path, leaf in iter_json_leaves(exchange.request_body):
            if _is_trackable(leaf):
                yield ConsumedSlot(exchange.exchange_id, f"body:{path}", leaf)
    elif exchange.request_body_encoding == "form" and isinstance(exchange.request_body, dict):
        for key, value in exchange.request_body.items():
            if _is_trackable(value):
                yield ConsumedSlot(exchange.exchange_id, f"body:{key}", value)


def _values_match(produced: str, consumed: Any) -> bool:
    candidate = str(consumed)
    if candidate == produced:
        return True
    if quote(produced, safe="") == candidate:
        return True
    try:
        encoded = base64.b64encode(produced.encode()).decode()
    except Exception:  # pragma: no cover - defensive
        return False
    return encoded == candidate


def _confidence(value_type: str, location: str) -> float:
    base = {
        "uuid": 0.9,
        "token": 0.9,
        "jwt": 0.9,
        "numeric_id": 0.6,
        "email": 0.5,
        "url": 0.4,
        "timestamp": 0.4,
    }.get(value_type, 0.65)
    if location == "path":
        base += 0.05
    return min(base, 0.99)


def _variable_name(selector: str, taken: set[str]) -> str:
    leaf = selector.rsplit(".", 1)[-1].rstrip("]").split("[")[-1].strip("'\"") or "value"
    name = re.sub(r"[^A-Za-z0-9_]", "_", leaf)
    if not name or name[0].isdigit():
        name = f"var_{name}"
    candidate, counter = name, 2
    while candidate in taken:
        candidate = f"{name}_{counter}"
        counter += 1
    taken.add(candidate)
    return candidate


def infer_bindings(exchanges: list[HttpExchange]) -> list[VariableBinding]:
    """Infer candidate variable bindings from a normalized trace.

    Only forward flow is considered: a value must appear in a response
    *before* it is consumed by a later request.
    """
    produced_by_exchange = {e.exchange_id: list(iter_produced_values(e)) for e in exchanges}
    groups: dict[tuple[str, str], _VariableGroup] = {}
    taken_names: set[str] = set()

    for index, consumer in enumerate(exchanges):
        for slot in iter_consumed_slots(consumer):
            for earlier in exchanges[:index]:
                for produced in produced_by_exchange[earlier.exchange_id]:
                    if not _values_match(produced.canonical, slot.value):
                        continue
                    key = (produced.exchange_id, produced.selector)
                    group = groups.get(key)
                    if group is None:
                        variable_id = _variable_name(produced.selector, taken_names)
                        group = _VariableGroup(variable_id=variable_id, producer=produced)
                        groups[key] = group
                    duplicate = any(
                        b.consumer_exchange_id == slot.exchange_id
                        and b.consumer_location == slot.location
                        for b in group.bindings
                    )
                    if duplicate:
                        continue
                    group.bindings.append(
                        VariableBinding(
                            variable_id=group.variable_id,
                            producer_exchange_id=produced.exchange_id,
                            producer_selector=produced.selector,
                            consumer_exchange_id=slot.exchange_id,
                            consumer_location=slot.location,
                            value_type=produced.value_type,
                            confidence=_confidence(produced.value_type, slot.location),
                        )
                    )
    return [binding for group in groups.values() for binding in group.bindings]
