"""Line-level survival tracking for commits."""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from git_aftermerge.analyzer.git_reader import CommitInfo, GitReader
from git_aftermerge.storage.models import CommitFate, Fate

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
    """Heuristically detect AI tool from author email/name."""
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


class SurvivalTracker:
    def __init__(self, repo_path: Path) -> None:
        self.repo = GitReader(repo_path)

    def analyze_commit(
        self,
        commit_sha: str,
        reverted_shas: Optional[set[str]] = None,
        commit_info: Optional[CommitInfo] = None,
    ) -> Optional[CommitFate]:
        """Analyze survival of a commit's lines at current HEAD."""
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
            author = commit_info.author
            date = commit_info.date
            message = commit_info.message
        else:
            output = self.repo._run(
                "log", "--format=%ae\x1f%aI\x1f%s", "-1", commit_sha
            )
            parts = output.strip().split("\x1f")
            if len(parts) >= 3:
                author, date_str, message = parts[0], parts[1], parts[2]
                try:
                    date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except ValueError:
                    date = datetime.utcnow()
            else:
                return None

        author_tool, author_model = detect_ai_author(author)
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

            # Get blame at HEAD for this file
            blame = self.repo.get_blame_snapshot(file_path, head_sha)
            if not blame:
                # File was deleted — no surviving lines
                continue

            # Count lines in HEAD blame still attributed to this commit
            surviving = sum(
                1 for entry in blame.values() if entry.commit_sha == commit_sha
            )
            total_surviving_lines += surviving

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
            author_tool=author_tool,
            author_model=author_model,
            merged_at=merged_at.replace(tzinfo=None),
            original_lines_added=total_original_lines,
            original_lines_modified=total_deleted,
            surviving_lines=total_surviving_lines,
            survival_score=score,
            fate=fate_val,
            days_since_merge=days_since,
            file_paths=file_paths,
            commit_type=commit_type,
            ai_attributed=author_tool is not None,
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
