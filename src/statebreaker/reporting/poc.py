"""Render a standalone executable PoC from a verified plan and a real trial.

The generated script depends only on the Python standard library: it fires
the exact rendered requests recorded in the confirming attack trial, released
on a threading barrier with the plan's per-instance offsets.
"""

from __future__ import annotations

from statebreaker.i18n import bi
from statebreaker.models.discovery import AttackPlan
from statebreaker.models.execution import ExecutionTrial
from statebreaker.models.findings import Finding

_TEMPLATE = '''#!/usr/bin/env python3
"""StateBreaker PoC — {finding_id}

Verdict: {verdict} (confidence {confidence:.2f})
Candidate: {candidate_id}  actions={action_ids}
Scheduler: {scheduler}  concurrency={concurrency}
Success rate during verification: {success_rate}
Evidence trials: {evidence}

{summary_line}
{reset_line}
"""

import http.client
import threading
import time
from urllib.parse import urlparse

REQUESTS = {requests}

OFFSETS_MS = {offsets}


def _fire(spec, offset_ms, barrier, results, index):
    parsed = urlparse(spec["url"])
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=15)
    target = parsed.path or "/"
    if parsed.query:
        target = target + "?" + parsed.query
    barrier.wait()
    if offset_ms > 0:
        time.sleep(offset_ms / 1000.0)
    connection.request(
        spec["method"], target, body=spec["body"], headers=spec["headers"]
    )
    response = connection.getresponse()
    results[index] = (response.status, response.read().decode(errors="replace"))
    connection.close()


def main():
    barrier = threading.Barrier(len(REQUESTS))
    results = [None] * len(REQUESTS)
    threads = [
        threading.Thread(
            target=_fire,
            args=(spec, OFFSETS_MS[index] if index < len(OFFSETS_MS) else 0.0,
                  barrier, results, index),
        )
        for index, spec in enumerate(REQUESTS)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for index, result in enumerate(results):
        status, body = result
        print(f"[{{index}}] status={{status}} body={{body[:200]}}")


if __name__ == "__main__":
    main()
'''


def render_poc_script(
    finding: Finding,
    plan: AttackPlan,
    trial: ExecutionTrial,
) -> str:
    """Render the PoC for a confirmed finding from recorded trial requests."""
    if not trial.requests:
        raise ValueError(f"trial {trial.trial_id} recorded no requests; cannot render PoC")
    specs = [
        {
            "instance_id": request.instance_id,
            "method": request.method,
            "url": request.url,
            "headers": dict(request.headers),
            "body": request.body,
        }
        for request in trial.requests
    ]
    success_rate = (
        f"{finding.statistics.successes}/{finding.statistics.rounds}"
        if finding.statistics
        else ("n/a" if finding.success_rate is None else f"{finding.success_rate:.2f}")
    )
    return _TEMPLATE.format(
        finding_id=finding.finding_id,
        verdict=finding.verdict.upper(),
        confidence=finding.confidence,
        candidate_id=finding.candidate.candidate_id,
        action_ids=finding.candidate.action_ids,
        scheduler=plan.scheduler,
        concurrency=len(plan.action_instances),
        success_rate=success_rate,
        evidence=", ".join(finding.evidence_refs),
        summary_line=bi(
            "会重新发起确认用 attack trial 中真实记录的请求。",
            "Re-fires the exact requests captured in the confirming attack trial.",
        ),
        reset_line=bi(
            "每次运行前都应把目标重置到干净状态。",
            "The target must be reset to a fresh state before each run.",
        ),
        requests=repr(specs),
        offsets=repr(list(plan.offsets_ms)),
    )
