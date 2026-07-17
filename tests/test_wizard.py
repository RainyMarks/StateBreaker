from __future__ import annotations

from pathlib import Path

from statebreaker.wizard import State, _init, _probe_lab, _sync_workflow


def test_init_creates_work_dir() -> None:
    root = Path(__file__).resolve().parents[1]
    state = _init(root)
    assert state.work.is_dir()
    assert state.workflow.name == "workflow.yaml"


def test_sync_workflow_rewrites_base_url(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    state = State(
        root=root,
        work=tmp_path,
        workflow=root / "examples" / "coupon-race" / "workflow.yaml",
        invariants=root / "examples" / "coupon-race" / "invariants.yaml",
        lab="http://127.0.0.1:18080",
    )
    _sync_workflow(state)
    assert "18080" in state.workflow.read_text(encoding="utf-8")
    assert state.workflow.name == "workflow.json"


def test_probe_lab_returns_none_or_http_url() -> None:
    result = _probe_lab()
    assert result is None or result.startswith("http://127.0.0.1:")
