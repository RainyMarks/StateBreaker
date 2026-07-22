"""Guided CLI tests: setup, automatic flow, and safe cancellation/error paths."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from statebreaker.artifacts.store import ArtifactStore
from statebreaker.cli import capture as capture_module
from statebreaker.cli import wizard as wizard_module
from statebreaker.cli.app import app as cli_app
from statebreaker.cli.project import create_project
from statebreaker.models.capture import CapturedTrace, HttpExchange
from statebreaker.models.discovery import ActionInstance, AttackPlan, RaceCandidate
from statebreaker.models.execution import ExecutionTrial, PreparedRequest
from statebreaker.models.findings import Finding, ScanOutcome

runner = CliRunner()


def _one_click_entry() -> str:
    """Prefer the future run command when present, otherwise exercise today's wizard."""
    for command in cli_app.registered_commands:
        if command.name == "run":
            return "run"
    return "wizard"


def _discovery_only_flag() -> str:
    return "--discovery-only" if _one_click_entry() == "run" else "--skip-scan"


def _seed_project(tmp_path: Path, monkeypatch) -> Path:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    directory = create_project("demo", "http://127.0.0.1:8080")
    store = ArtifactStore(directory)
    try:
        store.save(
            "captures",
            "capture-1",
            CapturedTrace(
                capture_id="capture-1",
                source="manual",
                project="demo",
            ),
        )
    finally:
        store.close()
    return directory


def _seed_confirmed_finding(directory: Path) -> None:
    plan = AttackPlan(
        plan_id="plan-1",
        candidate_id="candidate-1",
        action_instances=[
            ActionInstance(instance_id="instance-1", action_id="action-1")
        ],
        concurrency=2,
    )
    request = PreparedRequest(
        instance_id="instance-1",
        method="POST",
        url="http://127.0.0.1:8080/action",
        body=b"{}",
    )
    attack = ExecutionTrial(
        trial_id="trial-attack",
        candidate_id="candidate-1",
        plan_id=plan.plan_id,
        control_or_attack="attack",
        requests=[request],
    )
    control = attack.model_copy(
        update={"trial_id": "trial-control", "control_or_attack": "control"}
    )
    finding = Finding(
        finding_id="finding-1",
        verdict="confirmed",
        confidence=0.9,
        candidate=RaceCandidate(
            candidate_id="candidate-1",
            kind="same_action",
            action_ids=["action-1"],
        ),
        minimized_plan_id=plan.plan_id,
        evidence_refs=[control.trial_id, attack.trial_id],
        success_rate=1.0,
    )
    store = ArtifactStore(directory)
    try:
        store.save("plans", plan.plan_id, plan)
        store.save("trials", control.trial_id, control)
        store.save("trials", attack.trial_id, attack)
        store.save("findings", finding.finding_id, finding)
    finally:
        store.close()


def test_wizard_runs_existing_project_automatically(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    directory = _seed_project(tmp_path, monkeypatch)
    _seed_confirmed_finding(directory)
    calls: list[tuple[str, str, str]] = []

    def fake_discovery(project: str, capture_id: str | None = None) -> object:
        calls.append(("discover", project, capture_id or ""))
        return object()

    def fake_scan(project: str, capture_id: str | None = None) -> ScanOutcome:
        calls.append(("scan", project, capture_id or ""))
        return ScanOutcome(
            scan_id="scan-capture-1",
            project=project,
            capture_id=capture_id or "",
            status="completed",
            finding_ids=["finding-1"],
        )

    monkeypatch.setattr(wizard_module, "run_project_discovery", fake_discovery)
    monkeypatch.setattr(
        wizard_module,
        "print_discovery_summary",
        lambda result: None,
    )
    monkeypatch.setattr(wizard_module, "run_project_scan", fake_scan)

    result = runner.invoke(
        cli_app,
        [
            "wizard",
            "--project",
            "demo",
            "--capture-id",
            "capture-1",
            "--auto",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        ("discover", "demo", "capture-1"),
        ("scan", "demo", "capture-1"),
    ]
    assert "Discovery preview" in result.output
    assert "Race scan" in result.output
    assert "Finding: CONFIRMED" in result.output
    assert "Reports" in result.output
    assert (directory / "reports" / "finding-1.html").is_file()
    assert (directory / "reports" / "finding-1-poc.py").is_file()


def test_run_reuses_latest_capture_and_reports_without_prompts(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    directory = _seed_project(tmp_path, monkeypatch)
    _seed_confirmed_finding(directory)
    calls: list[tuple[str, str, str]] = []

    def fake_discovery(project: str, capture_id: str | None = None) -> object:
        calls.append(("discover", project, capture_id or ""))
        return object()

    def fake_scan(project: str, capture_id: str | None = None) -> ScanOutcome:
        calls.append(("scan", project, capture_id or ""))
        return ScanOutcome(
            scan_id="scan-capture-1",
            project=project,
            capture_id=capture_id or "",
            status="completed",
            finding_ids=["finding-1"],
        )

    monkeypatch.setattr(wizard_module, "run_project_discovery", fake_discovery)
    monkeypatch.setattr(
        wizard_module,
        "print_discovery_summary",
        lambda result: None,
    )
    monkeypatch.setattr(wizard_module, "run_project_scan", fake_scan)

    result = runner.invoke(
        cli_app,
        [_one_click_entry(), "--project", "demo", "--auto"],
        input="\n",
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        ("discover", "demo", "capture-1"),
        ("scan", "demo", "capture-1"),
    ]
    assert "Latest capture: 'capture-1'" in result.output
    assert "Run concurrent race experiments" not in result.output
    assert "Generate reports" not in result.output
    assert "Reports" in result.output
    assert (directory / "reports" / "finding-1.html").is_file()


def test_run_creates_missing_requested_capture_instead_of_reusing_latest(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    directory = _seed_project(tmp_path, monkeypatch)
    discovered: list[tuple[str, str | None]] = []
    proxy_calls: list[tuple[str, str | None]] = []

    def fake_record_proxy_capture(project: str, capture_id: str | None = None) -> CapturedTrace:
        proxy_calls.append((project, capture_id))
        return CapturedTrace(
            capture_id=capture_id or "proxy-cap",
            source="proxy",
            project=project,
        )

    def fake_discovery(project: str, capture_id: str | None = None) -> object:
        discovered.append((project, capture_id))
        return object()

    monkeypatch.setattr(wizard_module, "record_proxy_capture", fake_record_proxy_capture)
    monkeypatch.setattr(wizard_module, "run_project_discovery", fake_discovery)
    monkeypatch.setattr(
        wizard_module,
        "print_discovery_summary",
        lambda result: None,
    )

    result = runner.invoke(
        cli_app,
        [
            _one_click_entry(),
            "--project",
            "demo",
            "--capture-id",
            "new-cap",
            _discovery_only_flag(),
        ],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert "Capture 'new-cap' was not found" in result.output
    assert "Latest capture 'capture-1' exists" in result.output
    assert proxy_calls == [("demo", "new-cap")]
    assert discovered == [("demo", "new-cap")]
    store = ArtifactStore(directory)
    try:
        assert store.list_ids("captures") == ["capture-1", "new-cap"]
    finally:
        store.close()


def test_wizard_creates_project_and_imports_capture(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    capture_file = tmp_path / "normal.har"
    capture_file.write_text(
        json.dumps(
            {
                "log": {
                    "version": "1.2",
                    "entries": [
                        {
                            "request": {
                                "method": "GET",
                                "url": "http://127.0.0.1:8080/status",
                                "headers": [],
                            },
                            "response": {
                                "status": 200,
                                "headers": [],
                                "content": {},
                            },
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    discovered: list[tuple[str, str | None]] = []

    def fake_discovery(project: str, capture_id: str | None = None) -> object:
        discovered.append((project, capture_id))
        return object()

    monkeypatch.setattr(wizard_module, "run_project_discovery", fake_discovery)
    monkeypatch.setattr(
        wizard_module,
        "print_discovery_summary",
        lambda result: None,
    )

    result = runner.invoke(
        cli_app,
        [
            "wizard",
            "--project",
            "new-demo",
            "--base-url",
            "http://127.0.0.1:8080",
            "--skip-scan",
        ],
        input=f"n\n{capture_file}\n",
    )

    assert result.exit_code == 0, result.output
    assert discovered == [("new-demo", "normal")]
    assert "Created project" in result.output
    assert "No captures found; import a normal-flow trace." in result.output
    assert "Record through the local HTTP proxy" in result.output
    assert "Imported 1 exchanges as capture 'normal'." in result.output
    project_dir = tmp_path / ".statebreaker" / "projects" / "new-demo"
    assert (project_dir / "project.yaml").is_file()
    store = ArtifactStore(project_dir)
    try:
        assert store.list_ids("captures") == ["normal"]
    finally:
        store.close()


def test_run_creates_project_and_imports_capture_for_discovery_only(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    capture_file = tmp_path / "normal.har"
    capture_file.write_text(
        json.dumps(
            {
                "log": {
                    "version": "1.2",
                    "entries": [
                        {
                            "request": {
                                "method": "GET",
                                "url": "http://127.0.0.1:8080/status",
                                "headers": [],
                            },
                            "response": {
                                "status": 200,
                                "headers": [],
                                "content": {},
                            },
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    discovered: list[tuple[str, str | None]] = []

    def fake_discovery(project: str, capture_id: str | None = None) -> object:
        discovered.append((project, capture_id))
        return object()

    monkeypatch.setattr(wizard_module, "run_project_discovery", fake_discovery)
    monkeypatch.setattr(
        wizard_module,
        "print_discovery_summary",
        lambda result: None,
    )

    discovery_only_flag = _discovery_only_flag()
    result = runner.invoke(
        cli_app,
        [
            _one_click_entry(),
            "--project",
            "new-demo",
            "--base-url",
            "http://127.0.0.1:8080",
            "--capture-file",
            str(capture_file),
            discovery_only_flag,
        ],
    )

    assert result.exit_code == 0, result.output
    assert discovered == [("new-demo", "normal")]
    assert "Created project" in result.output
    assert "Imported 1 exchanges as capture 'normal'." in result.output
    assert f"Stopped after discovery preview ({discovery_only_flag})." in result.output
    project_dir = tmp_path / ".statebreaker" / "projects" / "new-demo"
    assert (project_dir / "project.yaml").is_file()


def test_wizard_uses_capture_id_for_interactive_import(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    create_project("demo", "http://127.0.0.1:8080")
    capture_file = tmp_path / "normal.har"
    capture_file.write_text(
        json.dumps(
            {
                "log": {
                    "version": "1.2",
                    "entries": [
                        {
                            "request": {
                                "method": "GET",
                                "url": "http://127.0.0.1:8080/status",
                                "headers": [],
                            },
                            "response": {
                                "status": 200,
                                "headers": [],
                                "content": {},
                            },
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    discovered: list[tuple[str, str | None]] = []

    def fake_discovery(project: str, capture_id: str | None = None) -> object:
        discovered.append((project, capture_id))
        return object()

    monkeypatch.setattr(wizard_module, "run_project_discovery", fake_discovery)
    monkeypatch.setattr(
        wizard_module,
        "print_discovery_summary",
        lambda result: None,
    )

    result = runner.invoke(
        cli_app,
        [
            "wizard",
            "--project",
            "demo",
            "--capture-id",
            "custom-cap",
            "--skip-scan",
        ],
        input=f"n\n{capture_file}\n",
    )

    assert result.exit_code == 0, result.output
    assert discovered == [("demo", "custom-cap")]
    assert "Capture 'custom-cap' was not found" in result.output
    assert "Imported 1 exchanges as capture 'custom-cap'." in result.output
    store = ArtifactStore(tmp_path / ".statebreaker" / "projects" / "demo")
    try:
        assert store.list_ids("captures") == ["custom-cap"]
    finally:
        store.close()


def test_wizard_lists_projects_and_respects_scan_rejection(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    _seed_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        wizard_module,
        "run_project_discovery",
        lambda project, capture_id=None: object(),
    )
    monkeypatch.setattr(
        wizard_module,
        "print_discovery_summary",
        lambda result: None,
    )

    def unexpected_scan(project: str, capture_id: str | None = None) -> ScanOutcome:
        raise AssertionError("scan must not run after the user rejects confirmation")

    monkeypatch.setattr(wizard_module, "run_project_scan", unexpected_scan)

    result = runner.invoke(cli_app, ["wizard"], input="1\ny\nn\n")

    assert result.exit_code == 0, result.output
    assert "Available projects:" in result.output
    assert "Latest capture: 'capture-1'" in result.output
    assert "Scan cancelled; discovery artifacts were kept." in result.output


def test_wizard_can_record_capture_through_proxy(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    directory = _seed_project(tmp_path, monkeypatch)
    calls: list[tuple[str, str | None]] = []

    def fake_record_proxy_capture(project: str, capture_id: str | None = None) -> CapturedTrace:
        calls.append((project, capture_id))
        return CapturedTrace(
            capture_id="proxy-cap",
            source="proxy",
            project=project,
        )

    monkeypatch.setattr(wizard_module, "record_proxy_capture", fake_record_proxy_capture)
    monkeypatch.setattr(
        wizard_module,
        "run_project_discovery",
        lambda project, capture_id=None: object(),
    )
    monkeypatch.setattr(
        wizard_module,
        "print_discovery_summary",
        lambda result: None,
    )

    result = runner.invoke(
        cli_app,
        [
            "wizard",
            "--project",
            "demo",
            "--capture-id",
            "proxy-cap",
            "--proxy-capture",
            "--skip-scan",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [("demo", "proxy-cap")]
    assert "Recorded 0 exchanges as capture 'proxy-cap'." in result.output
    store = ArtifactStore(directory)
    try:
        trace = store.load("captures", "proxy-cap", CapturedTrace)
    finally:
        store.close()
    assert trace.source == "proxy"


def test_wizard_can_record_capture_through_browser(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    directory = _seed_project(tmp_path, monkeypatch)
    calls: list[tuple[str, str | None, str | None]] = []

    def fake_record_browser_capture(
        project: str,
        *,
        capture_id: str | None = None,
        start_url: str | None = None,
        browser_path: str | None = None,
        fresh_profile: bool = False,
        max_exchanges: int | None = None,
    ) -> CapturedTrace:
        assert browser_path is None
        assert fresh_profile is False
        assert max_exchanges is None
        calls.append((project, capture_id, start_url))
        return CapturedTrace(
            capture_id="browser-cap",
            source="browser",
            project=project,
            base_url=start_url,
        )

    monkeypatch.setattr(wizard_module, "record_browser_capture", fake_record_browser_capture)
    monkeypatch.setattr(
        wizard_module,
        "run_project_discovery",
        lambda project, capture_id=None: object(),
    )
    monkeypatch.setattr(
        wizard_module,
        "print_discovery_summary",
        lambda result: None,
    )

    result = runner.invoke(
        cli_app,
        [
            "wizard",
            "--project",
            "demo",
            "--capture-id",
            "browser-cap",
            "--browser-capture",
            "--skip-scan",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [("demo", "browser-cap", "http://127.0.0.1:8080")]
    assert "Recorded 0 exchanges as capture 'browser-cap'." in result.output
    store = ArtifactStore(directory)
    try:
        trace = store.load("captures", "browser-cap", CapturedTrace)
    finally:
        store.close()
    assert trace.source == "browser"


def test_proxy_setup_instructions_explain_local_safety(capsys) -> None:  # type: ignore[no-untyped-def]
    capture_module._print_proxy_setup_instructions(  # noqa: SLF001
        "127.0.0.1",
        8088,
        public_bind=False,
    )

    output = capsys.readouterr().out
    assert "HTTP proxy listening on 127.0.0.1:8088" in output
    assert "loopback-only" in output
    assert "will not change OS or browser proxy settings" in output
    assert "authorized normal flow" in output
    assert "HTTPS CONNECT is tunneled" in output
    assert "not recorded" in output


def test_proxy_capture_summary_includes_methods_statuses_and_hosts() -> None:
    trace = CapturedTrace(
        capture_id="proxy-cap",
        source="proxy",
        project="demo",
        exchanges=[
            HttpExchange(
                exchange_id="proxy-1",
                method="GET",
                url="http://127.0.0.1:8080/status",
                response_status=200,
            ),
            HttpExchange(
                exchange_id="proxy-2",
                method="POST",
                url="http://127.0.0.1:8080/items",
                response_status=201,
            ),
            HttpExchange(
                exchange_id="proxy-3",
                method="POST",
                url="http://api.example.test/items",
                response_status=500,
            ),
        ],
    )

    lines = capture_module.format_proxy_capture_summary(trace)

    assert lines[0] == "recorded 3 exchanges as capture 'proxy-cap'."
    assert "Recording stopped: captured 3 HTTP exchange(s)." in lines
    assert any(line.startswith("Methods: GET=1, POST=2") for line in lines)
    assert any(line.startswith("Statuses: 2xx=2, 5xx=1") for line in lines)
    assert any(line.startswith("Hosts: 127.0.0.1:8080, api.example.test") for line in lines)


def test_wizard_reports_missing_capture_file(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    _seed_project(tmp_path, monkeypatch)
    missing = tmp_path / "missing.har"

    result = runner.invoke(
        cli_app,
        [
            "wizard",
            "--project",
            "demo",
            "--capture-file",
            str(missing),
            "--skip-scan",
        ],
    )

    assert result.exit_code == 2
    assert "error: cannot read" in result.output
    assert "missing.har" in result.output


def test_capture_proxy_command_saves_recorded_trace(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    directory = _seed_project(tmp_path, monkeypatch)

    def fake_record_proxy_capture(
        project: str,
        *,
        capture_id: str | None = None,
        listen_host: str = "127.0.0.1",
        listen_port: int = 8088,
        max_exchanges: int | None = None,
        allow_public_proxy: bool = False,
    ) -> CapturedTrace:
        assert project == "demo"
        assert capture_id == "proxy-cap"
        assert listen_host == "127.0.0.1"
        assert listen_port == 0
        assert max_exchanges == 1
        assert not allow_public_proxy
        return CapturedTrace(
            capture_id="proxy-cap",
            source="proxy",
            project="demo",
        )

    monkeypatch.setattr(capture_module, "record_proxy_capture", fake_record_proxy_capture)

    result = runner.invoke(
        cli_app,
        [
            "capture",
            "proxy",
            "--project",
            "demo",
            "--capture-id",
            "proxy-cap",
            "--listen-port",
            "0",
            "--max-exchanges",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "recorded 0 exchanges as capture 'proxy-cap'" in result.output
    store = ArtifactStore(directory)
    try:
        trace = store.load("captures", "proxy-cap", CapturedTrace)
    finally:
        store.close()
    assert trace.source == "proxy"


def test_capture_browser_command_saves_recorded_trace(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    directory = _seed_project(tmp_path, monkeypatch)

    def fake_record_browser_capture(
        project: str,
        *,
        capture_id: str | None = None,
        start_url: str | None = None,
        browser_path: str | None = None,
        fresh_profile: bool = False,
        max_exchanges: int | None = None,
    ) -> CapturedTrace:
        assert project == "demo"
        assert capture_id == "browser-cap"
        assert start_url == "https://example.test"
        assert browser_path == "C:\\Browser\\msedge.exe"
        assert fresh_profile is False
        assert max_exchanges == 1
        return CapturedTrace(
            capture_id="browser-cap",
            source="browser",
            project="demo",
            base_url=start_url,
        )

    monkeypatch.setattr(capture_module, "record_browser_capture", fake_record_browser_capture)

    result = runner.invoke(
        cli_app,
        [
            "capture",
            "browser",
            "--project",
            "demo",
            "--capture-id",
            "browser-cap",
            "--url",
            "https://example.test",
            "--browser-path",
            "C:\\Browser\\msedge.exe",
            "--max-exchanges",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "recorded 0 exchanges as capture 'browser-cap'" in result.output
    store = ArtifactStore(directory)
    try:
        trace = store.load("captures", "browser-cap", CapturedTrace)
    finally:
        store.close()
    assert trace.source == "browser"


def test_capture_proxy_rejects_public_bind_without_unsafe_flag(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    _seed_project(tmp_path, monkeypatch)

    def unexpected_record_proxy_capture(
        project: str,
        *,
        capture_id: str | None = None,
        listen_host: str = "127.0.0.1",
        listen_port: int = 8088,
        max_exchanges: int | None = None,
        allow_public_proxy: bool = False,
    ) -> CapturedTrace:
        raise AssertionError("public proxy must be rejected before recording starts")

    monkeypatch.setattr(capture_module, "record_proxy_capture", unexpected_record_proxy_capture)

    result = runner.invoke(
        cli_app,
        [
            "capture",
            "proxy",
            "--project",
            "demo",
            "--listen-host",
            "0.0.0.0",
            "--max-exchanges",
            "1",
        ],
    )

    assert result.exit_code == 2
    assert "refusing to bind unauthenticated proxy" in result.output
    assert "--unsafe-public-proxy" in result.output


def test_capture_proxy_allows_public_bind_with_unsafe_flag(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    directory = _seed_project(tmp_path, monkeypatch)

    def fake_record_proxy_capture(
        project: str,
        *,
        capture_id: str | None = None,
        listen_host: str = "127.0.0.1",
        listen_port: int = 8088,
        max_exchanges: int | None = None,
        allow_public_proxy: bool = False,
    ) -> CapturedTrace:
        assert project == "demo"
        assert listen_host == "0.0.0.0"
        assert listen_port == 8088
        assert max_exchanges == 1
        assert allow_public_proxy
        return CapturedTrace(
            capture_id="proxy-public",
            source="proxy",
            project="demo",
        )

    monkeypatch.setattr(capture_module, "record_proxy_capture", fake_record_proxy_capture)

    result = runner.invoke(
        cli_app,
        [
            "capture",
            "proxy",
            "--project",
            "demo",
            "--listen-host",
            "0.0.0.0",
            "--unsafe-public-proxy",
            "--max-exchanges",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "recorded 0 exchanges as capture 'proxy-public'" in result.output
    store = ArtifactStore(directory)
    try:
        trace = store.load("captures", "proxy-public", CapturedTrace)
    finally:
        store.close()
    assert trace.source == "proxy"
