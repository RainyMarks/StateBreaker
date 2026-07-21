"""Scan checkpoints: persist stage progress so interrupted scans can resume."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from statebreaker.artifacts.store import ArtifactStore
from statebreaker.models.base import ContractModel, utc_now


class ScanCheckpoint(ContractModel):
    """Progress marker written after each completed orchestration stage."""

    scan_id: str
    stage: str
    completed_stages: list[str] = Field(default_factory=list)
    artifact_refs: dict[str, Any] = Field(default_factory=dict)
    saved_at: str = Field(default_factory=lambda: utc_now().isoformat())


def save_checkpoint(store: ArtifactStore, checkpoint: ScanCheckpoint) -> None:
    store.save("checkpoints", checkpoint.scan_id, checkpoint, summary=checkpoint.stage)


def load_checkpoint(store: ArtifactStore, scan_id: str) -> ScanCheckpoint | None:
    if not store.exists("checkpoints", scan_id):
        return None
    return store.load("checkpoints", scan_id, ScanCheckpoint)


__all__ = ["ScanCheckpoint", "save_checkpoint", "load_checkpoint"]
