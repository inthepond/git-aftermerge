# git-aftermerge

> Track the downstream fate of every commit. Feed structured post-merge feedback to AI coding agents.

> **Note (v0.2):** this document is the original v0.1 build spec, kept for
> history. v0.2 replaced the storage design with a facts-only schema
> (`commits` / `commit_files` / `commit_links` / `survival_observations`;
> scores, fates, patterns, and curves are computed at query time), added
> trailer-based attribution with cohorts (ai-agent / bot / human), code
> maturity tiers, checkpoint survival observations, and the `curve` and
> `attribution` commands. See `docs/how-it-works.md` for current behavior.

## What This Is

git-aftermerge is a Python CLI + MCP server that analyzes what happens to code *after* it gets merged — tracking survival, reverts, churn, and bug-fix correlations — then feeds structured feedback back to coding agents so they learn from their own history.

## The Problem

AI coding agents (Claude Code, Codex, Cursor, etc.) generate code, open PRs, and get them merged. Then the feedback loop ends. The agent never learns whether that code survived a week, got reverted the next day, caused a downstream bug, or was completely rewritten in the next sprint. git-aftermerge closes that loop.

## Project Structure

```
git-aftermerge/
├── pyproject.toml              # Project metadata, dependencies, entry points
├── README.md
├── LICENSE                     # MIT
├── .github/
│   └── workflows/
│       ├── ci.yml              # Test + lint on PR
│       └── publish.yml         # PyPI publish on tag
├── src/
│   └── git_aftermerge/
│       ├── __init__.py         # Package version
│       ├── cli.py              # Typer CLI entry point
│       ├── analyzer/
│       │   ├── __init__.py
│       │   ├── git_reader.py   # Git history reading (log, blame, diff)
│       │   ├── survival.py     # Line-level survival tracking
│       │   ├── revert.py       # Revert detection
│       │   ├── correlation.py  # Bug-fix / churn correlation
│       │   ├── scorer.py       # Survival score computation
│       │   └── patterns.py     # Pattern aggregation engine
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── db.py           # SQLite database manager
│       │   ├── schema.sql      # Table definitions
│       │   └── models.py       # Dataclass models (CommitFate, Event, Pattern)
│       ├── output/
│       │   ├── __init__.py
│       │   ├── terminal.py     # Rich terminal output formatting
│       │   ├── json_out.py     # JSON export
│       │   └── markdown.py     # Markdown context file generation
│       └── mcp/
│           ├── __init__.py
│           └── server.py       # FastMCP server with all tools
├── tests/
│   ├── conftest.py             # Shared fixtures, synthetic git repo builder
│   ├── test_git_reader.py
│   ├── test_survival.py
│   ├── test_revert.py
│   ├── test_correlation.py
│   ├── test_scorer.py
│   ├── test_patterns.py
│   ├── test_cli.py
│   └── test_mcp.py
└── docs/
    ├── index.md
    ├── quickstart.md
    ├── cli-reference.md
    ├── mcp-setup.md
    └── how-it-works.md
```

## Dependencies

```toml
[project]
name = "git-aftermerge"
version = "0.1.0"
description = "Track the downstream fate of every commit. Post-merge feedback for AI coding agents."
requires-python = ">=3.11"
license = "MIT"
dependencies = [
    "gitpython>=3.1.40",
    "typer>=0.12.0",
    "rich>=13.0.0",
    "mcp>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.5.0",
]

[project.scripts]
git-aftermerge = "git_aftermerge.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
```

## Data Models

All core data structures are defined as Python dataclasses in `src/git_aftermerge/storage/models.py`.

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Fate(str, Enum):
    SURVIVED = "SURVIVED"       # Lines still present, unmodified
    MODIFIED = "MODIFIED"       # Lines changed (refactored, extended, fixed)
    REVERTED = "REVERTED"       # Commit explicitly reverted
    SUPERSEDED = "SUPERSEDED"   # Lines replaced by a different approach
    DECAYED = "DECAYED"         # Lines gradually rewritten over multiple commits


class EventType(str, Enum):
    BUG_FIX_LINKED = "BUG_FIX_LINKED"
    REVERT_LINKED = "REVERT_LINKED"
    CHURN_SPIKE = "CHURN_SPIKE"
    INCIDENT_TAG = "INCIDENT_TAG"
    MODIFICATION = "MODIFICATION"


@dataclass
class DownstreamEvent:
    event_type: EventType
    commit_sha: str
    date: datetime
    lines_affected: int
    commit_message: str
    author: str


@dataclass
class CommitFate:
    commit_sha: str
    author: str
    author_tool: Optional[str]       # e.g. "claude-code", "cursor" — from git-ai if available
    author_model: Optional[str]      # e.g. "claude-sonnet-4.6" — from git-ai if available
    merged_at: datetime
    original_lines_added: int
    original_lines_modified: int
    surviving_lines: int
    survival_score: int              # 0-100
    fate: Fate
    days_since_merge: int
    downstream_events: list[DownstreamEvent] = field(default_factory=list)
    file_paths: list[str] = field(default_factory=list)
    commit_type: Optional[str] = None  # feat, fix, refactor, test, etc.
    ai_attributed: bool = False


@dataclass
class PatternEntry:
    dimension: str      # "by_path", "by_type", "by_author", etc.
    key: str            # e.g. "src/auth/", "feat", "claude"
    avg_score: float
    commit_count: int
    revert_count: int = 0
    bug_fix_count: int = 0


@dataclass
class RiskyArea:
    path: str
    avg_score: float
    commit_count: int
    revert_count: int
    bug_fix_count: int
    churn_rate: float   # modifications per day
    recommendation: str


@dataclass
class PatternReport:
    repo_name: str
    analysis_window_start: datetime
    analysis_window_end: datetime
    total_commits_analyzed: int
    ai_commits: int
    overall_survival_score: float
    patterns: list[PatternEntry] = field(default_factory=list)
    risky_areas: list[RiskyArea] = field(default_factory=list)
```

## SQLite Schema

Stored at `.aftermerge/aftermerge.db` inside each tracked repo.

```sql
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
```

## Core Engine Specifications

### 1. Git Reader (`analyzer/git_reader.py`)

Wraps GitPython and raw git subprocess calls to extract structured data from repo history.

Key functions:

- `get_merge_commits(since: Optional[str] = None) -> list[CommitInfo]` — List all merge commits (or all commits on the default branch). Filter by date if `since` is provided. Return sha, author, date, message, files changed, lines added/deleted.
- `get_blame_snapshot(file_path: str, at_commit: str) -> dict[int, BlameEntry]` — Run `git blame` for a file at a specific commit. Return mapping of line_number -> (commit_sha, author, date). Use `git blame --porcelain` for structured parsing.
- `get_diff_stats(commit_sha: str) -> DiffStats` — Return files changed, lines added, lines deleted for a commit. Use `git diff-tree --numstat`.
- `get_file_at_commit(file_path: str, commit_sha: str) -> Optional[str]` — Return file contents at a specific commit. Use `git show commit:path`.
- `find_reverts(since: Optional[str] = None) -> list[RevertInfo]` — Find commits that are reverts by parsing commit messages for "Revert" prefix, "This reverts commit <sha>" in body. Return the reverting commit and the original commit SHA.
- `get_commits_touching_lines(file_path: str, start_line: int, end_line: int, since_commit: str) -> list[CommitInfo]` — Find all commits that modified specific lines of a file after a given commit. Use `git log -L start,end:file`.

Performance note: For repos with 10k+ commits, use `git log --format` with raw subprocess calls instead of GitPython's iter_commits for the initial scan. GitPython is fine for per-commit operations.

### 2. Survival Tracker (`analyzer/survival.py`)

Tracks line-level survival of code introduced by each commit.

Algorithm:

```
For each commit C being analyzed:
  1. Get the list of files modified by C
  2. For each file F in the commit:
     a. Get the diff to identify which lines were added/modified by C
     b. Record the original line ranges attributed to C
  3. At the current HEAD (or at a specified checkpoint):
     a. Run git blame on each file F
     b. For each line originally from C, check if blame still attributes it to C
     c. Count: surviving = still attributed to C, modified = attributed to a later commit
  4. Compute: surviving_lines, modified_lines, deleted_lines
  5. Determine overall fate based on ratios
```

Fate determination logic:

```
if commit was explicitly reverted:
    fate = REVERTED
elif surviving_lines / original_lines >= 0.9:
    fate = SURVIVED
elif surviving_lines / original_lines >= 0.3:
    fate = MODIFIED
elif surviving_lines == 0 and was_replaced_by_single_commit:
    fate = SUPERSEDED
else:
    fate = DECAYED
```

Important: Do NOT process merge commits themselves, only the actual code-change commits. On repos using squash-merge, each merged commit IS the code change. On repos using merge commits, analyze the commits within the merge.

### 3. Revert Detector (`analyzer/revert.py`)

Detect when commits have been reverted.

Detection methods (in priority order):

1. **Message parsing**: Commit message starts with "Revert" and body contains "This reverts commit <sha>". This is the standard `git revert` format.
2. **Manual revert detection**: A commit that removes >80% of the lines added by a previous commit to the same files, within 7 days. No message reference needed.
3. **Force-push detection** (optional, harder): If the repo has reflog access, detect when a commit was removed from history entirely. This is a stretch goal for v0.4+.

Each detected revert creates a `DownstreamEvent` of type `REVERT_LINKED` attached to the original commit.

### 4. Correlation Engine (`analyzer/correlation.py`)

Link downstream events (bug-fixes, incidents, churn) back to originating commits.

Bug-fix correlation:

```
For each commit B where commit_type == "fix" or message contains fix/bug/hotfix:
  1. Get the files and lines touched by B
  2. For each file touched, get the blame at the commit BEFORE B
  3. Identify which earlier commits last touched those lines
  4. If an earlier commit C touched overlapping lines AND
     B is within 14 days of C being merged:
     → Create BUG_FIX_LINKED event from C to B
```

Churn spike detection:

```
For each file F:
  1. Count modifications per week over the analysis window
  2. Compute baseline churn rate (median modifications per week)
  3. If any week has >3x baseline churn:
     → Find commits from the preceding 2 weeks that touched F
     → Create CHURN_SPIKE events for those commits
```

Incident tagging:

```
For each commit where message contains "hotfix", "incident", "CVE", "urgent", "emergency":
  → Get blame for touched lines before this commit
  → Link source commits with INCIDENT_TAG events
```

### 5. Scorer (`analyzer/scorer.py`)

Compute the 0-100 survival score for each commit.

```python
def compute_score(commit_fate: CommitFate) -> int:
    if commit_fate.original_lines_added == 0:
        return 100  # No lines to track

    # Base score: percentage of lines surviving
    base = (commit_fate.surviving_lines / commit_fate.original_lines_added) * 100

    adjustments = 0

    # Penalty: reverted within 24 hours
    for event in commit_fate.downstream_events:
        if event.event_type == EventType.REVERT_LINKED:
            hours_to_revert = (event.date - commit_fate.merged_at).total_seconds() / 3600
            if hours_to_revert <= 24:
                adjustments -= 20
            elif hours_to_revert <= 72:
                adjustments -= 10

    # Penalty: >50% of lines modified within 7 days
    early_modifications = sum(
        e.lines_affected for e in commit_fate.downstream_events
        if e.event_type == EventType.MODIFICATION
        and (e.date - commit_fate.merged_at).days <= 7
    )
    if early_modifications > commit_fate.original_lines_added * 0.5:
        adjustments -= 10

    # Bonus: survived 30+ days with zero modifications
    if commit_fate.days_since_merge >= 30 and len(commit_fate.downstream_events) == 0:
        adjustments += 10

    # Bonus: other commits build on top (extending, not replacing)
    extensions = [
        e for e in commit_fate.downstream_events
        if e.event_type == EventType.MODIFICATION
        and "extend" in e.commit_message.lower() or "add" in e.commit_message.lower()
    ]
    if extensions:
        adjustments += 5

    # Penalty: linked to bug fixes
    bug_fixes = [e for e in commit_fate.downstream_events if e.event_type == EventType.BUG_FIX_LINKED]
    adjustments -= len(bug_fixes) * 5

    return max(0, min(100, int(base + adjustments)))
```

### 6. Pattern Aggregator (`analyzer/patterns.py`)

Roll up individual commit fates into queryable patterns.

Dimensions:

- **by_path**: Group commits by directory prefix (e.g. `src/auth/`, `src/api/`, `tests/`). Use the first 2-3 directory levels.
- **by_type**: Parse conventional commit prefix (feat, fix, refactor, test, docs, chore). Fall back to "unknown" if no prefix.
- **by_author**: Group by committer name. Separate "human" vs specific agent names if distinguishable.
- **by_size**: Small (<20 lines added), Medium (20-100), Large (>100).
- **by_language**: Group by file extension of modified files.

For each dimension and key, compute:

- Average survival score
- Total commit count
- Revert count
- Bug-fix correlation count
- Recommendation string (generated from simple rules, no LLM needed)

Recommendation rules:

```
if revert_count >= 3 in 90 days:
    "This area has been reverted {n}x in 90 days. Consider architectural review before modifying."
if avg_score < 50:
    "Low survival rate ({score}%). Changes here are frequently rewritten. Check existing patterns before committing."
if avg_score > 90 and commit_count > 10:
    "High stability area. Changes here tend to stick."
```

## CLI Specification

Entry point: `src/git_aftermerge/cli.py` using Typer.

### Commands

#### `git-aftermerge init`

Initialize aftermerge tracking in the current git repository.

- Create `.aftermerge/` directory
- Create SQLite database with schema
- Add `.aftermerge/` to `.gitignore` if not already present
- Run initial scan (equivalent to `scan --full`)
- Print summary: "Initialized git-aftermerge. Analyzed {n} commits."

#### `git-aftermerge scan`

Scan git history and update commit fate data.

Options:
- `--full`: Re-scan entire history (default on first run)
- `--since TEXT`: Only scan commits since this date/ref (default: since last scan)

Behavior:
1. Read `scan_meta` for last scanned SHA
2. Get all commits from last scanned SHA to HEAD
3. For each new commit, run survival tracking
4. For all commits (including previously scanned), update survival scores based on current HEAD blame
5. Run revert detection and correlation engine
6. Update patterns
7. Store new last scanned SHA

Print progress with Rich progress bar. Print summary at end.

#### `git-aftermerge fate <commit-sha>`

Show the fate of a specific commit.

Output (Rich formatted terminal):

```
Commit:    abc1234 — "feat: add JWT refresh token rotation"
Author:    claude (via Claude Code)
Merged:    2026-03-15  (29 days ago)
Score:     73 / 100

Lines:     +142 added, +38 modified
Surviving: 104 / 142 (73.2%)

Events:
  ├─ 2026-03-17  MODIFIED   fix: handle edge case in token expiry
  │              ↳ 12 lines changed by human (dekko)
  ├─ 2026-03-22  MODIFIED   refactor: extract token validation
  │              ↳ 16 lines moved, logic preserved
  └─ 2026-04-01  BUG_FIX    fix: refresh token race condition
                 ↳ 10 lines deleted, replaced by mutex approach

Patterns:
  ⚠ auth/ directory has 34% lower survival than repo average
  ℹ "feat" commits by claude avg 71 score (human avg: 68)
```

Options:
- `--json`: Output as JSON instead of formatted terminal
- `--short`: One-line summary only

#### `git-aftermerge report`

Show aggregate patterns for the entire repo.

Output sections:
1. Overview: total commits, AI vs human split, overall survival score
2. Top 5 riskiest areas (lowest avg survival)
3. Top 5 most stable areas (highest avg survival)
4. Author comparison (if multiple authors)
5. Recent reverts and bug-fix correlations (last 30 days)

Options:
- `--json`: Output as JSON
- `--since TEXT`: Limit analysis window
- `--path TEXT`: Filter to a specific directory

#### `git-aftermerge context`

Generate an agent-consumable context file.

Output: Markdown-formatted summary designed to be included in or referenced from CLAUDE.md / AGENTS.md. Limited to a reasonable token count (default ~2000 tokens worth of text).

Content:
1. One-paragraph repo survival overview
2. Top 3 risky areas with recommendations
3. Recent failures (reverts, bug-fix clusters) from last 30 days
4. Per-directory survival scores (abbreviated)

Options:
- `--format [md|json]`: Output format (default: md)
- `--max-lines INT`: Maximum output lines (default: 80)
- `--output FILE`: Write to file instead of stdout

Typical usage:

```bash
git-aftermerge context --output .aftermerge/CONTEXT.md
```

Then in CLAUDE.md:

```markdown
## Post-Merge Context
See .aftermerge/CONTEXT.md for code survival data and risky areas.
```

#### `git-aftermerge mcp-serve`

Start the MCP server (stdio transport).

This is the command used in MCP server configuration. It starts a FastMCP server that exposes the aftermerge tools.

## MCP Server Specification

File: `src/git_aftermerge/mcp/server.py`

Use the `mcp` Python SDK (FastMCP) with stdio transport.

### Tools

#### `aftermerge_get_fate`

Get the post-merge fate of a specific commit.

Parameters:
- `commit_sha` (string, required): The commit SHA to look up

Returns: JSON-serialized `CommitFate` object

#### `aftermerge_get_patterns`

Get aggregate survival patterns for the repo.

Parameters:
- `path_filter` (string, optional): Filter to a directory, e.g. "src/auth/"
- `since` (string, optional): Only include commits since this date, e.g. "30 days ago"
- `author_filter` (string, optional): Filter to a specific author, e.g. "claude"

Returns: JSON-serialized `PatternReport` object

#### `aftermerge_get_risky_areas`

List directories/files with lowest survival scores.

Parameters:
- `limit` (int, optional, default 10): Number of results

Returns: JSON array of `RiskyArea` objects

#### `aftermerge_get_recent_failures`

Get commits that were reverted or caused bug-fixes recently.

Parameters:
- `since` (string, optional, default "30 days ago"): Time window

Returns: JSON array of `CommitFate` objects filtered to those with REVERT_LINKED or BUG_FIX_LINKED events

#### `aftermerge_get_context`

Get a structured markdown summary for agent context injection. Same output as the `context` CLI command.

Parameters:
- `max_tokens` (int, optional, default 2000): Approximate maximum output length

Returns: Markdown-formatted string

### MCP Server Setup

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("git-aftermerge")

@mcp.tool()
def aftermerge_get_fate(commit_sha: str) -> str:
    """Get the post-merge fate of a specific commit, including survival score, downstream events, and patterns."""
    # ... implementation

@mcp.tool()
def aftermerge_get_patterns(
    path_filter: str = "",
    since: str = "",
    author_filter: str = ""
) -> str:
    """Get aggregate survival patterns for the repo, optionally filtered by path, date, or author."""
    # ... implementation

# ... other tools

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

Agent configuration (`claude_desktop_config.json` or equivalent):

```json
{
  "mcpServers": {
    "aftermerge": {
      "command": "git-aftermerge",
      "args": ["mcp-serve"],
      "cwd": "/path/to/your/repo"
    }
  }
}
```

## Testing Strategy

### Synthetic Git Repo Fixture

Create a pytest fixture that builds a temporary git repository with a known history for deterministic testing. This is the foundation of all tests.

```python
# tests/conftest.py

import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def synthetic_repo(tmp_path):
    """Create a git repo with a known history for testing.

    History created:
    1. Initial commit with base files
    2. Feature commit (feat: add auth) — adds src/auth.py with 50 lines
    3. Bug fix commit (fix: auth edge case) — modifies 10 lines in src/auth.py
    4. Another feature (feat: add api) — adds src/api.py with 30 lines
    5. Revert commit — reverts commit 2
    6. A stable feature (feat: add utils) — adds src/utils.py, never modified
    """
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()
    os.chdir(repo_dir)

    def run_git(*args, **kwargs):
        env = {**os.environ, "GIT_AUTHOR_DATE": kwargs.get("date", ""), "GIT_COMMITTER_DATE": kwargs.get("date", "")}
        return subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True, env=env)

    run_git("init")
    run_git("config", "user.email", "test@test.com")
    run_git("config", "user.name", "Test User")

    # Build commits with known content and dates...
    # (implementation creates the specific history described above)

    return repo_dir
```

### Test Cases

**test_git_reader.py**: Test that we can correctly parse commits, blame, diffs from the synthetic repo. Test edge cases: binary files, renamed files, empty commits.

**test_survival.py**: Test that survival tracking correctly identifies surviving/modified/deleted lines. Test with the synthetic repo where we know exact line fates.

**test_revert.py**: Test revert detection for standard `git revert` messages, manual reverts (same lines deleted), and non-reverts that happen to touch the same files.

**test_correlation.py**: Test bug-fix correlation. The synthetic repo has a known bug-fix (commit 3 fixes commit 2). Verify the correlation is detected. Test false-positive resistance.

**test_scorer.py**: Test score computation with known inputs. Test all adjustment conditions (revert penalty, early modification penalty, longevity bonus, bug-fix penalty).

**test_patterns.py**: Test pattern aggregation produces correct groupings and averages.

**test_cli.py**: Integration tests using Click's CliRunner. Test init, scan, fate, report, context commands produce expected output.

**test_mcp.py**: Test MCP tools return correctly structured JSON. Can use FastMCP's test utilities if available, otherwise test the underlying functions directly.

## Implementation Order

### Milestone 1: Foundation (Week 1-2)

1. Project scaffolding: pyproject.toml, src layout, CI workflow
2. `storage/models.py` — all dataclasses
3. `storage/schema.sql` and `storage/db.py` — SQLite manager with create, insert, query methods
4. `analyzer/git_reader.py` — all git reading functions
5. `analyzer/survival.py` — line-level survival tracking
6. `cli.py` — `init` and `scan` commands (basic version)
7. `tests/conftest.py` — synthetic repo fixture
8. Tests for git_reader and survival

### Milestone 2: Intelligence (Week 3-4)

1. `analyzer/revert.py` — revert detection
2. `analyzer/correlation.py` — bug-fix and churn correlation
3. `analyzer/scorer.py` — survival score computation
4. `analyzer/patterns.py` — pattern aggregation
5. `output/terminal.py` — Rich formatted output
6. `cli.py` — `fate`, `report`, `context` commands
7. `output/markdown.py` — markdown context generation
8. Tests for revert, correlation, scorer, patterns

### Milestone 3: Agent Bridge (Week 5-6)

1. `mcp/server.py` — FastMCP server with all 5 tools
2. `cli.py` — `mcp-serve` command
3. Incremental scan (only process new commits since last scan)
4. `output/json_out.py` — JSON export
5. Git-AI integration: detect and use git-ai attribution data if available
6. CLAUDE.md template generator
7. Tests for MCP tools
8. PyPI packaging and publish workflow

### Milestone 4: Production Ready (Week 7-8)

1. Performance optimization: batch blame operations, limit full-repo scans
2. Handle large repos (10k+ commits) — pagination, progress indicators
3. Watch mode (`git-aftermerge watch`)
4. Documentation site (MkDocs)
5. README with badges, examples, quickstart
6. GitHub release automation
7. Comprehensive edge-case testing (binary files, submodules, shallow clones)
8. Launch prep: Show HN post, V2EX post, Product Hunt listing

## Design Principles

1. **Read-only by default**: Never modify the git repo. No hooks required. Pure observation.
2. **Zero external services**: No databases to run, no APIs to call, no LLM inference in the core engine. Pure deterministic analysis.
3. **Incremental by design**: After the initial scan, only process new commits. Survival scores are updated by re-running blame on existing tracked files.
4. **Agent-first output**: Every output format is designed for machine consumption first, human readability second. JSON and structured markdown over prose.
5. **Optional AI attribution**: Works on any repo without git-ai. If git-ai is installed, uses its data for richer AI-vs-human breakdowns.
6. **Composable layers**: The analyzer library, CLI, and MCP server are independent. You can import the analyzer in your own Python code without using the CLI or MCP server.