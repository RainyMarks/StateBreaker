from __future__ import annotations

from pathlib import Path

from statebreaker.wizard import WizardState, _default_paths, _ensure_workflow_for_lab, _probe_lab


def test_default_paths_creates_work_dir() -> None:
    # Use repo root so examples exist when run from project tests.
    root = Path(__file__).resolve().parents[1]
    state = _default_paths(root)
    assert state.work_dir.is_dir()
    assert state.workflow_path.name == "workflow.yaml"


def test_ensure_workflow_rewrites_base_url(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    state = WizardState(
        root=root,
        work_dir=tmp_path,
        workflow_path=root / "examples" / "coupon-race" / "workflow.yaml",
        invariants_path=root / "examples" / "coupon-race" / "invariants.yaml",
        lab_base_url="http://127.0.0.1:18080",
    )
    _ensure_workflow_for_lab(state)
    text = state.workflow_path.read_text(encoding="utf-8")
    assert "18080" in text
    assert state.workflow_path.name == "workflow.json"


def test_probe_lab_returns_none_or_url() -> None:
    result = _probe_lab(ports=[59999])
    assert result is None
