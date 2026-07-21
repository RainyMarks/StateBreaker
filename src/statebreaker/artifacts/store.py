"""Filesystem artifact store: JSON evidence per project, indexed by SQLite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from statebreaker.artifacts.index import ArtifactIndex
from statebreaker.errors import ArtifactError
from statebreaker.models.base import ContractModel

ModelT = TypeVar("ModelT", bound=ContractModel)

ARTIFACT_KINDS = (
    "captures",
    "workflows",
    "graphs",
    "baselines",
    "candidates",
    "plans",
    "trials",
    "findings",
    "bundles",
    "reports",
    "checkpoints",
    "scans",
)


class ArtifactStore:
    """Rooted at ``.statebreaker/projects/<project>/``.

    Every artifact is a versioned JSON document on disk (portable evidence);
    SQLite only indexes kind/id/path for lookup and resumability.
    """

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self.project_dir.mkdir(parents=True, exist_ok=True)
        for kind in ARTIFACT_KINDS:
            (self.project_dir / kind).mkdir(exist_ok=True)
        self.index = ArtifactIndex(self.project_dir / "index.sqlite3")

    @staticmethod
    def default_project_dir(workspace: str | Path, project: str) -> Path:
        return Path(workspace) / ".statebreaker" / "projects" / project

    def _path(self, kind: str, artifact_id: str) -> Path:
        if kind not in ARTIFACT_KINDS:
            raise ArtifactError(f"unknown artifact kind: {kind!r}")
        safe_id = artifact_id.replace("/", "_").replace("\\", "_")
        return self.project_dir / kind / f"{safe_id}.json"

    def save(self, kind: str, artifact_id: str, model: ContractModel, summary: str = "") -> Path:
        path = self._path(kind, artifact_id)
        path.write_text(model.to_json(), encoding="utf-8")
        self.index.register(kind, artifact_id, path, summary)
        return path

    def save_raw(self, kind: str, artifact_id: str, payload: dict[str, object]) -> Path:
        path = self._path(kind, artifact_id)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        self.index.register(kind, artifact_id, path)
        return path

    def load(self, kind: str, artifact_id: str, model_type: type[ModelT]) -> ModelT:
        path = self._path(kind, artifact_id)
        if not path.exists():
            raise ArtifactError(f"artifact not found: {kind}/{artifact_id}")
        return model_type.from_json(path.read_text(encoding="utf-8"))

    def exists(self, kind: str, artifact_id: str) -> bool:
        return self._path(kind, artifact_id).exists()

    def list_ids(self, kind: str) -> list[str]:
        return self.index.list_ids(kind)

    def close(self) -> None:
        self.index.close()
