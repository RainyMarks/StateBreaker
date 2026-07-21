"""Active dependency validation: replay the flow with fresh values.

A binding is only *confirmed* when the producer is replayed, a fresh value is
extracted from the live response, substituted into the consumer, and the
consumer still succeeds (spec §6.4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from statebreaker.errors import TemplateError
from statebreaker.execution.client import HttpSender
from statebreaker.execution.templating import render_template
from statebreaker.intelligence.selectors import extract_from_exchange
from statebreaker.models.capture import HttpExchange, RequestTemplate
from statebreaker.models.workflow import VariableBinding

_SUCCESS_STATUSES = range(200, 400)


@dataclass
class TemplateReplayResult:
    template_id: str
    exchange: HttpExchange
    ok: bool


@dataclass
class FlowReplayResult:
    success: bool
    variables: dict[str, Any] = field(default_factory=dict)
    results: list[TemplateReplayResult] = field(default_factory=list)
    failed_template_id: str | None = None
    failure_reason: str | None = None

    def result_for(self, template_id: str) -> TemplateReplayResult | None:
        for result in self.results:
            if result.template_id == template_id:
                return result
        return None


async def send_template(
    template: RequestTemplate,
    variables: Mapping[str, Any],
    sender: HttpSender,
    *,
    session_id: str = "default",
    exchange_id: str | None = None,
) -> HttpExchange:
    """Render and fire one template; used by probes and replay."""
    rendered = render_template(template, variables)
    content, content_headers = rendered.build_content()
    headers = {**rendered.headers, **content_headers}
    return await sender.send(
        session_id=session_id,
        method=rendered.method,
        path_or_url=rendered.path,
        query=rendered.query,
        headers=headers,
        content=content,
        exchange_id=exchange_id or f"send-{template.template_id}",
    )


async def replay_flow(
    templates: list[RequestTemplate],
    bindings: list[VariableBinding],
    sender: HttpSender,
    *,
    session_id: str = "default",
    initial_variables: Mapping[str, Any] | None = None,
    stop_on_failure: bool = True,
) -> FlowReplayResult:
    """Replay a template chain, threading freshly produced values through it."""
    variables: dict[str, Any] = dict(initial_variables or {})
    producers_by_exchange: dict[str, list[tuple[str, str]]] = {}
    for binding in bindings:
        producers_by_exchange.setdefault(binding.producer_exchange_id, [])
        entry = (binding.variable_id, binding.producer_selector)
        if entry not in producers_by_exchange[binding.producer_exchange_id]:
            producers_by_exchange[binding.producer_exchange_id].append(entry)

    replay = FlowReplayResult(success=True)
    for template in templates:
        try:
            rendered = render_template(template, variables)
        except TemplateError as exc:
            replay.success = False
            replay.failed_template_id = template.template_id
            replay.failure_reason = str(exc)
            break

        content, content_headers = rendered.build_content()
        headers = {**rendered.headers, **content_headers}
        exchange = await sender.send(
            session_id=session_id,
            method=rendered.method,
            path_or_url=rendered.path,
            query=rendered.query,
            headers=headers,
            content=content,
            exchange_id=f"replay-{template.template_id}",
        )
        ok = exchange.response_status in _SUCCESS_STATUSES
        replay.results.append(
            TemplateReplayResult(
                template_id=template.template_id, exchange=exchange, ok=ok
            )
        )

        source_id = template.source_exchange_id or template.template_id
        for variable_id, selector in producers_by_exchange.get(source_id, []):
            fresh = extract_from_exchange(exchange, selector)
            if fresh is not None:
                variables[variable_id] = fresh
        replay.variables = variables

        if not ok and stop_on_failure:
            replay.success = False
            replay.failed_template_id = template.template_id
            replay.failure_reason = f"HTTP {exchange.response_status}"
            break
    return replay


def evaluate_bindings(
    bindings: list[VariableBinding],
    replay: FlowReplayResult,
) -> list[VariableBinding]:
    """Mark each binding confirmed/rejected based on the replay outcome."""
    evaluated: list[VariableBinding] = []
    for binding in bindings:
        produced_fresh = binding.variable_id in replay.variables
        consumer = replay.result_for(binding.consumer_exchange_id)
        if consumer is not None and consumer.ok and produced_fresh:
            evaluated.append(
                binding.model_copy(
                    update={
                        "status": "confirmed",
                        "confidence": min(binding.confidence + 0.05, 0.99),
                    }
                )
            )
        elif consumer is not None and not consumer.ok:
            evaluated.append(
                binding.model_copy(
                    update={"status": "rejected", "confidence": binding.confidence * 0.5}
                )
            )
        else:
            evaluated.append(binding)
    return evaluated
