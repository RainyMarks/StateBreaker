"""The generated PoC is not decoration: it runs against a live target (§15.2)."""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from support.recorder import load_lab_app

from statebreaker.models.discovery import ActionInstance, AttackPlan, RaceCandidate
from statebreaker.models.execution import ExecutionTrial, PreparedRequest
from statebreaker.models.findings import Finding, RunStatistics
from statebreaker.reporting import render_poc_script


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _LiveServer:
    def __init__(self, app: Any, port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> _LiveServer:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.mark.timeout(60)
def test_generated_poc_executes_against_live_target(tmp_path: Path) -> None:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    app = load_lab_app("lab-oneshot-redemption")
    with _LiveServer(app, port):
        plan = AttackPlan(
            plan_id="plan-min",
            candidate_id="cand-1",
            action_instances=[
                ActionInstance(instance_id="i-0", action_id="act-a"),
                ActionInstance(instance_id="i-1", action_id="act-a"),
            ],
        )
        trial = ExecutionTrial(
            trial_id="trial-attack-1",
            plan_id="plan-min",
            control_or_attack="attack",
            requests=[
                PreparedRequest(
                    instance_id=f"i-{index}",
                    method="POST",
                    url=f"{base_url}/__test__/reset",
                    headers={"content-type": "application/json"},
                    body=b"{}",
                )
                for index in range(2)
            ],
        )
        finding = Finding(
            finding_id="finding-plan-1",
            verdict="confirmed",
            confidence=0.9,
            candidate=RaceCandidate(
                candidate_id="cand-1", kind="same_action", action_ids=["act-a"]
            ),
            minimized_plan_id="plan-min",
            evidence_refs=["trial-attack-1"],
            statistics=RunStatistics(rounds=2, successes=2, success_rate=1.0),
        )
        script = render_poc_script(finding, plan, trial)
        poc = tmp_path / "poc.py"
        poc.write_text(script, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(poc)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "[0] status=200" in result.stdout
        assert "[1] status=200" in result.stdout
