"""Line-level survival tracking for commits.

Produces two kinds of output:

- **Facts** for storage: ``CommitFact`` (via :func:`build_commit_fact`) and
  checkpoint :class:`SurvivalObservation` rows (via
  :meth:`SurvivalTracker.observe_commit`) — blame snapshots at fixed ages
  (7/30/90/180/365 days) and at HEAD. Survival curves are queries over
  these observations.
- A **derived** ``CommitFate`` via :meth:`SurvivalTracker.analyze_commit`
  (HEAD-only convenience used by the CLI ``fate`` fallback and tests).
"""

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from git_aftermerge.analyzer.attribution import Attribution, attribute
from git_aftermerge.analyzer.git_reader import BlameEntry, CommitInfo, GitReader
from git_aftermerge.storage.models import (
    CHECKPOINT_DAYS,
    HEAD_LABEL,
    Cohort,
    CommitFact,
    CommitFate,
    Fate,
    FileChange,
    SurvivalObservation,
    checkpoint_label,
)

BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".tiff", ".webp", ".svg",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".gz", ".tar", ".bz2", ".xz", ".7z", ".rar",
    ".bin", ".exe", ".dll", ".so", ".dylib", ".a",
    ".pyc", ".pyo", ".class", ".jar", ".war",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".ogg",
    ".sqlite", ".db",
})


def _is_binary_path(path: str) -> bool:
    return Path(path).suffix.lower() in BINARY_EXTENSIONS


_COMMIT_TYPE_RE = re.compile(
    r"^(feat|fix|refactor|test|docs|chore|style|perf|ci|build|revert)"
    r"(\(.+\))?!?:"
)


def parse_commit_type(message: str) -> Optional[str]:
    """Extract conventional commit type prefix."""
    match = _COMMIT_TYPE_RE.match(message)
    if match:
        return match.group(1)
    return None


def detect_ai_author(author: str) -> tuple[Optional[str], Optional[str]]:
    """Back-compat shim: detect AI tool from an author string only.

    Prefer :func:`git_aftermerge.analyzer.attribution.attribute`, which also
    reads commit-message trailers.
    """
    author_lower = author.lower()
    if "claude" in author_lower:
        return "claude-code", None
    if "cursor" in author_lower:
        return "cursor", None
    if "copilot" in author_lower:
        return "github-copilot", None
    if "codex" in author_lower:
        return "codex", None
    return None, None


def build_commit_fact(
    commit: CommitInfo,
    attribution: Optional[Attribution] = None,
    prev_touch: Optional[dict[str, datetime]] = None,
) -> CommitFact:
    """Assemble the fact row for a commit from its info + attribution.

    ``prev_touch`` maps file path -> when that path was last touched before
    this commit (from the full-history touch pass); absent paths mean the
    file was created by this commit.
    """
    attr = attribution or attribute(commit.author_name, commit.author, commit.body)
    prev_touch = prev_touch or {}
    files = [
        FileChange(
            path=path,
            lines_added=added,
            lines_deleted=deleted,
            prev_touch_at=prev_touch.get(path),
        )
        for path, (added, deleted) in commit.file_details.items()
        if not _is_binary_path(path)
    ]
    return CommitFact(
        sha=commit.sha,
        author_name=commit.author_name,
        author_email=commit.author,
        authored_at=commit.date.replace(tzinfo=None),
        subject=commit.message,
        cohort=attr.cohort.value,
        author_tool=attr.tool,
        author_model=attr.model,
        ai_session=attr.session_id,
        human_ratio=attr.human_ratio,
        attribution_source=attr.source,
        lines_added=sum(f.lines_added for f in files),
        lines_deleted=sum(f.lines_deleted for f in files),
        files=files,
    )


class SurvivalTracker:
    def __init__(self, repo_path: Path) -> None:
        self.repo = GitReader(repo_path)
        # Blame results are immutable per (sha, path): cache them so many
        # commits observed at the same checkpoint state share one blame call.
        self._blame_cache: dict[tuple[str, str], dict[int, BlameEntry]] = {}
        # rev-list lookups quantized by day so same-day checkpoints share one call.
        self._rev_cache: dict[str, Optional[str]] = {}
        self._ancestor_cache: dict[tuple[str, str], bool] = {}

    def _blame(self, file_path: str, at_sha: str) -> dict[int, BlameEntry]:
        key = (at_sha, file_path)
        if key not in self._blame_cache:
            self._blame_cache[key] = self.repo.get_blame_snapshot(file_path, at_sha)
        return self._blame_cache[key]

    def _rev_at(self, when: datetime) -> Optional[str]:
        day_key = when.strftime("%Y-%m-%d")
        if day_key not in self._rev_cache:
            self._rev_cache[day_key] = self.repo.rev_before(when)
        return self._rev_cache[day_key]

    def _contains(self, commit_sha: str, at_sha: str) -> bool:
        key = (commit_sha, at_sha)
        if key not in self._ancestor_cache:
            self._ancestor_cache[key] = self.repo.is_ancestor(commit_sha, at_sha)
        return self._ancestor_cache[key]

    def _count_surviving(
        self, commit_sha: str, file_paths: list[str], at_sha: str
    ) -> int:
        surviving = 0
        for path in file_paths:
            blame = self._blame(path, at_sha)
            surviving += sum(
                1 for entry in blame.values() if entry.commit_sha == commit_sha
            )
        return surviving

    def observe_commit(
        self,
        commit_sha: str,
        authored_at: datetime,
        file_paths: list[str],
        skip_labels: Optional[set[str]] = None,
        now: Optional[datetime] = None,
    ) -> list[SurvivalObservation]:
        """Blame the commit's files at every due checkpoint and at HEAD.

        Only checkpoints the commit has already aged past are observed
        (younger ones are right-censored, not zero). ``skip_labels`` lets an
        incremental scan skip historical checkpoints it already recorded —
        those observations are immutable.
        """
        skip_labels = skip_labels or set()
        now = now or datetime.utcnow()
        authored_at = authored_at.replace(tzinfo=None)
        tracked = [p for p in file_paths if not _is_binary_path(p)]
        if not tracked:
            return []

        observations = []
        # Quantize checkpoint instants to day boundaries so commits from the
        # same day share cached rev-list and blame results.
        for days in CHECKPOINT_DAYS:
            label = checkpoint_label(days)
            if label in skip_labels:
                continue
            checkpoint_at = (authored_at + timedelta(days=days)).replace(
                hour=23, minute=59, second=59, microsecond=0
            )
            if checkpoint_at > now:
                continue
            at_sha = self._rev_at(checkpoint_at)
            if not at_sha:
                continue
            # Only observe at states that actually contain the commit. With
            # squash/rebase flows the resolved checkpoint state can predate
            # the commit landing; blaming there would record a false death
            # instead of right-censoring.
            if not self._contains(commit_sha, at_sha):
                continue
            observations.append(SurvivalObservation(
                commit_sha=commit_sha,
                label=label,
                checkpoint_days=days,
                observed_sha=at_sha,
                observed_at=checkpoint_at,
                surviving_lines=self._count_surviving(commit_sha, tracked, at_sha),
            ))

        # HEAD is always (re-)observed: it moves with every scan.
        head_sha = self.repo.get_head_sha()
        if not self._contains(commit_sha, head_sha):
            return observations
        observations.append(SurvivalObservation(
            commit_sha=commit_sha,
            label=HEAD_LABEL,
            checkpoint_days=max((now - authored_at).days, 0),
            observed_sha=head_sha,
            observed_at=now,
            surviving_lines=self._count_surviving(commit_sha, tracked, head_sha),
        ))
        return observations

    def analyze_commit(
        self,
        commit_sha: str,
        reverted_shas: Optional[set[str]] = None,
        commit_info: Optional[CommitInfo] = None,
    ) -> Optional[CommitFate]:
        """Derive a HEAD-only CommitFate for a commit (convenience view)."""
        reverted_shas = reverted_shas or set()

        # Use pre-fetched file_details from commit_info if available
        if commit_info and commit_info.file_details:
            file_details = commit_info.file_details
        else:
            diff_stats = self.repo.get_diff_stats(commit_sha)
            file_details = diff_stats.file_details

        if not file_details:
            return None

        # Use pre-fetched commit info, or fall back to git log -1
        if commit_info is not None:
            author_name = commit_info.author_name
            author = commit_info.author
            date = commit_info.date
            message = commit_info.message
            body = commit_info.body or commit_info.message
        else:
            output = self.repo._run(
                "log", "--format=%an\x1f%ae\x1f%cI\x1f%B", "-1", commit_sha
            )
            parts = output.strip().split("\x1f")
            if len(parts) < 4:
                return None
            author_name, author, date_str, body = parts[0], parts[1], parts[2], parts[3]
            message = body.splitlines()[0] if body else ""
            try:
                date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except ValueError:
                date = datetime.utcnow()

        attr = attribute(author_name, author, body)
        commit_type = parse_commit_type(message)
        head_sha = self.repo.get_head_sha()
        merged_at = date

        total_original_lines = 0
        total_surviving_lines = 0
        file_paths = []
        total_deleted = 0

        for file_path, (added, deleted) in file_details.items():
            total_deleted += deleted
            if added == 0:
                continue
            if _is_binary_path(file_path):
                continue
            file_paths.append(file_path)
            total_original_lines += added

            # Count lines in HEAD blame still attributed to this commit
            blame = self._blame(file_path, head_sha)
            if not blame:
                # File was deleted — no surviving lines
                continue
            total_surviving_lines += sum(
                1 for entry in blame.values() if entry.commit_sha == commit_sha
            )

        if total_original_lines == 0:
            # Only modifications, no new lines — treat as 100% survival
            fate_val = Fate.SURVIVED
            score = 100
        else:
            ratio = total_surviving_lines / total_original_lines
            fate_val = _determine_fate(commit_sha, ratio, reverted_shas)
            score = int(ratio * 100)

        days_since = (datetime.utcnow() - merged_at.replace(tzinfo=None)).days

        return CommitFate(
            commit_sha=commit_sha,
            author=author,
            author_tool=attr.tool,
            author_model=attr.model,
            merged_at=merged_at.replace(tzinfo=None),
            original_lines_added=total_original_lines,
            original_lines_modified=total_deleted,
            surviving_lines=total_surviving_lines,
            survival_score=score,
            fate=fate_val,
            days_since_merge=days_since,
            file_paths=file_paths,
            commit_type=commit_type,
            ai_attributed=attr.cohort == Cohort.AI_AGENT,
            cohort=attr.cohort.value,
            ai_session=attr.session_id,
            human_ratio=attr.human_ratio,
        )


def _determine_fate(
    commit_sha: str,
    survival_ratio: float,
    reverted_shas: set[str],
) -> Fate:
    if commit_sha in reverted_shas:
        return Fate.REVERTED
    if survival_ratio >= 0.9:
        return Fate.SURVIVED
    if survival_ratio >= 0.3:
        return Fate.MODIFIED
    if survival_ratio == 0:
        return Fate.SUPERSEDED
    return Fate.DECAYED
