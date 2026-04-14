"""SQLite database manager for git-aftermerge."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from git_aftermerge.storage.models import (
    CommitFate,
    DownstreamEvent,
    EventType,
    Fate,
    PatternEntry,
)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Use as context manager or call connect().")
        return self._conn

    def initialize_schema(self) -> None:
        schema = SCHEMA_PATH.read_text()
        self.conn.executescript(schema)
        self.conn.commit()

    # --- scan_meta ---

    def get_last_scan(self) -> Optional[tuple[str, datetime]]:
        row = self.conn.execute(
            "SELECT last_scanned_sha, last_scan_time FROM scan_meta ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row and row["last_scanned_sha"]:
            return row["last_scanned_sha"], datetime.fromisoformat(row["last_scan_time"])
        return None

    def update_scan_meta(self, sha: str, repo_path: str) -> None:
        now = datetime.utcnow().isoformat()
        existing = self.conn.execute("SELECT id FROM scan_meta LIMIT 1").fetchone()
        if existing:
            self.conn.execute(
                "UPDATE scan_meta SET last_scanned_sha=?, last_scan_time=?, repo_path=? WHERE id=?",
                (sha, now, repo_path, existing["id"]),
            )
        else:
            self.conn.execute(
                "INSERT INTO scan_meta"
                " (last_scanned_sha, last_scan_time, repo_path)"
                " VALUES (?,?,?)",
                (sha, now, repo_path),
            )
        self.conn.commit()

    # --- commit_fates ---

    def upsert_commit_fate(self, fate: CommitFate) -> None:
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """INSERT INTO commit_fates
               (commit_sha, author, author_tool, author_model, merged_at,
                original_lines_added, original_lines_modified, surviving_lines,
                survival_score, fate, commit_type, ai_attributed, file_paths_json,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(commit_sha) DO UPDATE SET
                 author=excluded.author,
                 author_tool=excluded.author_tool,
                 author_model=excluded.author_model,
                 merged_at=excluded.merged_at,
                 original_lines_added=excluded.original_lines_added,
                 original_lines_modified=excluded.original_lines_modified,
                 surviving_lines=excluded.surviving_lines,
                 survival_score=excluded.survival_score,
                 fate=excluded.fate,
                 commit_type=excluded.commit_type,
                 ai_attributed=excluded.ai_attributed,
                 file_paths_json=excluded.file_paths_json,
                 updated_at=excluded.updated_at
            """,
            (
                fate.commit_sha,
                fate.author,
                fate.author_tool,
                fate.author_model,
                fate.merged_at.isoformat(),
                fate.original_lines_added,
                fate.original_lines_modified,
                fate.surviving_lines,
                fate.survival_score,
                fate.fate.value,
                fate.commit_type,
                int(fate.ai_attributed),
                json.dumps(fate.file_paths),
                now,
                now,
            ),
        )

    def get_commit_fate(self, commit_sha: str) -> Optional[CommitFate]:
        row = self.conn.execute(
            "SELECT * FROM commit_fates WHERE commit_sha=?", (commit_sha,)
        ).fetchone()
        if not row:
            return None
        fate = self._row_to_commit_fate(row)
        fate.downstream_events = self.get_downstream_events(commit_sha)
        return fate

    def get_all_commit_fates(
        self,
        since: Optional[datetime] = None,
        path_filter: Optional[str] = None,
        author_filter: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[CommitFate]:
        query = "SELECT * FROM commit_fates WHERE 1=1"
        params: list = []
        if since:
            query += " AND merged_at >= ?"
            params.append(since.isoformat())
        if author_filter:
            query += " AND author LIKE ?"
            params.append(f"%{author_filter}%")
        query += " ORDER BY merged_at DESC"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = self.conn.execute(query, params).fetchall()
        if not rows:
            return []

        # Batch-fetch all downstream events in one query
        shas = [row["commit_sha"] for row in rows]
        events_map = self._get_batch_downstream_events(shas)

        fates = []
        for row in rows:
            fate = self._row_to_commit_fate(row)
            fate.downstream_events = events_map.get(fate.commit_sha, [])
            if path_filter:
                if not any(p.startswith(path_filter) for p in fate.file_paths):
                    continue
            fates.append(fate)
        return fates

    def _get_batch_downstream_events(self, shas: list[str]) -> dict[str, list[DownstreamEvent]]:
        """Fetch downstream events for multiple commits in one query."""
        if not shas:
            return {}
        events_map: dict[str, list[DownstreamEvent]] = {}
        # Chunk to stay under SQLite's 999 variable limit
        for i in range(0, len(shas), 900):
            chunk = shas[i : i + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT * FROM downstream_events WHERE source_commit_sha IN ({placeholders}) "
                "ORDER BY event_date",
                chunk,
            ).fetchall()
            for r in rows:
                event = DownstreamEvent(
                    event_type=EventType(r["event_type"]),
                    commit_sha=r["event_commit_sha"],
                    date=datetime.fromisoformat(r["event_date"]),
                    lines_affected=r["lines_affected"],
                    commit_message=r["commit_message"] or "",
                    author=r["author"] or "",
                )
                events_map.setdefault(r["source_commit_sha"], []).append(event)
        return events_map

    def _row_to_commit_fate(self, row: sqlite3.Row) -> CommitFate:
        file_paths = json.loads(row["file_paths_json"]) if row["file_paths_json"] else []
        return CommitFate(
            commit_sha=row["commit_sha"],
            author=row["author"],
            author_tool=row["author_tool"],
            author_model=row["author_model"],
            merged_at=datetime.fromisoformat(row["merged_at"]),
            original_lines_added=row["original_lines_added"],
            original_lines_modified=row["original_lines_modified"],
            surviving_lines=row["surviving_lines"],
            survival_score=row["survival_score"],
            fate=Fate(row["fate"]),
            days_since_merge=(datetime.utcnow() - datetime.fromisoformat(row["merged_at"])).days,
            file_paths=file_paths,
            commit_type=row["commit_type"],
            ai_attributed=bool(row["ai_attributed"]),
        )

    # --- downstream_events ---

    def insert_downstream_event(self, source_sha: str, event: DownstreamEvent) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO downstream_events
               (source_commit_sha, event_type, event_commit_sha, event_date,
                lines_affected, commit_message, author)
               VALUES (?,?,?,?,?,?,?)
            """,
            (
                source_sha,
                event.event_type.value,
                event.commit_sha,
                event.date.isoformat(),
                event.lines_affected,
                event.commit_message,
                event.author,
            ),
        )

    def get_downstream_events(self, source_sha: str) -> list[DownstreamEvent]:
        rows = self.conn.execute(
            "SELECT * FROM downstream_events WHERE source_commit_sha=? ORDER BY event_date",
            (source_sha,),
        ).fetchall()
        return [
            DownstreamEvent(
                event_type=EventType(r["event_type"]),
                commit_sha=r["event_commit_sha"],
                date=datetime.fromisoformat(r["event_date"]),
                lines_affected=r["lines_affected"],
                commit_message=r["commit_message"] or "",
                author=r["author"] or "",
            )
            for r in rows
        ]

    def delete_downstream_events(self, source_sha: str) -> None:
        self.conn.execute(
            "DELETE FROM downstream_events WHERE source_commit_sha=?", (source_sha,)
        )

    # --- patterns ---

    def upsert_pattern(self, pattern: PatternEntry) -> None:
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """INSERT INTO patterns
               (dimension, key, avg_score, commit_count,
                revert_count, bug_fix_count, computed_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(dimension, key) DO UPDATE SET
                 avg_score=excluded.avg_score,
                 commit_count=excluded.commit_count,
                 revert_count=excluded.revert_count,
                 bug_fix_count=excluded.bug_fix_count,
                 computed_at=excluded.computed_at
            """,
            (
                pattern.dimension,
                pattern.key,
                pattern.avg_score,
                pattern.commit_count,
                pattern.revert_count,
                pattern.bug_fix_count,
                now,
            ),
        )

    def get_patterns(self, dimension: Optional[str] = None) -> list[PatternEntry]:
        if dimension:
            rows = self.conn.execute(
                "SELECT * FROM patterns WHERE dimension=? ORDER BY avg_score", (dimension,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM patterns ORDER BY dimension, avg_score"
            ).fetchall()
        return [
            PatternEntry(
                dimension=r["dimension"],
                key=r["key"],
                avg_score=r["avg_score"],
                commit_count=r["commit_count"],
                revert_count=r["revert_count"],
                bug_fix_count=r["bug_fix_count"],
            )
            for r in rows
        ]

    def commit_exists(self, sha: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM commit_fates WHERE commit_sha=?", (sha,)
        ).fetchone()
        return row is not None
