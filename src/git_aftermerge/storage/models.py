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
