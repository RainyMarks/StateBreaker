"""SQLite index over stored artifacts: fast lookup and scan-state queries."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from statebreaker.models.base import utc_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    kind TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (kind, artifact_id)
);
"""


class ArtifactIndex:
    """Thin SQLite registry; JSON files remain the portable source of truth."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._connection = sqlite3.connect(str(db_path))
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def register(self, kind: str, artifact_id: str, path: Path, summary: str = "") -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO artifacts (kind, artifact_id, path, created_at, summary)"
            " VALUES (?, ?, ?, ?, ?)",
            (kind, artifact_id, str(path), utc_now().isoformat(), summary),
        )
        self._connection.commit()

    def list_ids(self, kind: str) -> list[str]:
        cursor = self._connection.execute(
            "SELECT artifact_id FROM artifacts WHERE kind = ? ORDER BY created_at", (kind,)
        )
        return [row[0] for row in cursor.fetchall()]

    def path_for(self, kind: str, artifact_id: str) -> Path | None:
        cursor = self._connection.execute(
            "SELECT path FROM artifacts WHERE kind = ? AND artifact_id = ?",
            (kind, artifact_id),
        )
        row = cursor.fetchone()
        return Path(row[0]) if row else None

    def close(self) -> None:
        self._connection.close()
