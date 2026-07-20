"""Conservative inference of replay variables from prior HAR JSON responses."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from jsonpath_ng.ext import parse as parse_jsonpath  # type: ignore[import-untyped]

from statebreaker_har_capture.response_body import decode_json_response

JsonPathComponent = str | int
ScalarValue = str | int
MatchMode = Literal["python", "text"]
LocationKind = Literal["path", "query", "json", "form"]
SourceKey = tuple[int, str, str, str]
LocationPathKey = tuple[tuple[int, str | int], ...]

_MIN_STRING_LENGTH = 8
_MIN_INTEGER_ABS = 1000
_COMMON_STRING_VALUES = frozenset(
    {
        "true",
        "false",
        "success",
        "successful",
        "ok",
        "active",
        "inactive",
        "pending",
        "complete",
        "completed",
        "failed",
        "failure",
        "enabled",
        "disabled",
        "open",
        "closed",
        "created",
        "updated",
        "deleted",
        "yes",
        "no",
        "none",
        "null",
        "unknown",
    }
)
_SENSITIVE_WORDS = frozenset(
    {
        "authorization",
        "cookie",
        "password",
        "passwd",
        "token",
        "secret",
        "csrf",
        "xsrf",
        "session",
    }
)
_SENSITIVE_COMPACT_NAMES = frozenset({"accesstoken", "refreshtoken", "apikey"})
_IDENTIFIER_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INTEGER_TEXT = re.compile(r"^[+-]?\d+$")
_JWT_TEXT = re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$")
_SHORT_BUSINESS_CONSTANT = re.compile(r"^[A-Z][A-Z0-9_-]{7,15}$")
_COOKIE_NAME = re.compile(r"^[^=;\s]+$")
_COOKIE_FLAG_ATTRIBUTES = frozenset({"secure", "httponly", "partitioned"})
_LOCATION_KIND_ORDER: dict[LocationKind, int] = {
    "path": 0,
    "query": 1,
    "json": 2,
    "form": 3,
}


@dataclass(frozen=True, slots=True)
class ResponseCandidate:
    """One safe scalar leaf from a retained producer response."""

    entry_index: int
    producer_step_id: str
    json_path: str
    field_path: tuple[JsonPathComponent, ...]
    value: ScalarValue
    text_value: str

    @property
    def source_key(self) -> SourceKey:
        return (
            self.entry_index,
            self.producer_step_id,
            self.json_path,
            _strict_type_name(self.value),
        )


@dataclass(frozen=True, slots=True)
class VariableBinding:
    """A uniquely selected producer candidate and its stable variable name."""

    candidate: ResponseCandidate
    name: str


@dataclass(frozen=True, slots=True)
class ConsumerMatchIntent:
    """One immutable consumer location that may use a prior response value."""

    entry_index: int
    consumer_step_id: str
    kind: LocationKind
    field_path: tuple[JsonPathComponent, ...]
    value: ScalarValue
    mode: MatchMode

    @property
    def location_key(self) -> tuple[int, str, int, LocationPathKey, str]:
        return (
            self.entry_index,
            self.consumer_step_id,
            _LOCATION_KIND_ORDER[self.kind],
            _location_path_key(self.field_path),
            _strict_type_name(self.value),
        )


class InferenceInvariantError(RuntimeError):
    """Raised only when internal normalization assumptions are violated."""


def _strict_type_name(value: object) -> str:
    return f"{type(value).__module__}.{type(value).__qualname__}"


def _location_path_key(path: Sequence[JsonPathComponent]) -> LocationPathKey:
    return tuple(
        (0, component) if isinstance(component, str) else (1, component) for component in path
    )


def _normalized_name_parts(component: str) -> tuple[list[str], str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", component)
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", expanded).strip("_").lower()
    parts = [part for part in normalized.split("_") if part]
    return parts, "".join(parts)


def is_sensitive_field_path(path: Sequence[JsonPathComponent]) -> bool:
    """Return whether any field component carries credential-sensitive meaning."""

    for component in path:
        if not isinstance(component, str):
            continue
        _, compact = _normalized_name_parts(component)
        if any(marker in compact for marker in _SENSITIVE_WORDS):
            return True
        if any(marker in compact for marker in _SENSITIVE_COMPACT_NAMES):
            return True
    return False


def _cookie_pair(part: str) -> bool:
    if "=" not in part:
        return False
    name, value = part.split("=", maxsplit=1)
    return _COOKIE_NAME.fullmatch(name.strip()) is not None and bool(value)


def _looks_like_cookie_value(value: str) -> bool:
    """Return whether *value* has a Cookie or Set-Cookie field shape."""

    parts = [part.strip() for part in value.strip().split(";")]
    if not parts or not _cookie_pair(parts[0]):
        return False
    for attribute in parts[1:]:
        if not attribute:
            return False
        if "=" in attribute:
            if not _cookie_pair(attribute):
                return False
            continue
        if attribute.casefold() not in _COOKIE_FLAG_ATTRIBUTES:
            return False
    return True


def _looks_sensitive_value(value: str) -> bool:
    folded = value.casefold()
    if folded.startswith("bearer "):
        return True
    if "-----begin " in folded and "private key-----" in folded:
        return True
    if _JWT_TEXT.fullmatch(value) is not None:
        return True
    return _looks_like_cookie_value(value)


def _safe_scalar(value: Any) -> ScalarValue | None:
    if isinstance(value, bool) or value is None or isinstance(value, float):
        return None
    if isinstance(value, int):
        return value if abs(value) >= _MIN_INTEGER_ABS else None
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if (
        stripped != value
        or any(character.isspace() for character in stripped)
        or len(stripped) < _MIN_STRING_LENGTH
    ):
        return None
    folded = stripped.casefold()
    if folded in _COMMON_STRING_VALUES:
        return None
    if _SHORT_BUSINESS_CONSTANT.fullmatch(stripped) is not None:
        return None
    if "/" in stripped or "%" in stripped:
        return None
    if _INTEGER_TEXT.fullmatch(stripped) is not None:
        try:
            if abs(int(stripped)) < _MIN_INTEGER_ABS:
                return None
        except ValueError:
            return None
    if _looks_sensitive_value(stripped):
        return None
    return stripped




def _json_path(path: Sequence[JsonPathComponent]) -> str | None:
    expression = "$"
    for component in path:
        if isinstance(component, int):
            if component < 0:
                return None
            expression += f"[{component}]"
            continue
        if not component or any(ord(character) < 32 for character in component):
            return None
        if _IDENTIFIER_KEY.fullmatch(component) is not None:
            expression += f".{component}"
        else:
            expression += f"[{json.dumps(component, ensure_ascii=False)}]"
    return expression


def _json_path_selects_unique_value(expression: str, document: Any, expected: ScalarValue) -> bool:
    try:
        matches = parse_jsonpath(expression).find(document)
    except Exception:
        return False
    if len(matches) != 1:
        return False
    actual = matches[0].value
    return type(actual) is type(expected) and actual == expected


def collect_response_candidates(
    entry_index: int,
    producer_step_id: str,
    entry: Mapping[str, Any],
) -> tuple[ResponseCandidate, ...]:
    """Collect safe scalar leaves without mutating the HAR entry."""

    decoded = decode_json_response(entry)
    if decoded.failure is not None:
        return ()
    document = decoded.value

    candidates: list[ResponseCandidate] = []

    def visit(value: Any, path: tuple[JsonPathComponent, ...]) -> None:
        if isinstance(value, Mapping):
            for key in sorted(value, key=lambda item: str(item)):
                if isinstance(key, str):
                    item = value[key]
                    visit(item, (*path, key))
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, (*path, index))
            return
        if not path or is_sensitive_field_path(path):
            return
        scalar = _safe_scalar(value)
        if scalar is None:
            return
        expression = _json_path(path)
        if expression is None or not _json_path_selects_unique_value(expression, document, scalar):
            return
        candidates.append(
            ResponseCandidate(
                entry_index=entry_index,
                producer_step_id=producer_step_id,
                json_path=expression,
                field_path=path,
                value=scalar,
                text_value=str(scalar),
            )
        )

    visit(document, ())
    return tuple(candidates)


def _snake_case(value: str) -> str:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", expanded).strip("_").lower()
    return normalized


def _singularize(value: str) -> str:
    if value.endswith("ies") and len(value) > 3:
        return f"{value[:-3]}y"
    if value.endswith("s") and not value.endswith("ss") and len(value) > 3:
        return value[:-1]
    return value


def _base_variable_name(candidate: ResponseCandidate) -> str:
    string_components = [
        _snake_case(component)
        for component in candidate.field_path
        if isinstance(component, str) and _snake_case(component)
    ]
    if not string_components:
        base = "value"
    else:
        base = string_components[-1]
        if base == "id" and len(string_components) > 1:
            base = f"{_singularize(string_components[-2])}_id"
    if not base or not base[0].isalpha():
        base = f"value_{base}" if base else "value"
    return base


def _variable_name_candidates(candidate: ResponseCandidate) -> list[str]:
    base = _base_variable_name(candidate)
    parents = [
        _snake_case(component)
        for component in candidate.field_path[:-1]
        if isinstance(component, str) and _snake_case(component)
    ]
    names = [base]
    prefix_parts: list[str] = []
    for parent in reversed(parents):
        prefix_parts.insert(0, _singularize(parent))
        prefixed = "_".join([*prefix_parts, base])
        if prefixed not in names and not base.startswith(f"{prefix_parts[-1]}_"):
            names.append(prefixed)
    names.append(f"{base}_entry_{candidate.entry_index}")
    digest = hashlib.sha256(f"{candidate.entry_index}\0{candidate.json_path}".encode()).hexdigest()[
        :8
    ]
    names.append(f"{base}_{digest}")
    return names


def _allocate_variable_name(candidate: ResponseCandidate, used_names: Collection[str]) -> str:
    for name in _variable_name_candidates(candidate):
        if name not in used_names and name[0].isalpha():
            return name
    raise InferenceInvariantError(
        "HAR response inference invariant error: could not allocate a unique variable name"
    )


def _candidate_matches(candidate: ResponseCandidate, value: Any, mode: MatchMode) -> bool:
    if mode == "text":
        return isinstance(value, str) and candidate.text_value == value
    return type(candidate.value) is type(value) and candidate.value == value


def _append_consumer_scalar_intent(
    intents: list[ConsumerMatchIntent],
    *,
    entry_index: int,
    consumer_step_id: str,
    kind: LocationKind,
    field_path: tuple[JsonPathComponent, ...],
    value: Any,
    mode: MatchMode,
) -> None:
    if is_sensitive_field_path(field_path):
        return
    if mode == "text":
        if not isinstance(value, str):
            return
    elif isinstance(value, bool) or not isinstance(value, (str, int)):
        return
    intents.append(
        ConsumerMatchIntent(
            entry_index=entry_index,
            consumer_step_id=consumer_step_id,
            kind=kind,
            field_path=field_path,
            value=value,
            mode=mode,
        )
    )


def _collect_nested_consumer_intents(
    value: Any,
    field_path: tuple[JsonPathComponent, ...],
    *,
    entry_index: int,
    consumer_step_id: str,
    kind: LocationKind,
    mode: MatchMode,
    intents: list[ConsumerMatchIntent],
) -> None:
    if isinstance(value, Mapping):
        for key in sorted(value, key=lambda item: str(item)):
            if isinstance(key, str):
                _collect_nested_consumer_intents(
                    value[key],
                    (*field_path, key),
                    entry_index=entry_index,
                    consumer_step_id=consumer_step_id,
                    kind=kind,
                    mode=mode,
                    intents=intents,
                )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _collect_nested_consumer_intents(
                item,
                (*field_path, index),
                entry_index=entry_index,
                consumer_step_id=consumer_step_id,
                kind=kind,
                mode=mode,
                intents=intents,
            )
        return
    _append_consumer_scalar_intent(
        intents,
        entry_index=entry_index,
        consumer_step_id=consumer_step_id,
        kind=kind,
        field_path=field_path,
        value=value,
        mode=mode,
    )


def _collect_consumer_intents(
    entry_index: int, step: Mapping[str, Any]
) -> tuple[ConsumerMatchIntent, ...]:
    step_id = step.get("id")
    request = step.get("request")
    if not isinstance(step_id, str) or not isinstance(request, Mapping):
        raise InferenceInvariantError(
            "HAR response inference invariant error: step request must be an object"
        )

    intents: list[ConsumerMatchIntent] = []
    path = request.get("path")
    if isinstance(path, str):
        for index, segment in enumerate(path.split("/")):
            if segment and "%" not in segment:
                _append_consumer_scalar_intent(
                    intents,
                    entry_index=entry_index,
                    consumer_step_id=step_id,
                    kind="path",
                    field_path=(index,),
                    value=segment,
                    mode="text",
                )

    request_locations: tuple[tuple[str, LocationKind, MatchMode], ...] = (
        ("query", "query", "text"),
        ("json_body", "json", "python"),
        ("form_body", "form", "text"),
    )
    for request_key, kind, mode in request_locations:
        if request_key not in request or request[request_key] is None:
            continue
        _collect_nested_consumer_intents(
            request[request_key],
            (),
            entry_index=entry_index,
            consumer_step_id=step_id,
            kind=kind,
            mode=mode,
            intents=intents,
        )
    return tuple(sorted(intents, key=lambda intent: intent.location_key))


def _resolve_candidate_for_intent(
    intent: ConsumerMatchIntent,
    candidates: Sequence[ResponseCandidate],
    established_sources: Mapping[SourceKey, ResponseCandidate],
) -> ResponseCandidate | None:
    established = [
        candidate
        for candidate in established_sources.values()
        if _candidate_matches(candidate, intent.value, intent.mode)
    ]
    if len(established) == 1:
        return established[0]
    if established:
        return None

    matching_sources: dict[SourceKey, ResponseCandidate] = {}
    for candidate in candidates:
        if _candidate_matches(candidate, intent.value, intent.mode):
            matching_sources[candidate.source_key] = candidate
    if len(matching_sources) != 1:
        return None
    return next(iter(matching_sources.values()))


def _plan_bindings(
    retained_entries: Sequence[tuple[int, Mapping[str, Any]]],
    steps: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[SourceKey, ResponseCandidate],
    dict[SourceKey, list[ConsumerMatchIntent]],
]:
    candidates: list[ResponseCandidate] = []
    established_sources: dict[SourceKey, ResponseCandidate] = {}
    candidates_by_source: dict[SourceKey, ResponseCandidate] = {}
    targets_by_source: dict[SourceKey, list[ConsumerMatchIntent]] = {}

    for (entry_index, entry), step in zip(retained_entries, steps, strict=True):
        for intent in _collect_consumer_intents(entry_index, step):
            candidate = _resolve_candidate_for_intent(intent, candidates, established_sources)
            if candidate is None:
                continue
            established_sources.setdefault(candidate.source_key, candidate)
            candidates_by_source[candidate.source_key] = candidate
            targets_by_source.setdefault(candidate.source_key, []).append(intent)

        step_id = step["id"]
        candidates.extend(collect_response_candidates(entry_index, step_id, entry))

    return candidates_by_source, targets_by_source


def _allocate_bindings(
    candidates_by_source: Mapping[SourceKey, ResponseCandidate],
    used_names: Collection[str],
) -> dict[SourceKey, VariableBinding]:
    allocated_names = set(used_names)
    bindings: dict[SourceKey, VariableBinding] = {}
    for source_key in sorted(candidates_by_source):
        candidate = candidates_by_source[source_key]
        name = _allocate_variable_name(candidate, allocated_names)
        bindings[source_key] = VariableBinding(candidate=candidate, name=name)
        allocated_names.add(name)
    return bindings


def _add_extractors(
    bindings: Mapping[SourceKey, VariableBinding],
    steps_by_id: Mapping[str, dict[str, Any]],
) -> None:
    for source_key in sorted(bindings):
        binding = bindings[source_key]
        producer = steps_by_id.get(binding.candidate.producer_step_id)
        if producer is None:
            raise InferenceInvariantError(
                "HAR response inference invariant error: producer step is missing"
            )
        extractors = producer.get("extract")
        if not isinstance(extractors, list):
            raise InferenceInvariantError(
                "HAR response inference invariant error: producer extract must be a list"
            )
        extractors.append(
            {
                "name": binding.name,
                "kind": "jsonpath",
                "expression": binding.candidate.json_path,
                "required": True,
            }
        )


def _validate_target_value(actual: Any, expected: ScalarValue) -> None:
    if type(actual) is not type(expected) or actual != expected:
        raise InferenceInvariantError(
            "HAR response inference invariant error: consumer target changed during planning"
        )


def _replace_nested_target(
    root: Any,
    field_path: Sequence[JsonPathComponent],
    expected: ScalarValue,
    replacement: str,
) -> Any:
    if not field_path:
        _validate_target_value(root, expected)
        return replacement

    current = root
    for component in field_path[:-1]:
        if isinstance(component, str) and isinstance(current, dict):
            current = current.get(component)
        elif isinstance(component, int) and isinstance(current, list):
            if component >= len(current):
                raise InferenceInvariantError(
                    "HAR response inference invariant error: consumer list target is missing"
                )
            current = current[component]
        else:
            raise InferenceInvariantError(
                "HAR response inference invariant error: consumer target is missing"
            )

    final = field_path[-1]
    if isinstance(final, str) and isinstance(current, dict) and final in current:
        _validate_target_value(current[final], expected)
        current[final] = replacement
        return root
    if isinstance(final, int) and isinstance(current, list) and final < len(current):
        _validate_target_value(current[final], expected)
        current[final] = replacement
        return root
    raise InferenceInvariantError(
        "HAR response inference invariant error: consumer target is missing"
    )


def _apply_consumer_target(
    step: dict[str, Any], intent: ConsumerMatchIntent, binding: VariableBinding
) -> None:
    request = step.get("request")
    if not isinstance(request, dict):
        raise InferenceInvariantError(
            "HAR response inference invariant error: step request must be an object"
        )
    replacement = f"${{{binding.name}}}"
    if intent.kind == "path":
        path = request.get("path")
        if not isinstance(path, str) or len(intent.field_path) != 1:
            raise InferenceInvariantError(
                "HAR response inference invariant error: consumer path target is missing"
            )
        segment_index = intent.field_path[0]
        if not isinstance(segment_index, int):
            raise InferenceInvariantError(
                "HAR response inference invariant error: consumer path target is invalid"
            )
        segments = path.split("/")
        if segment_index >= len(segments):
            raise InferenceInvariantError(
                "HAR response inference invariant error: consumer path target is missing"
            )
        _validate_target_value(segments[segment_index], intent.value)
        segments[segment_index] = replacement
        request["path"] = "/".join(segments)
        return

    request_key = {
        "query": "query",
        "json": "json_body",
        "form": "form_body",
    }[intent.kind]
    if request_key not in request:
        raise InferenceInvariantError(
            "HAR response inference invariant error: consumer request target is missing"
        )
    request[request_key] = _replace_nested_target(
        request[request_key], intent.field_path, intent.value, replacement
    )


def _apply_binding_plan(
    bindings: Mapping[SourceKey, VariableBinding],
    targets_by_source: Mapping[SourceKey, Sequence[ConsumerMatchIntent]],
    inferred_steps: Sequence[dict[str, Any]],
    steps_by_id: Mapping[str, dict[str, Any]],
) -> None:
    planned_targets = [
        (intent, bindings[source_key])
        for source_key, intents in targets_by_source.items()
        for intent in intents
    ]
    dependencies_by_consumer: dict[str, set[str]] = {}
    for intent, binding in sorted(planned_targets, key=lambda item: item[0].location_key):
        consumer = steps_by_id[intent.consumer_step_id]
        _apply_consumer_target(consumer, intent, binding)
        dependencies_by_consumer.setdefault(intent.consumer_step_id, set()).add(
            binding.candidate.producer_step_id
        )

    step_positions = {step["id"]: index for index, step in enumerate(inferred_steps)}
    for consumer in inferred_steps:
        consumer_id = consumer["id"]
        producer_ids = dependencies_by_consumer.get(consumer_id, set())
        dependencies = consumer.get("depends_on")
        if not isinstance(dependencies, list):
            raise InferenceInvariantError(
                "HAR response inference invariant error: depends_on must be a list"
            )
        for producer_id in sorted(producer_ids, key=step_positions.__getitem__):
            if step_positions[producer_id] >= step_positions[consumer_id]:
                raise InferenceInvariantError(
                    "HAR response inference invariant error: producer must appear earlier"
                )
            if producer_id not in dependencies:
                dependencies.append(producer_id)


def infer_response_variables(
    retained_entries: Sequence[tuple[int, Mapping[str, Any]]],
    steps: list[dict[str, Any]],
    *,
    existing_variable_names: Collection[str] = (),
) -> list[dict[str, Any]]:
    """Return inferred steps without mutating HAR entries or input step mappings."""

    if len(retained_entries) != len(steps):
        raise InferenceInvariantError(
            "HAR response inference invariant error: entry and step counts differ"
        )

    inferred_steps = copy.deepcopy(steps)
    steps_by_id: dict[str, dict[str, Any]] = {}
    used_names = set(existing_variable_names)
    for step in inferred_steps:
        step_id = step.get("id")
        if not isinstance(step_id, str) or step_id in steps_by_id:
            raise InferenceInvariantError(
                "HAR response inference invariant error: step IDs must be unique strings"
            )
        steps_by_id[step_id] = step
        extractors = step.get("extract")
        if not isinstance(extractors, list):
            raise InferenceInvariantError(
                "HAR response inference invariant error: producer extract must be a list"
            )
        for extractor in extractors:
            if not isinstance(extractor, Mapping) or not isinstance(extractor.get("name"), str):
                raise InferenceInvariantError(
                    "HAR response inference invariant error: extractor names must be strings"
                )
            used_names.add(extractor["name"])

    candidates_by_source, targets_by_source = _plan_bindings(retained_entries, inferred_steps)
    bindings = _allocate_bindings(candidates_by_source, used_names)
    _add_extractors(bindings, steps_by_id)
    _apply_binding_plan(bindings, targets_by_source, inferred_steps, steps_by_id)
    return inferred_steps
