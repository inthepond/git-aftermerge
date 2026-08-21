-- git-aftermerge schema v2 — facts only.
--
-- Design rule: these tables store only facts recoverable from git plus
-- deterministic attribution. Anything judged or scored (survival score,
-- fate label, pattern aggregates, maturity tiers, curves) is computed at
-- query time so metric definitions can change without a rescan.

CREATE TABLE IF NOT EXISTS scan_meta (
    id INTEGER PRIMARY KEY,
    last_scanned_sha TEXT,
    last_scan_time TEXT,
    repo_path TEXT
);

-- One row per analyzed commit. Attribution fields are facts: they derive
-- deterministically from the commit object (author identity + trailers).
CREATE TABLE IF NOT EXISTS commits (
    commit_sha TEXT PRIMARY KEY,
    author_name TEXT NOT NULL DEFAULT '',
    author_email TEXT NOT NULL,
    authored_at TEXT NOT NULL,
    message_subject TEXT NOT NULL DEFAULT '',
    cohort TEXT NOT NULL DEFAULT 'human',      -- ai-agent | bot | human
    author_tool TEXT,                          -- claude-code, cursor, aider, …
    author_model TEXT,
    ai_session TEXT,
    human_ratio REAL,
    attribution_source TEXT NOT NULL DEFAULT 'none',  -- trailer | co-author | author | message | none
    lines_added INTEGER NOT NULL DEFAULT 0,
    lines_deleted INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Per-file facts of each commit. prev_touch_at is when any commit last
-- touched the path before this one (NULL = the file was created here);
-- maturity tiers are derived from it at query time.
CREATE TABLE IF NOT EXISTS commit_files (
    commit_sha TEXT NOT NULL,
    path TEXT NOT NULL,
    lines_added INTEGER NOT NULL DEFAULT 0,
    lines_deleted INTEGER NOT NULL DEFAULT 0,
    prev_touch_at TEXT,
    PRIMARY KEY (commit_sha, path),
    FOREIGN KEY (commit_sha) REFERENCES commits(commit_sha)
);

-- Detected relationships between commits (revert of, fix touching, …).
-- The detection heuristics are versioned via the detector column so links
-- can be re-derived when heuristics change.
CREATE TABLE IF NOT EXISTS commit_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_commit_sha TEXT NOT NULL,
    link_type TEXT NOT NULL,
    event_commit_sha TEXT NOT NULL,
    event_date TEXT NOT NULL,
    lines_affected INTEGER NOT NULL DEFAULT 0,
    commit_message TEXT,
    author TEXT,
    detector TEXT NOT NULL DEFAULT 'v1',
    UNIQUE (source_commit_sha, link_type, event_commit_sha),
    FOREIGN KEY (source_commit_sha) REFERENCES commits(commit_sha)
);

-- Blame snapshot facts: surviving line counts observed at fixed checkpoints
-- (7d/30d/90d/180d/365d after authoring) and at HEAD. Survival curves are
-- a query over these rows.
CREATE TABLE IF NOT EXISTS survival_observations (
    commit_sha TEXT NOT NULL,
    label TEXT NOT NULL,                -- '7d', '30d', …, 'head'
    checkpoint_days INTEGER NOT NULL,
    observed_sha TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    surviving_lines INTEGER NOT NULL,
    PRIMARY KEY (commit_sha, label),
    FOREIGN KEY (commit_sha) REFERENCES commits(commit_sha)
);

CREATE INDEX IF NOT EXISTS idx_links_source ON commit_links(source_commit_sha);
CREATE INDEX IF NOT EXISTS idx_commits_author ON commits(author_email);
CREATE INDEX IF NOT EXISTS idx_commits_cohort ON commits(cohort);
CREATE INDEX IF NOT EXISTS idx_commits_authored ON commits(authored_at);
CREATE INDEX IF NOT EXISTS idx_obs_commit ON survival_observations(commit_sha);

PRAGMA user_version = 2;
