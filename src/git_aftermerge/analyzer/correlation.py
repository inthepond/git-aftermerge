"""Bug-fix and churn correlation engine.

Bug-introduction linkage follows the SZZ family (Śliwerski/Zimmermann/Zeller
2005 and the AG-SZZ refinement): the suspect lines are exactly the lines a
fix *deleted or replaced* — blame those at the fix's parent and the blamed
commits are the candidates that introduced the defect. Whole-file blame (the
naive approach) implicates everyone who ever touched the file and is mostly
noise. Trivial lines (blank, punctuation-only, comment-only) are excluded, as
in AG-SZZ; whitespace-only changes never implicate because diff and blame
both run whitespace-insensitive.
"""

import re
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Optional

from git_aftermerge.analyzer.git_reader import CommitInfo, GitReader
from git_aftermerge.storage.models import DownstreamEvent, EventType

# Recorded on each link so heuristic changes can be told apart in the facts.
BUG_FIX_DETECTOR = "szz-2"

BUG_KEYWORDS = re.compile(
    r"\b(fix|bug|hotfix|bugfix|issue|error|crash|regression|patch|defect)\b",
    re.IGNORECASE,
)
INCIDENT_KEYWORDS = re.compile(
    r"\b(hotfix|incident|CVE|urgent|emergency|critical|outage)\b",
    re.IGNORECASE,
)

# Lines that carry no blame-worthy semantics: blank, lone braces/brackets,
# and comment-only lines across common languages (AG-SZZ's key refinement).
TRIVIAL_LINE = re.compile(
    r"^\s*$"
    r"|^\s*[{}()\[\];,]+\s*$"
    r"|^\s*(#|//|/\*|\*|\*/|--|<!--|'''|\"\"\")"
)


class CorrelationEngine:
    def __init__(self, repo_path: Path) -> None:
        self.repo = GitReader(repo_path)

    def _implicated_line_counts(
        self,
        commit: CommitInfo,
        commit_map: dict[str, CommitInfo],
        window_days: Optional[int],
    ) -> dict[str, int]:
        """SZZ core: blame exactly the lines ``commit`` deleted, at its parent.

        Returns implicated source sha -> number of non-trivial deleted lines
        it owned. ``window_days`` limits how old an implicated commit may be
        (None = no limit).
        """
        details = (
            commit.file_details or self.repo.get_diff_stats(commit.sha).file_details
        )
        if not details:
            return {}
        try:
            parent_sha = self._get_parent(commit.sha)
        except RuntimeError:
            return {}
        if not parent_sha:
            return {}

        implicated: dict[str, int] = defaultdict(int)
        for file_path in details:
            deleted = self.repo.get_deleted_lines_by_commit(commit.sha, file_path)
            if not deleted:
                continue
            blame = self.repo.get_blame_snapshot(file_path, parent_sha)
            if not blame:
                continue
            for line_no in deleted:
                entry = blame.get(line_no)
                if entry is None:
                    continue
                if TRIVIAL_LINE.match(entry.line_content):
                    continue
                blamed_sha = entry.commit_sha
                if blamed_sha == commit.sha or blamed_sha not in commit_map:
                    continue
                delta = commit.date - commit_map[blamed_sha].date
                if delta <= timedelta(0):
                    continue
                if window_days is not None and delta > timedelta(days=window_days):
                    continue
                implicated[blamed_sha] += 1
        return dict(implicated)

    def find_bug_fix_correlations(
        self, commits: list[CommitInfo], window_days: int = 14
    ) -> dict[str, list[DownstreamEvent]]:
        """
        Returns mapping of original_sha -> list[DownstreamEvent(BUG_FIX_LINKED)].
        lines_affected on each event is the count of the source commit's
        non-trivial lines that the fix deleted or replaced.
        """
        events: dict[str, list[DownstreamEvent]] = defaultdict(list)
        commit_map = {c.sha: c for c in commits}

        bug_fixes = [
            c for c in commits
            if c.commit_type_hint() == "fix" or BUG_KEYWORDS.search(c.message)
        ]

        for fix_commit in bug_fixes:
            for orig_sha, n_lines in self._implicated_line_counts(
                fix_commit, commit_map, window_days
            ).items():
                events[orig_sha].append(DownstreamEvent(
                    event_type=EventType.BUG_FIX_LINKED,
                    commit_sha=fix_commit.sha,
                    date=fix_commit.date.replace(tzinfo=None),
                    lines_affected=n_lines,
                    commit_message=fix_commit.message,
                    author=fix_commit.author,
                ))

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
            for orig_sha, n_lines in self._implicated_line_counts(
                inc_commit, commit_map, window_days=None
            ).items():
                events[orig_sha].append(DownstreamEvent(
                    event_type=EventType.INCIDENT_TAG,
                    commit_sha=inc_commit.sha,
                    date=inc_commit.date.replace(tzinfo=None),
                    lines_affected=n_lines,
                    commit_message=inc_commit.message,
                    author=inc_commit.author,
                ))

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
