"""Revert detection: standard git revert and manual revert heuristics."""

from datetime import timedelta
from pathlib import Path
from typing import Optional

from git_aftermerge.analyzer.git_reader import CommitInfo, GitReader
from git_aftermerge.storage.models import DownstreamEvent, EventType


class RevertDetector:
    def __init__(self, repo_path: Path) -> None:
        self.repo = GitReader(repo_path)

    def find_all_reverts(
        self, commits: list[CommitInfo], since: Optional[str] = None
    ) -> dict[str, list[DownstreamEvent]]:
        """
        Returns a mapping of original_sha -> list[DownstreamEvent] of type REVERT_LINKED.
        """
        events: dict[str, list[DownstreamEvent]] = {}
        commit_map = {c.sha: c for c in commits}

        # Method 1: Standard git revert messages
        standard_reverts = self.repo.find_reverts(since=since)
        for rev in standard_reverts:
            original_sha = self._resolve_partial_sha(rev.original_sha, commit_map)
            if original_sha:
                event = DownstreamEvent(
                    event_type=EventType.REVERT_LINKED,
                    commit_sha=rev.reverting_sha,
                    date=rev.date,
                    lines_affected=0,
                    commit_message=rev.reverting_message,
                    author="",
                )
                events.setdefault(original_sha, []).append(event)

        # Method 2: Manual revert heuristics (>80% of added lines removed within 7 days)
        sha_set = {c.sha for c in commits}
        for commit in commits:
            if commit.sha in {e.commit_sha for evts in events.values() for e in evts}:
                continue  # already detected
            manual = self._detect_manual_revert(commit, commits)
            if manual:
                original_sha, event = manual
                if original_sha in sha_set:
                    events.setdefault(original_sha, []).append(event)

        return events

    def get_reverted_shas(self, commits: list[CommitInfo]) -> set[str]:
        """Return set of SHAs that have been reverted."""
        events = self.find_all_reverts(commits)
        return set(events.keys())

    def _resolve_partial_sha(
        self, partial: str, commit_map: dict[str, CommitInfo],
    ) -> Optional[str]:
        if partial in commit_map:
            return partial
        # Match prefix
        for sha in commit_map:
            if sha.startswith(partial):
                return sha
        # Try git rev-parse
        resolved = self.repo.resolve_sha(partial)
        return resolved

    def _get_file_details(self, commit: CommitInfo) -> dict[str, tuple[int, int]]:
        """Get file details from CommitInfo or fall back to get_diff_stats."""
        if commit.file_details:
            return commit.file_details
        stats = self.repo.get_diff_stats(commit.sha)
        return stats.file_details

    def _detect_manual_revert(
        self, candidate: CommitInfo, all_commits: list[CommitInfo]
    ) -> Optional[tuple[str, DownstreamEvent]]:
        """
        Check if candidate looks like a manual revert of some earlier commit:
        removes >80% of lines added by another commit, within 7 days.
        """
        if candidate.lines_added > 0 or candidate.lines_deleted == 0:
            return None

        candidate_details = self._get_file_details(candidate)
        if not candidate_details:
            return None

        candidate_files = set(candidate_details.keys())
        window_start = candidate.date - timedelta(days=7)

        for earlier in all_commits:
            if earlier.sha == candidate.sha:
                continue
            if earlier.date >= candidate.date:
                continue
            if earlier.date < window_start:
                continue
            if not earlier.lines_added:
                continue

            earlier_details = self._get_file_details(earlier)
            earlier_files = set(earlier_details.keys())

            # Must overlap in files
            shared_files = candidate_files & earlier_files
            if not shared_files:
                continue

            # Check if candidate deletes most of what earlier added
            total_added_by_earlier = sum(
                v[0] for k, v in earlier_details.items() if k in shared_files
            )
            total_deleted_by_candidate = sum(
                v[1] for k, v in candidate_details.items() if k in shared_files
            )

            if total_added_by_earlier > 0:
                revert_ratio = total_deleted_by_candidate / total_added_by_earlier
                if revert_ratio > 0.8:
                    event = DownstreamEvent(
                        event_type=EventType.REVERT_LINKED,
                        commit_sha=candidate.sha,
                        date=candidate.date.replace(tzinfo=None),
                        lines_affected=total_deleted_by_candidate,
                        commit_message=candidate.message,
                        author=candidate.author,
                    )
                    return earlier.sha, event

        return None
