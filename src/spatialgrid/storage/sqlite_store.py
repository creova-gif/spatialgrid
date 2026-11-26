"""SQLite-backed durable storage for SpatialGrid facilities."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from spatialgrid.models import Facility

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facilities (
    org_id TEXT NOT NULL,
    facility_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (org_id, facility_id)
);
CREATE INDEX IF NOT EXISTS idx_facilities_org ON facilities (org_id);
"""


class SQLiteFacilityStore:
    """Thread-safe SQLite store (WAL mode) for tenant-isolated Facility persistence."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()

    def upsert_facilities(self, facilities: list[Facility]) -> int:
        """Transactionally upsert facilities into SQLite and return the count written."""
        if not facilities:
            return 0

        updated_at = datetime.now(UTC).isoformat()
        rows = [
            (fac.org_id, fac.facility_id, fac.model_dump_json(), updated_at)
            for fac in facilities
        ]
        with self._lock, self._conn:
            self._conn.executemany(
                """
                INSERT INTO facilities (org_id, facility_id, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(org_id, facility_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
        return len(facilities)

    def delete_facility(self, org_id: str, facility_id: str) -> bool:
        """Delete a facility by (org_id, facility_id) and return True if a row was removed."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM facilities WHERE org_id = ? AND facility_id = ?",
                (org_id, facility_id),
            )
        return cur.rowcount > 0

    def load_all_facilities(self) -> list[Facility]:
        """Load and validate all persisted facilities from SQLite."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT payload_json FROM facilities ORDER BY org_id, facility_id"
            )
            rows = cur.fetchall()
        return [Facility.model_validate_json(row[0]) for row in rows]

    def count_by_org(self) -> dict[str, int]:
        """Return persisted facility counts keyed by org_id."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT org_id, COUNT(*) FROM facilities GROUP BY org_id"
            )
            rows = cur.fetchall()
        return {str(row[0]): int(row[1]) for row in rows}
