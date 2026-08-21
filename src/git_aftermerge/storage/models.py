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


class Cohort(str, Enum):
    AI_AGENT = "ai-agent"   # LLM coding agents (claude-code, cursor, copilot, …)
    BOT = "bot"             # deterministic automation (dependabot, renovate) — negative control
    HUMAN = "human"


class MaturityTier(str, Enum):
    NEW = "new"         # file < 30 days old at the time of the change (or created by it)
    YOUNG = "young"     # 30–365 days since previous touch
    MATURE = "mature"   # > 365 days since previous touch


# Checkpoint ages (days after authoring) at which survival is observed.
CHECKPOINT_DAYS = (7, 30, 90, 180, 365)
HEAD_LABEL = "head"


def checkpoint_label(days: int) -> str:
    return f"{days}d"


def maturity_of(age_days: Optional[int]) -> MaturityTier:
    """Bucket a file's age (days since previous touch) into a maturity tier.

    ``None`` means the file was created by the commit itself → NEW code.
    """
    if age_days is None or age_days < 30:
        return MaturityTier.NEW
    if age_days <= 365:
        return MaturityTier.YOUNG
    return MaturityTier.MATURE


# --- Fact models (what gets persisted; nothing derived) -------------------------


@dataclass
class FileChange:
    """Per-file fact of a commit."""
    path: str
    lines_added: int
    lines_deleted: int
    # When any commit last touched this path before this one (None = file created here).
    prev_touch_at: Optional[datetime] = None


@dataclass
class CommitFact:
    """Facts about a single commit, straight from the git object + attribution."""
    sha: str
    author_name: str
    author_email: str
    authored_at: datetime
    subject: str
    cohort: str                       # Cohort value
    author_tool: Optional[str] = None
    author_model: Optional[str] = None
    ai_session: Optional[str] = None
    human_ratio: Optional[float] = None
    attribution_source: str = "none"
    lines_added: int = 0
    lines_deleted: int = 0
    files: list[FileChange] = field(default_factory=list)


@dataclass
class SurvivalObservation:
    """A blame snapshot fact: how many of a commit's lines were still attributed
    to it when the repo state at ``observed_sha`` was inspected."""
    commit_sha: str
    label: str            # "7d", "30d", …, "head"
    checkpoint_days: int  # nominal days after authoring (actual age for "head")
    observed_sha: str
    observed_at: datetime
    surviving_lines: int


@dataclass
class DownstreamEvent:
    event_type: EventType
    commit_sha: str
    date: datetime
    lines_affected: int
    commit_message: str
    author: str


# --- Derived models (computed at query time, never persisted) -------------------


@dataclass
class CommitFate:
    commit_sha: str
    author: str
    author_tool: Optional[str]       # e.g. "claude-code", "cursor"
    author_model: Optional[str]      # e.g. "claude-sonnet-4-6"
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
    cohort: str = Cohort.HUMAN.value
    maturity: Optional[str] = None            # dominant MaturityTier of touched files
    early_survival_ratio: Optional[float] = None  # survival at the 7d checkpoint
    ai_session: Optional[str] = None
    human_ratio: Optional[float] = None


@dataclass
class CurvePoint:
    days: int
    survival_rate: float   # line-weighted fraction of tracked lines still surviving
    commit_count: int
    lines_tracked: int


@dataclass
class SurvivalCurve:
    """Survival over time for one cohort (optionally × maturity tier).

    Curves are only meaningful as *within-repo* comparisons between cohorts;
    absolute values are not comparable across repositories.
    """
    cohort: str                    # "ai-agent" | "human" | "bot" | "all"
    maturity: Optional[str]        # MaturityTier value or None for all tiers
    points: list[CurvePoint] = field(default_factory=list)


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
