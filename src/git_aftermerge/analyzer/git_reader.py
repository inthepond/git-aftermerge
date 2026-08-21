"""Git history reading: log, blame, diff."""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Histogram diff + whitespace-skipping everywhere: different diff algorithms
# produce different line counts, and formatting/indentation churn would
# otherwise pollute the survival signal.
DIFF_FLAGS = ["--diff-algorithm=histogram", "-w"]

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class CommitInfo:
    sha: str
    author: str                 # author email (v1 compatibility)
    date: datetime
    message: str                # subject line
    files_changed: list[str] = field(default_factory=list)
    lines_added: int = 0
    lines_deleted: int = 0
    is_merge: bool = False
    # path -> (added, deleted)
    file_details: dict[str, tuple[int, int]] = field(default_factory=dict)
    author_name: str = ""
    body: str = ""              # full message: subject + body + trailers


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


@dataclass
class TouchRecord:
    """One commit's footprint in the full-history touch pass."""
    sha: str
    date: datetime
    paths: list[str] = field(default_factory=list)


def _parse_iso(date_str: str) -> datetime:
    """Parse an ISO date from git and normalize to naive UTC.

    All datetimes in the pipeline are naive UTC so fact rows, checkpoint
    arithmetic, and event comparisons never mix aware and naive values.
    """
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        return datetime.utcnow()
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class GitReader:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    def _run(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
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
        """List commits on the default branch with diff stats in a single git call.

        Fetches the full message body (%B) so trailer-based attribution
        (Co-Authored-By, Generated-By, …) has raw material to work with.

        Dates are committer dates (%cI) — when the change actually landed.
        Under squash/rebase PR flows the author date is when the code was
        written on a branch, weeks before it reached the mainline; using it
        as the survival clock produces false early deaths.
        """
        fmt = "__COMMIT__%H\x1f%an\x1f%ae\x1f%cI\x1f%P\x1f%B\x1e"
        args = ["log", f"--format={fmt}", "--numstat", "--no-merges", *DIFF_FLAGS]
        if since:
            args += [f"--since={since}"]
        output = self._run(*args)
        if not output.strip():
            return []

        commits: list[CommitInfo] = []
        for chunk in output.split("__COMMIT__"):
            if not chunk.strip():
                continue
            head, sep, tail = chunk.partition("\x1e")
            parts = head.split("\x1f")
            if len(parts) < 6 or not _SHA_RE.match(parts[0]):
                continue
            sha, author_name, author_email, date_str, parents_str, body = parts[:6]
            body = body.strip("\n")
            commit = CommitInfo(
                sha=sha,
                author=author_email,
                date=_parse_iso(date_str),
                message=body.splitlines()[0] if body else "",
                is_merge=len(parents_str.split()) > 1,
                author_name=author_name,
                body=body,
            )
            for line in tail.splitlines():
                nparts = line.split("\t")
                if len(nparts) != 3:
                    continue
                added_str, deleted_str, path = nparts
                if added_str == "-" or deleted_str == "-":
                    continue  # binary file
                added = int(added_str)
                deleted = int(deleted_str)
                commit.file_details[path] = (added, deleted)
                commit.lines_added += added
                commit.lines_deleted += deleted
            commit.files_changed = list(commit.file_details.keys())
            commits.append(commit)

        return commits

    def get_touch_history(self) -> list[TouchRecord]:
        """Full history, oldest first, with the paths each commit touched.

        One cheap pass that lets the scanner compute, for every (commit, file),
        when that file was previously modified — the raw fact behind maturity
        tiers. Renames appear as a fresh path (known limitation).
        """
        output = self._run(
            "log", "--reverse", "--format=__C__%H\x1f%cI", "--name-only", "--no-merges"
        )
        records: list[TouchRecord] = []
        current: Optional[TouchRecord] = None
        for line in output.splitlines():
            if line.startswith("__C__"):
                if current is not None:
                    records.append(current)
                parts = line[len("__C__"):].split("\x1f")
                if len(parts) < 2 or not _SHA_RE.match(parts[0]):
                    current = None
                    continue
                current = TouchRecord(sha=parts[0], date=_parse_iso(parts[1]))
            elif line.strip() and current is not None:
                current.paths.append(line.strip())
        if current is not None:
            records.append(current)
        return records

    def rev_before(self, when: datetime, ref: str = "HEAD") -> Optional[str]:
        """The most recent commit on ref at or before a point in time."""
        output = self._run(
            "rev-list", "-1", f"--before={when.isoformat()}", ref, check=False
        )
        sha = output.strip()
        return sha if sha else None

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """Whether ``ancestor`` is contained in ``descendant``'s history."""
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=self.repo_path,
            capture_output=True,
        )
        return result.returncode == 0

    def get_blame_snapshot(self, file_path: str, at_commit: str) -> dict[int, BlameEntry]:
        """Run git blame --porcelain for a file at a specific commit."""
        output = self._run(
            "blame", "--porcelain", "-w", at_commit, "--", file_path, check=False
        )
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
        output = self._run(
            "diff-tree", "--numstat", "-r", "--root", *DIFF_FLAGS, commit_sha
        )
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
                reverts.append(RevertInfo(
                    reverting_sha=sha,
                    original_sha=original_sha,
                    reverting_message=subject,
                    date=_parse_iso(date_str),
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
            commits.append(CommitInfo(
                sha=sha, author=author, date=_parse_iso(date_str), message=message
            ))
        return commits

    def get_added_lines_by_commit(self, commit_sha: str, file_path: str) -> list[int]:
        """Return line numbers added by commit in file (1-indexed)."""
        # Use diff to find which lines were added in the commit for this file
        output = self._run(
            "diff", *DIFF_FLAGS, f"{commit_sha}^", commit_sha, "--", file_path,
            check=False,
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
            commits.append(CommitInfo(
                sha=sha, author=author, date=_parse_iso(date_str), message=message
            ))
        return commits
