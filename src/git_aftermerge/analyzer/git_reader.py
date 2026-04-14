"""Git history reading: log, blame, diff."""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class CommitInfo:
    sha: str
    author: str
    date: datetime
    message: str
    files_changed: list[str] = field(default_factory=list)
    lines_added: int = 0
    lines_deleted: int = 0
    is_merge: bool = False
    # path -> (added, deleted)
    file_details: dict[str, tuple[int, int]] = field(default_factory=dict)


@dataclass
class BlameEntry:
    commit_sha: str
    author: str
    date: datetime
    line_content: str


@dataclass
class DiffStats:
    files_changed: int
    lines_added: int
    lines_deleted: int
    # path -> (added, deleted)
    file_details: dict[str, tuple[int, int]] = field(default_factory=dict)


@dataclass
class RevertInfo:
    reverting_sha: str
    original_sha: str
    reverting_message: str
    date: datetime


class GitReader:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    def _run(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
        return result.stdout

    def get_default_branch(self) -> str:
        """Return the default branch name (main or master)."""
        for branch in ("main", "master"):
            result = self._run("rev-parse", "--verify", branch, check=False)
            if result.strip():
                return branch
        # Fall back to current HEAD branch
        result = self._run("rev-parse", "--abbrev-ref", "HEAD")
        return result.strip()

    def get_merge_commits(self, since: Optional[str] = None) -> list[CommitInfo]:
        """List commits on the default branch with diff stats in a single git call."""
        args = [
            "log",
            "--format=__COMMIT__%H\x1f%ae\x1f%aI\x1f%s\x1f%P",
            "--numstat",
            "--no-merges",
        ]
        if since:
            args += [f"--since={since}"]
        output = self._run(*args)
        if not output.strip():
            return []

        commits: list[CommitInfo] = []
        current: Optional[CommitInfo] = None

        for line in output.splitlines():
            if line.startswith("__COMMIT__"):
                if current is not None:
                    current.files_changed = list(current.file_details.keys())
                    commits.append(current)
                raw = line[len("__COMMIT__"):]
                parts = raw.split("\x1f")
                if len(parts) < 4:
                    current = None
                    continue
                sha, author, date_str, message = parts[0], parts[1], parts[2], parts[3]
                parents = parts[4].split() if len(parts) > 4 else []
                try:
                    date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except ValueError:
                    date = datetime.utcnow()
                current = CommitInfo(
                    sha=sha, author=author, date=date, message=message,
                    is_merge=len(parents) > 1,
                )
            elif line.strip() and current is not None:
                parts = line.split("\t")
                if len(parts) == 3:
                    added_str, deleted_str, path = parts
                    if added_str == "-" or deleted_str == "-":
                        continue  # binary file
                    added = int(added_str)
                    deleted = int(deleted_str)
                    current.file_details[path] = (added, deleted)
                    current.lines_added += added
                    current.lines_deleted += deleted

        if current is not None:
            current.files_changed = list(current.file_details.keys())
            commits.append(current)

        return commits

    def get_blame_snapshot(self, file_path: str, at_commit: str) -> dict[int, BlameEntry]:
        """Run git blame --porcelain for a file at a specific commit."""
        output = self._run("blame", "--porcelain", at_commit, "--", file_path, check=False)
        if not output.strip():
            return {}

        result: dict[int, BlameEntry] = {}
        lines = output.splitlines()
        i = 0
        current_sha = ""
        current_author = ""
        current_date = datetime.utcnow()

        while i < len(lines):
            line = lines[i]
            # Header line: <sha> <orig_line> <final_line> [<num_lines>]
            header_match = re.match(r"^([0-9a-f]{40}) \d+ (\d+)", line)
            if header_match:
                current_sha = header_match.group(1)
                final_line = int(header_match.group(2))
                i += 1
                # Read key-value pairs until we hit the line content
                author = ""
                date = datetime.utcnow()
                while i < len(lines) and not lines[i].startswith("\t"):
                    kv = lines[i]
                    if kv.startswith("author "):
                        author = kv[7:]
                    elif kv.startswith("author-time "):
                        try:
                            date = datetime.utcfromtimestamp(int(kv[12:]))
                        except ValueError:
                            pass
                    i += 1
                current_author = author
                current_date = date
                # The tab-prefixed line is the actual content
                if i < len(lines) and lines[i].startswith("\t"):
                    content = lines[i][1:]
                    result[final_line] = BlameEntry(
                        commit_sha=current_sha,
                        author=current_author,
                        date=current_date,
                        line_content=content,
                    )
                    i += 1
            else:
                i += 1

        return result

    def get_diff_stats(self, commit_sha: str) -> DiffStats:
        """Return files changed, lines added/deleted for a commit."""
        output = self._run("diff-tree", "--numstat", "-r", "--root", commit_sha)
        file_details: dict[str, tuple[int, int]] = {}
        total_added = 0
        total_deleted = 0
        for line in output.strip().splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added_str, deleted_str, path = parts
            if added_str == "-" or deleted_str == "-":
                # Binary file
                continue
            added = int(added_str)
            deleted = int(deleted_str)
            file_details[path] = (added, deleted)
            total_added += added
            total_deleted += deleted
        return DiffStats(
            files_changed=len(file_details),
            lines_added=total_added,
            lines_deleted=total_deleted,
            file_details=file_details,
        )

    def get_file_at_commit(self, file_path: str, commit_sha: str) -> Optional[str]:
        """Return file contents at a specific commit."""
        output = self._run("show", f"{commit_sha}:{file_path}", check=False)
        if not output:
            check = self._run("cat-file", "-e", f"{commit_sha}:{file_path}", check=False)
            if check == "":
                return None
        return output if output else None

    def find_reverts(self, since: Optional[str] = None) -> list[RevertInfo]:
        """Find commits that are standard git reverts."""
        args = ["log", "--format=%H\x1f%aI\x1f%s\x1f%b\x1f\x1e", "--no-merges"]
        if since:
            args += [f"--since={since}"]
        output = self._run(*args)

        reverts = []
        for block in output.split("\x1e"):
            block = block.strip()
            if not block:
                continue
            parts = block.split("\x1f")
            if len(parts) < 3:
                continue
            sha, date_str, subject = parts[0], parts[1], parts[2]
            body = parts[3] if len(parts) > 3 else ""

            if not subject.startswith("Revert"):
                continue

            # Look for "This reverts commit <sha>" in body
            match = re.search(r"This reverts commit ([0-9a-f]{7,40})", body, re.IGNORECASE)
            if match:
                original_sha = match.group(1)
                try:
                    date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except ValueError:
                    date = datetime.utcnow()
                reverts.append(RevertInfo(
                    reverting_sha=sha,
                    original_sha=original_sha,
                    reverting_message=subject,
                    date=date,
                ))

        return reverts

    def get_commits_touching_lines(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        since_commit: str,
    ) -> list[CommitInfo]:
        """Find commits that modified specific lines after a given commit."""
        output = self._run(
            "log", f"{since_commit}..HEAD",
            "--format=%H\x1f%ae\x1f%aI\x1f%s",
            f"-L{start_line},{end_line}:{file_path}",
            check=False,
        )
        commits = []
        for line in output.strip().splitlines():
            if not re.match(r"^[0-9a-f]{40}", line):
                continue
            parts = line.split("\x1f")
            if len(parts) < 4:
                continue
            sha, author, date_str, message = parts
            try:
                date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except ValueError:
                date = datetime.utcnow()
            commits.append(CommitInfo(sha=sha, author=author, date=date, message=message))
        return commits

    def get_added_lines_by_commit(self, commit_sha: str, file_path: str) -> list[int]:
        """Return line numbers added by commit in file (1-indexed)."""
        # Use diff to find which lines were added in the commit for this file
        output = self._run(
            "diff", f"{commit_sha}^", commit_sha, "--", file_path, check=False
        )
        if not output:
            return []

        added_lines = []
        current_new_line = 0
        for line in output.splitlines():
            hunk_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if hunk_match:
                current_new_line = int(hunk_match.group(1))
                continue
            if line.startswith("+") and not line.startswith("+++"):
                added_lines.append(current_new_line)
                current_new_line += 1
            elif line.startswith("-"):
                pass  # deleted lines don't advance new line counter
            elif not line.startswith("\\"):
                current_new_line += 1

        return added_lines

    def resolve_sha(self, ref: str) -> Optional[str]:
        """Resolve a ref to a full SHA."""
        output = self._run("rev-parse", "--verify", ref, check=False)
        sha = output.strip()
        return sha if sha else None

    def get_head_sha(self) -> str:
        return self._run("rev-parse", "HEAD").strip()

    def get_commit_count(self, since: Optional[str] = None) -> int:
        args = ["rev-list", "--count", "HEAD", "--no-merges"]
        if since:
            args += [f"--since={since}"]
        return int(self._run(*args).strip() or "0")

    def get_files_at_commit(self, commit_sha: str) -> list[str]:
        """Return all files in the repo at a given commit."""
        output = self._run("ls-tree", "-r", "--name-only", commit_sha)
        return [f for f in output.splitlines() if f]

    def commit_exists_in_history(self, sha: str) -> bool:
        self._run("cat-file", "-e", sha, check=False)
        return True

    def get_file_modification_history(
        self, file_path: str, since: Optional[str] = None
    ) -> list[CommitInfo]:
        """All commits that touched a specific file."""
        args = ["log", "--format=%H\x1f%ae\x1f%aI\x1f%s", "--no-merges", "--", file_path]
        if since:
            args.insert(1, f"--since={since}")
        output = self._run(*args)
        commits = []
        for line in output.strip().splitlines():
            parts = line.split("\x1f")
            if len(parts) < 4:
                continue
            sha, author, date_str, message = parts
            try:
                date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except ValueError:
                date = datetime.utcnow()
            commits.append(CommitInfo(sha=sha, author=author, date=date, message=message))
        return commits
