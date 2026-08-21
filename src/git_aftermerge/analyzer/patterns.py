"""Pattern aggregation engine."""

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from git_aftermerge.storage.models import (
    CommitFate,
    EventType,
    Fate,
    PatternEntry,
    PatternReport,
    RiskyArea,
)

EXTENSION_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript", ".go": "go",
    ".rs": "rust", ".java": "java", ".rb": "ruby", ".php": "php",
    ".cpp": "cpp", ".c": "c", ".cs": "csharp", ".swift": "swift",
    ".kt": "kotlin", ".sh": "shell", ".md": "markdown",
}


def _path_key(file_paths: list[str]) -> str:
    """Return 2-level directory prefix for grouping."""
    if not file_paths:
        return "root"
    parts = file_paths[0].split("/")
    return "/".join(parts[:2]) + "/" if len(parts) > 2 else parts[0] + "/"


def _size_bucket(lines_added: int) -> str:
    if lines_added < 20:
        return "small"
    if lines_added <= 100:
        return "medium"
    return "large"


def _language(file_paths: list[str]) -> str:
    for path in file_paths:
        ext = Path(path).suffix.lower()
        if ext in EXTENSION_MAP:
            return EXTENSION_MAP[ext]
    return "other"


def _make_recommendation(
    avg_score: float,
    revert_count: int,
    commit_count: int,
    key: str,
) -> str:
    if revert_count >= 3:
        return (
            f"This area has been reverted {revert_count}x recently. "
            "Consider architectural review before modifying."
        )
    if avg_score < 50:
        return (
            f"Low survival rate ({avg_score:.0f}%). "
            "Changes here are frequently rewritten. Check existing patterns before committing."
        )
    if avg_score > 90 and commit_count > 10:
        return "High stability area. Changes here tend to stick."
    return ""


class PatternAggregator:
    def compute_patterns(
        self,
        fates: list[CommitFate],
        since: Optional[datetime] = None,
    ) -> list[PatternEntry]:
        if since:
            fates = [f for f in fates if f.merged_at >= since]

        # Group by each dimension
        groups: dict[tuple[str, str], list[CommitFate]] = defaultdict(list)
        for fate in fates:
            groups[("by_path", _path_key(fate.file_paths))].append(fate)
            groups[("by_type", fate.commit_type or "unknown")].append(fate)
            groups[("by_author", fate.author.split("@")[0])].append(fate)
            groups[("by_size", _size_bucket(fate.original_lines_added))].append(fate)
            groups[("by_language", _language(fate.file_paths))].append(fate)
            groups[("by_cohort", fate.cohort)].append(fate)
            if fate.maturity:
                groups[("by_maturity", fate.maturity)].append(fate)

        entries = []
        for (dimension, key), group_fates in groups.items():
            avg_score = sum(f.survival_score for f in group_fates) / len(group_fates)
            revert_count = sum(
                1 for f in group_fates if f.fate == Fate.REVERTED
                or any(e.event_type == EventType.REVERT_LINKED for e in f.downstream_events)
            )
            bug_fix_count = sum(
                sum(1 for e in f.downstream_events if e.event_type == EventType.BUG_FIX_LINKED)
                for f in group_fates
            )
            entries.append(PatternEntry(
                dimension=dimension,
                key=key,
                avg_score=round(avg_score, 2),
                commit_count=len(group_fates),
                revert_count=revert_count,
                bug_fix_count=bug_fix_count,
            ))

        return entries

    def compute_risky_areas(
        self, fates: list[CommitFate], since: Optional[datetime] = None
    ) -> list[RiskyArea]:
        patterns = self.compute_patterns(fates, since)
        path_patterns = [p for p in patterns if p.dimension == "by_path"]

        risky = []
        for p in sorted(path_patterns, key=lambda x: x.avg_score):
            # Compute churn rate: modifications per day
            path_fates = [
                f for f in fates
                if f.file_paths and _path_key(f.file_paths) == p.key
            ]
            if not path_fates:
                continue
            date_range = (
                (max(f.merged_at for f in path_fates) - min(f.merged_at for f in path_fates)).days
                or 1
            )
            modifications = sum(
                sum(1 for e in f.downstream_events if e.event_type == EventType.MODIFICATION)
                for f in path_fates
            )
            churn_rate = modifications / date_range

            recommendation = _make_recommendation(
                p.avg_score, p.revert_count, p.commit_count, p.key
            )
            risky.append(RiskyArea(
                path=p.key,
                avg_score=p.avg_score,
                commit_count=p.commit_count,
                revert_count=p.revert_count,
                bug_fix_count=p.bug_fix_count,
                churn_rate=round(churn_rate, 4),
                recommendation=recommendation,
            ))

        return risky

    def build_report(
        self,
        repo_name: str,
        fates: list[CommitFate],
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> PatternReport:
        filtered = fates
        if since:
            filtered = [f for f in filtered if f.merged_at >= since]
        if until:
            filtered = [f for f in filtered if f.merged_at <= until]

        window_start = since or (min((f.merged_at for f in filtered), default=datetime.utcnow()))
        window_end = until or datetime.utcnow()

        ai_commits = sum(1 for f in filtered if f.ai_attributed)
        overall_score = (
            sum(f.survival_score for f in filtered) / len(filtered) if filtered else 0.0
        )

        patterns = self.compute_patterns(filtered)
        risky_areas = self.compute_risky_areas(filtered)

        return PatternReport(
            repo_name=repo_name,
            analysis_window_start=window_start,
            analysis_window_end=window_end,
            total_commits_analyzed=len(filtered),
            ai_commits=ai_commits,
            overall_survival_score=round(overall_score, 2),
            patterns=patterns,
            risky_areas=risky_areas,
        )
