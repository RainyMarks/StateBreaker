"""CLI smoke test: real uvicorn server, real TCP, full command path.

This is the demo scenario: a target the tool has never seen, one recorded
normal flow, then `discover` and `scan` produce a CONFIRMED finding.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from support.recorder import load_lab_app
from typer.testing import CliRunner

from statebreaker.artifacts.store import ArtifactStore
from statebreaker.cli.app import app as cli_app
from statebreaker.models.capture import CapturedTrace, HttpExchange
from statebreaker.models.findings import Finding

runner = CliRunner()


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


def _record_over_http(base_url: str) -> CapturedTrace:
    exchanges: list[HttpExchange] = []
    with httpx.Client(base_url=base_url, timeout=10) as client:
        issued = client.post("/perks/issue", json={"credit": 50})
        code = issued.json()["perk"]["code"]
        exchanges.append(_exchange("http-1", "POST", "/perks/issue", {"credit": 50}, issued))
        claimed = client.post(f"/perks/{code}/claim", headers={"X-User-Id": "alice"})
        exchanges.append(
            _exchange("http-2", "POST", f"/perks/{code}/claim", None, claimed,
                      headers={"x-user-id": "alice"})
        )
        perk = client.get(f"/perks/{code}")
        exchanges.append(_exchange("http-3", "GET", f"/perks/{code}", None, perk))
        account = client.get("/accounts/alice")
        exchanges.append(_exchange("http-4", "GET", "/accounts/alice", None, account))
    return CapturedTrace(
        capture_id="cli-capture",
        source="manual",
        project="cli-demo",
        base_url=base_url,
        sessions=["alice"],
        exchanges=exchanges,
    )


def _exchange(
    exchange_id: str,
    method: str,
    path: str,
    json_body: Any,
    response: httpx.Response,
    headers: dict[str, str] | None = None,
) -> HttpExchange:
    body: Any = None
    encoding = "none"
    if response.content:
        if "json" in response.headers.get("content-type", ""):
            body = response.json()
            encoding = "json"
        else:
            body = response.text
            encoding = "raw"
    return HttpExchange(
        exchange_id=exchange_id,
        session_id="alice",
        method=method,
        url=str(response.url),
        request_headers=headers or {},
        request_body=json_body,
        request_body_encoding="json" if json_body is not None else "none",
        response_status=response.status_code,
        response_headers={k.lower(): v for k, v in response.headers.items()},
        response_body=body,
        response_body_encoding=encoding,  # type: ignore[arg-type]
    )


def _write_project(directory: Path, base_url: str) -> None:
    directory.mkdir(parents=True)
    (directory / "project.yaml").write_text(
        f"""
schema_version: "0.2"
project:
  name: cli-demo
  base_url: {base_url}
scope:
  allowed_hosts: ["127.0.0.1"]
  requests_per_second: 1000
sessions:
  alice: {{}}
  bob:
    headers:
      X-User-Id: bob
reset:
  strategy: api
  endpoint: /__test__/reset
discovery:
  max_candidates: 6
execution:
  schedulers: [async-http]
  concurrency: [2]
  repetitions: 2
budget:
  maximum_requests: 800
  maximum_minutes: 5
""",
        encoding="utf-8",
    )


@pytest.mark.timeout(90)
def test_cli_discover_and_scan_over_real_http(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    app = load_lab_app("lab-oneshot-redemption")
    monkeypatch.chdir(tmp_path)

    with _LiveServer(app, port):
        trace = _record_over_http(base_url)
        project_dir = tmp_path / ".statebreaker" / "projects" / "cli-demo"
        _write_project(project_dir, base_url)
        store = ArtifactStore(project_dir)
        store.save("captures", trace.capture_id, trace)
        store.close()

        discover_result = runner.invoke(cli_app, ["discover", "--project", "cli-demo"])
        assert discover_result.exit_code == 0, discover_result.output
        assert "Workflow nodes: 4" in discover_result.output
        assert "Confirmed variable bindings: 2" in discover_result.output
        assert "State probes: 2" in discover_result.output
        assert "High-risk actions: 2" in discover_result.output

        scan_result = runner.invoke(cli_app, ["scan", "--project", "cli-demo", "--auto"])
        assert scan_result.exit_code == 0, scan_result.output
        assert "Captured actions: 4" in scan_result.output
        assert "Finding: CONFIRMED" in scan_result.output
        assert "Control result:" in scan_result.output
        assert "Concurrent result:" in scan_result.output
        assert "Success rate:" in scan_result.output

        findings_result = runner.invoke(cli_app, ["findings", "list", "--project", "cli-demo"])
        assert findings_result.exit_code == 0
        assert "CONFIRMED" in findings_result.output

        # Phase 5: report + reproduce work off the stored finding
        store = ArtifactStore(project_dir)
        finding_ids = [
            fid
            for fid in store.list_ids("findings")
            if store.load("findings", fid, Finding).verdict == "confirmed"
        ]
        assert finding_ids, "expected a confirmed finding on disk"
        finding_id = finding_ids[0]
        store.close()

        report_result = runner.invoke(
            cli_app, ["report", finding_id, "--project", "cli-demo"]
        )
        assert report_result.exit_code == 0, report_result.output
        assert "poc:" in report_result.output
        poc_path = Path(
            report_result.output.split("poc:", 1)[1].strip().splitlines()[0]
        )
        compile(poc_path.read_text(encoding="utf-8"), str(poc_path), "exec")

        reproduce_result = runner.invoke(
            cli_app, ["reproduce", finding_id, "--project", "cli-demo"]
        )
        assert reproduce_result.exit_code == 0, reproduce_result.output
        assert "http.client" in reproduce_result.output
