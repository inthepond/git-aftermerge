CREATE TABLE IF NOT EXISTS scan_meta (
    id INTEGER PRIMARY KEY,
    last_scanned_sha TEXT,
    last_scan_time TEXT,
    repo_path TEXT
);

CREATE TABLE IF NOT EXISTS commit_fates (
    commit_sha TEXT PRIMARY KEY,
    author TEXT NOT NULL,
    author_tool TEXT,
    author_model TEXT,
    merged_at TEXT NOT NULL,
    original_lines_added INTEGER NOT NULL DEFAULT 0,
    original_lines_modified INTEGER NOT NULL DEFAULT 0,
    surviving_lines INTEGER NOT NULL DEFAULT 0,
    survival_score INTEGER NOT NULL DEFAULT 100,
    fate TEXT NOT NULL DEFAULT 'SURVIVED',
    commit_type TEXT,
    ai_attributed INTEGER NOT NULL DEFAULT 0,
    file_paths_json TEXT,  -- JSON array of file paths
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS downstream_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_commit_sha TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_commit_sha TEXT NOT NULL,
    event_date TEXT NOT NULL,
    lines_affected INTEGER NOT NULL DEFAULT 0,
    commit_message TEXT,
    author TEXT,
    FOREIGN KEY (source_commit_sha) REFERENCES commit_fates(commit_sha)
);

CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension TEXT NOT NULL,       -- by_path, by_type, by_author, by_language
    key TEXT NOT NULL,
    avg_score REAL NOT NULL,
    commit_count INTEGER NOT NULL,
    revert_count INTEGER NOT NULL DEFAULT 0,
    bug_fix_count INTEGER NOT NULL DEFAULT 0,
    computed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(dimension, key)
);

CREATE INDEX IF NOT EXISTS idx_events_source ON downstream_events(source_commit_sha);
CREATE INDEX IF NOT EXISTS idx_fates_author ON commit_fates(author);
CREATE INDEX IF NOT EXISTS idx_fates_fate ON commit_fates(fate);
CREATE INDEX IF NOT EXISTS idx_fates_merged ON commit_fates(merged_at);
