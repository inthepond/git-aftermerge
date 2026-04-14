"""Bug-fix and churn correlation engine."""

import re
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Optional

from git_aftermerge.analyzer.git_reader import CommitInfo, GitReader
from git_aftermerge.storage.models import DownstreamEvent, EventType

BUG_KEYWORDS = re.compile(
    r"\b(fix|bug|hotfix|bugfix|issue|error|crash|regression|patch|defect)\b",
    re.IGNORECASE,
)
INCIDENT_KEYWORDS = re.compile(
    r"\b(hotfix|incident|CVE|urgent|emergency|critical|outage)\b",
    re.IGNORECASE,
)


class CorrelationEngine:
    def __init__(self, repo_path: Path) -> None:
        self.repo = GitReader(repo_path)

    def find_bug_fix_correlations(
        self, commits: list[CommitInfo], window_days: int = 14
    ) -> dict[str, list[DownstreamEvent]]:
        """
        Returns mapping of original_sha -> list[DownstreamEvent(BUG_FIX_LINKED)].
        """
        events: dict[str, list[DownstreamEvent]] = defaultdict(list)
        commit_map = {c.sha: c for c in commits}

        bug_fixes = [
            c for c in commits
            if c.commit_type_hint() == "fix" or BUG_KEYWORDS.search(c.message)
        ]

        for fix_commit in bug_fixes:
            fix_details = (
                fix_commit.file_details
                or self.repo.get_diff_stats(fix_commit.sha).file_details
            )
            if not fix_details:
                continue

            for file_path in fix_details:
                # Get blame just before this fix
                try:
                    parent_sha = self._get_parent(fix_commit.sha)
                except RuntimeError:
                    continue
                if not parent_sha:
                    continue

                blame = self.repo.get_blame_snapshot(file_path, parent_sha)
                if not blame:
                    continue

                # Find earlier commits that last touched these lines
                implicated: set[str] = set()
                for entry in blame.values():
                    blamed_sha = entry.commit_sha
                    if blamed_sha == fix_commit.sha:
                        continue
                    if blamed_sha not in commit_map:
                        continue
                    blamed_commit = commit_map[blamed_sha]
                    # Within window
                    delta = fix_commit.date - blamed_commit.date
                    if timedelta(0) < delta <= timedelta(days=window_days):
                        implicated.add(blamed_sha)

                for orig_sha in implicated:
                    event = DownstreamEvent(
                        event_type=EventType.BUG_FIX_LINKED,
                        commit_sha=fix_commit.sha,
                        date=fix_commit.date.replace(tzinfo=None),
                        lines_affected=fix_details[file_path][1],
                        commit_message=fix_commit.message,
                        author=fix_commit.author,
                    )
                    # Avoid duplicate events
                    existing = events[orig_sha]
                    already = any(
                        e.commit_sha == fix_commit.sha
                        and e.event_type == EventType.BUG_FIX_LINKED
                        for e in existing
                    )
                    if not already:
                        events[orig_sha].append(event)

        return dict(events)

    def find_churn_spikes(
        self, commits: list[CommitInfo], analysis_window_days: int = 90
    ) -> dict[str, list[DownstreamEvent]]:
        """
        Returns mapping of commit_sha -> list[DownstreamEvent(CHURN_SPIKE)].
        """
        events: dict[str, list[DownstreamEvent]] = defaultdict(list)

        # Group commits by file, then by week
        file_commits: dict[str, list[CommitInfo]] = defaultdict(list)
        for c in commits:
            for fp in c.files_changed:
                file_commits[fp].append(c)

        for file_path, file_commit_list in file_commits.items():
            if len(file_commit_list) < 3:
                continue
            # Count mods per week
            week_counts: dict[int, int] = defaultdict(int)
            base_date = min(c.date for c in file_commit_list)
            for c in file_commit_list:
                week = (c.date - base_date).days // 7
                week_counts[week] += 1

            if not week_counts:
                continue

            weekly_vals = list(week_counts.values())
            weekly_vals_sorted = sorted(weekly_vals)
            median = weekly_vals_sorted[len(weekly_vals_sorted) // 2]
            if median == 0:
                continue

            for week, count in week_counts.items():
                if count > 3 * median:
                    # Churn spike — find commits from preceding 2 weeks
                    spike_start = base_date + timedelta(weeks=week - 2)
                    spike_end = base_date + timedelta(weeks=week)
                    preceding_commits = [
                        c for c in file_commit_list
                        if spike_start <= c.date <= spike_end
                    ]
                    for c in preceding_commits:
                        event = DownstreamEvent(
                            event_type=EventType.CHURN_SPIKE,
                            commit_sha=c.sha,
                            date=c.date.replace(tzinfo=None),
                            lines_affected=count,
                            commit_message=f"Churn spike in {file_path} (week {week})",
                            author=c.author,
                        )
                        events[c.sha].append(event)

        return dict(events)

    def find_incident_tags(
        self, commits: list[CommitInfo]
    ) -> dict[str, list[DownstreamEvent]]:
        """
        Returns mapping of source_sha -> list[DownstreamEvent(INCIDENT_TAG)].
        """
        events: dict[str, list[DownstreamEvent]] = defaultdict(list)
        commit_map = {c.sha: c for c in commits}

        incident_commits = [c for c in commits if INCIDENT_KEYWORDS.search(c.message)]

        for inc_commit in incident_commits:
            inc_details = (
                inc_commit.file_details
                or self.repo.get_diff_stats(inc_commit.sha).file_details
            )
            for file_path in inc_details:
                try:
                    parent_sha = self._get_parent(inc_commit.sha)
                except RuntimeError:
                    continue
                if not parent_sha:
                    continue

                blame = self.repo.get_blame_snapshot(file_path, parent_sha)
                for entry in blame.values():
                    orig_sha = entry.commit_sha
                    if orig_sha == inc_commit.sha or orig_sha not in commit_map:
                        continue
                    event = DownstreamEvent(
                        event_type=EventType.INCIDENT_TAG,
                        commit_sha=inc_commit.sha,
                        date=inc_commit.date.replace(tzinfo=None),
                        lines_affected=0,
                        commit_message=inc_commit.message,
                        author=inc_commit.author,
                    )
                    existing = events[orig_sha]
                    if not any(e.commit_sha == inc_commit.sha for e in existing):
                        events[orig_sha].append(event)

        return dict(events)

    def _get_parent(self, sha: str) -> Optional[str]:
        output = self.repo._run("rev-parse", f"{sha}^", check=False)
        parent = output.strip()
        return parent if parent else None


# Monkey-patch CommitInfo to add commit_type_hint without changing the dataclass
_COMMIT_TYPE_RE = re.compile(
    r"^(feat|fix|refactor|test|docs|chore|style|perf|ci|build|revert)"
    r"(\(.+\))?!?:"
)


def _commit_type_hint(self) -> str:
    m = _COMMIT_TYPE_RE.match(self.message)
    return m.group(1) if m else ""


CommitInfo.commit_type_hint = _commit_type_hint
