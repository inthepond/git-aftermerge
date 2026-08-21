"""SQLite database manager for git-aftermerge.

Persists facts only (see schema.sql). The read methods assemble derived
views — ``CommitFate`` with its score and fate label, survival-curve rows —
from those facts at query time, so scoring rules can change without a rescan.
"""

import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from git_aftermerge.storage.models import (
    CHECKPOINT_DAYS,
    HEAD_LABEL,
    Cohort,
    CommitFact,
    CommitFate,
    DownstreamEvent,
    EventType,
    Fate,
    FileChange,
    MaturityTier,
    SurvivalObservation,
    checkpoint_label,
    maturity_of,
)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SCHEMA_VERSION = 2

# Tables from any schema version, used when rebuilding an outdated database.
_ALL_TABLES = (
    "commit_fates", "downstream_events", "patterns",  # v1
    "commits", "commit_files", "commit_links", "survival_observations",  # v2
    "scan_meta",
)


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
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            # Facts are fully rebuildable from git history, so an outdated
            # database is dropped and repopulated by the next full scan.
            for table in _ALL_TABLES:
                self.conn.execute(f"DROP TABLE IF EXISTS {table}")
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

    # --- commit facts ---

    def upsert_commit_fact(self, fact: CommitFact) -> None:
        self.conn.execute(
            """INSERT INTO commits
               (commit_sha, author_name, author_email, authored_at, message_subject,
                cohort, author_tool, author_model, ai_session, human_ratio,
                attribution_source, lines_added, lines_deleted, recorded_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(commit_sha) DO UPDATE SET
                 author_name=excluded.author_name,
                 author_email=excluded.author_email,
                 authored_at=excluded.authored_at,
                 message_subject=excluded.message_subject,
                 cohort=excluded.cohort,
                 author_tool=excluded.author_tool,
                 author_model=excluded.author_model,
                 ai_session=excluded.ai_session,
                 human_ratio=excluded.human_ratio,
                 attribution_source=excluded.attribution_source,
                 lines_added=excluded.lines_added,
                 lines_deleted=excluded.lines_deleted,
                 recorded_at=excluded.recorded_at
            """,
            (
                fact.sha,
                fact.author_name,
                fact.author_email,
                fact.authored_at.isoformat(),
                fact.subject,
                fact.cohort,
                fact.author_tool,
                fact.author_model,
                fact.ai_session,
                fact.human_ratio,
                fact.attribution_source,
                fact.lines_added,
                fact.lines_deleted,
                datetime.utcnow().isoformat(),
            ),
        )
        self.conn.execute("DELETE FROM commit_files WHERE commit_sha=?", (fact.sha,))
        for f in fact.files:
            self.conn.execute(
                "INSERT INTO commit_files"
                " (commit_sha, path, lines_added, lines_deleted, prev_touch_at)"
                " VALUES (?,?,?,?,?)",
                (
                    fact.sha,
                    f.path,
                    f.lines_added,
                    f.lines_deleted,
                    f.prev_touch_at.isoformat() if f.prev_touch_at else None,
                ),
            )

    def commit_exists(self, sha: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM commits WHERE commit_sha=?", (sha,)
        ).fetchone()
        return row is not None

    def get_commit_facts(self) -> list[CommitFact]:
        rows = self.conn.execute("SELECT * FROM commits ORDER BY authored_at").fetchall()
        files_map: dict[str, list[FileChange]] = defaultdict(list)
        for r in self.conn.execute("SELECT * FROM commit_files").fetchall():
            files_map[r["commit_sha"]].append(_row_to_file_change(r))
        facts = []
        for row in rows:
            facts.append(CommitFact(
                sha=row["commit_sha"],
                author_name=row["author_name"],
                author_email=row["author_email"],
                authored_at=datetime.fromisoformat(row["authored_at"]),
                subject=row["message_subject"],
                cohort=row["cohort"],
                author_tool=row["author_tool"],
                author_model=row["author_model"],
                ai_session=row["ai_session"],
                human_ratio=row["human_ratio"],
                attribution_source=row["attribution_source"],
                lines_added=row["lines_added"],
                lines_deleted=row["lines_deleted"],
                files=files_map.get(row["commit_sha"], []),
            ))
        return facts

    # --- links (facts about detected relationships) ---

    def insert_link(self, source_sha: str, event: DownstreamEvent, detector: str = "v1") -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO commit_links
               (source_commit_sha, link_type, event_commit_sha, event_date,
                lines_affected, commit_message, author, detector)
               VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                source_sha,
                event.event_type.value,
                event.commit_sha,
                event.date.isoformat(),
                event.lines_affected,
                event.commit_message,
                event.author,
                detector,
            ),
        )

    def delete_links(self, source_sha: str) -> None:
        self.conn.execute(
            "DELETE FROM commit_links WHERE source_commit_sha=?", (source_sha,)
        )

    def get_links(self, source_sha: str) -> list[DownstreamEvent]:
        rows = self.conn.execute(
            "SELECT * FROM commit_links WHERE source_commit_sha=? ORDER BY event_date",
            (source_sha,),
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    # Backward-compatible aliases (v1 naming).
    insert_downstream_event = insert_link
    delete_downstream_events = delete_links
    get_downstream_events = get_links

    def _get_batch_links(self, shas: list[str]) -> dict[str, list[DownstreamEvent]]:
        if not shas:
            return {}
        events_map: dict[str, list[DownstreamEvent]] = {}
        # Chunk to stay under SQLite's 999 variable limit
        for i in range(0, len(shas), 900):
            chunk = shas[i : i + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT * FROM commit_links WHERE source_commit_sha IN ({placeholders}) "
                "ORDER BY event_date",
                chunk,
            ).fetchall()
            for r in rows:
                events_map.setdefault(r["source_commit_sha"], []).append(_row_to_event(r))
        return events_map

    # --- survival observations ---

    def upsert_observation(self, obs: SurvivalObservation) -> None:
        self.conn.execute(
            """INSERT INTO survival_observations
               (commit_sha, label, checkpoint_days, observed_sha, observed_at, surviving_lines)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(commit_sha, label) DO UPDATE SET
                 checkpoint_days=excluded.checkpoint_days,
                 observed_sha=excluded.observed_sha,
                 observed_at=excluded.observed_at,
                 surviving_lines=excluded.surviving_lines
            """,
            (
                obs.commit_sha,
                obs.label,
                obs.checkpoint_days,
                obs.observed_sha,
                obs.observed_at.isoformat(),
                obs.surviving_lines,
            ),
        )

    def get_observation_labels(self, sha: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT label FROM survival_observations WHERE commit_sha=?", (sha,)
        ).fetchall()
        return {r["label"] for r in rows}

    def _get_batch_observations(self, shas: list[str]) -> dict[str, dict[str, SurvivalObservation]]:
        obs_map: dict[str, dict[str, SurvivalObservation]] = defaultdict(dict)
        for i in range(0, len(shas), 900):
            chunk = shas[i : i + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT * FROM survival_observations WHERE commit_sha IN ({placeholders})",
                chunk,
            ).fetchall()
            for r in rows:
                obs_map[r["commit_sha"]][r["label"]] = SurvivalObservation(
                    commit_sha=r["commit_sha"],
                    label=r["label"],
                    checkpoint_days=r["checkpoint_days"],
                    observed_sha=r["observed_sha"],
                    observed_at=datetime.fromisoformat(r["observed_at"]),
                    surviving_lines=r["surviving_lines"],
                )
        return dict(obs_map)

    # --- derived views (assembled at query time) ---

    def get_commit_fate(self, commit_sha: str) -> Optional[CommitFate]:
        row = self.conn.execute(
            "SELECT * FROM commits WHERE commit_sha=?", (commit_sha,)
        ).fetchone()
        if not row:
            return None
        fates = self._assemble_fates([row])
        return fates[0] if fates else None

    def get_all_commit_fates(
        self,
        since: Optional[datetime] = None,
        path_filter: Optional[str] = None,
        author_filter: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[CommitFate]:
        query = "SELECT * FROM commits WHERE 1=1"
        params: list = []
        if since:
            query += " AND authored_at >= ?"
            params.append(since.isoformat())
        if author_filter:
            query += " AND (author_email LIKE ? OR author_name LIKE ?)"
            params.extend([f"%{author_filter}%", f"%{author_filter}%"])
        query += " ORDER BY authored_at DESC"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = self.conn.execute(query, params).fetchall()
        fates = self._assemble_fates(rows)
        if path_filter:
            fates = [
                f for f in fates
                if any(p.startswith(path_filter) for p in f.file_paths)
            ]
        return fates

    def _assemble_fates(self, rows: list[sqlite3.Row]) -> list[CommitFate]:
        from git_aftermerge.analyzer.scorer import compute_score
        from git_aftermerge.analyzer.survival import parse_commit_type

        shas = [r["commit_sha"] for r in rows]
        links_map = self._get_batch_links(shas)
        obs_map = self._get_batch_observations(shas)

        files_map: dict[str, list[FileChange]] = defaultdict(list)
        for i in range(0, len(shas), 900):
            chunk = shas[i : i + 900]
            placeholders = ",".join("?" for _ in chunk)
            for r in self.conn.execute(
                f"SELECT * FROM commit_files WHERE commit_sha IN ({placeholders})", chunk
            ).fetchall():
                files_map[r["commit_sha"]].append(_row_to_file_change(r))

        now = datetime.utcnow()
        fates = []
        for row in rows:
            sha = row["commit_sha"]
            authored_at = datetime.fromisoformat(row["authored_at"])
            events = links_map.get(sha, [])
            observations = obs_map.get(sha, {})
            files = files_map.get(sha, [])
            lines_added = row["lines_added"]

            # Clamp: blame's internal diff can attribute slightly more lines
            # than numstat's histogram diff counted as added.
            head_obs = observations.get(HEAD_LABEL)
            surviving = (
                min(head_obs.surviving_lines, lines_added) if head_obs else lines_added
            )

            early_obs = observations.get(checkpoint_label(CHECKPOINT_DAYS[0]))
            early_ratio = (
                min(early_obs.surviving_lines, lines_added) / lines_added
                if early_obs and lines_added > 0 else None
            )

            reverted = any(e.event_type == EventType.REVERT_LINKED for e in events)
            fate_val = _determine_fate(lines_added, surviving, reverted)

            fate = CommitFate(
                commit_sha=sha,
                author=row["author_email"],
                author_tool=row["author_tool"],
                author_model=row["author_model"],
                merged_at=authored_at,
                original_lines_added=lines_added,
                original_lines_modified=row["lines_deleted"],
                surviving_lines=surviving,
                survival_score=0,
                fate=fate_val,
                days_since_merge=(now - authored_at).days,
                downstream_events=events,
                file_paths=[f.path for f in files],
                commit_type=parse_commit_type(row["message_subject"]),
                ai_attributed=row["cohort"] == Cohort.AI_AGENT.value,
                cohort=row["cohort"],
                maturity=_dominant_maturity(files, authored_at),
                early_survival_ratio=early_ratio,
                ai_session=row["ai_session"],
                human_ratio=row["human_ratio"],
            )
            fate.survival_score = compute_score(fate)
            fates.append(fate)
        return fates

    def get_curve_rows(self, since: Optional[datetime] = None) -> list[dict]:
        """Rows for survival-curve computation: one dict per commit with its
        cohort, dominant maturity tier, tracked lines, age, and observations."""
        query = "SELECT * FROM commits WHERE lines_added > 0"
        params: list = []
        if since:
            query += " AND authored_at >= ?"
            params.append(since.isoformat())
        rows = self.conn.execute(query, params).fetchall()
        shas = [r["commit_sha"] for r in rows]
        obs_map = self._get_batch_observations(shas)

        files_map: dict[str, list[FileChange]] = defaultdict(list)
        for i in range(0, len(shas), 900):
            chunk = shas[i : i + 900]
            placeholders = ",".join("?" for _ in chunk)
            for r in self.conn.execute(
                f"SELECT * FROM commit_files WHERE commit_sha IN ({placeholders})", chunk
            ).fetchall():
                files_map[r["commit_sha"]].append(_row_to_file_change(r))

        now = datetime.utcnow()
        out = []
        for row in rows:
            sha = row["commit_sha"]
            authored_at = datetime.fromisoformat(row["authored_at"])
            out.append({
                "sha": sha,
                "cohort": row["cohort"],
                "tool": row["author_tool"],
                "maturity": _dominant_maturity(files_map.get(sha, []), authored_at),
                "lines_added": row["lines_added"],
                "age_days": (now - authored_at).days,
                "observations": {
                    label: obs.surviving_lines
                    for label, obs in obs_map.get(sha, {}).items()
                },
            })
        return out


def _determine_fate(lines_added: int, surviving: int, reverted: bool) -> Fate:
    if reverted:
        return Fate.REVERTED
    if lines_added <= 0:
        return Fate.SURVIVED
    ratio = surviving / lines_added
    if ratio >= 0.9:
        return Fate.SURVIVED
    if ratio >= 0.3:
        return Fate.MODIFIED
    if ratio == 0:
        return Fate.SUPERSEDED
    return Fate.DECAYED


def _dominant_maturity(files: list[FileChange], authored_at: datetime) -> Optional[str]:
    """Maturity tier of the code a commit touched, weighted by lines added."""
    if not files:
        return None
    weights: dict[MaturityTier, int] = defaultdict(int)
    for f in files:
        age = None
        if f.prev_touch_at is not None:
            age = (authored_at.replace(tzinfo=None) - f.prev_touch_at.replace(tzinfo=None)).days
        weights[maturity_of(age)] += max(f.lines_added, 1)
    return max(weights.items(), key=lambda kv: kv[1])[0].value


def _row_to_event(r: sqlite3.Row) -> DownstreamEvent:
    return DownstreamEvent(
        event_type=EventType(r["link_type"]),
        commit_sha=r["event_commit_sha"],
        date=datetime.fromisoformat(r["event_date"]),
        lines_affected=r["lines_affected"],
        commit_message=r["commit_message"] or "",
        author=r["author"] or "",
    )


def _row_to_file_change(r: sqlite3.Row) -> FileChange:
    return FileChange(
        path=r["path"],
        lines_added=r["lines_added"],
        lines_deleted=r["lines_deleted"],
        prev_touch_at=(
            datetime.fromisoformat(r["prev_touch_at"]) if r["prev_touch_at"] else None
        ),
    )
