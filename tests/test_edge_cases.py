"""Edge-case tests: binary, renames, empty commits, shallow clones, large files, submodules."""

import time

from git_aftermerge.analyzer.git_reader import GitReader
from git_aftermerge.analyzer.survival import SurvivalTracker


class TestBinaryFiles:
    def test_diff_stats_skips_binary(self, binary_file_repo):
        reader = GitReader(binary_file_repo["repo_dir"])
        stats = reader.get_diff_stats(binary_file_repo["shas"]["add_binary"])
        # Binary file should not appear in file_details (git reports - for binary)
        assert "image.png" not in stats.file_details

    def test_merge_commits_skips_binary_in_numstat(self, binary_file_repo):
        reader = GitReader(binary_file_repo["repo_dir"])
        commits = reader.get_merge_commits()
        binary_commit = next(c for c in commits if c.sha == binary_file_repo["shas"]["add_binary"])
        assert "image.png" not in binary_commit.file_details

    def test_survival_skips_binary(self, binary_file_repo):
        tracker = SurvivalTracker(binary_file_repo["repo_dir"])
        fate = tracker.analyze_commit(binary_file_repo["shas"]["add_binary"])
        # Commit only added a binary file, so no trackable text lines
        assert fate is None

    def test_text_commit_still_works_with_binary_repo(self, binary_file_repo):
        tracker = SurvivalTracker(binary_file_repo["repo_dir"])
        fate = tracker.analyze_commit(binary_file_repo["shas"]["add_text"])
        assert fate is not None
        assert fate.original_lines_added > 0


class TestRenamedFiles:
    def test_diff_stats_shows_rename(self, renamed_file_repo):
        reader = GitReader(renamed_file_repo["repo_dir"])
        stats = reader.get_diff_stats(renamed_file_repo["shas"]["rename"])
        # git diff-tree --numstat shows rename as delete + add or as a rename entry
        assert stats.files_changed >= 1

    def test_merge_commits_includes_rename(self, renamed_file_repo):
        reader = GitReader(renamed_file_repo["repo_dir"])
        commits = reader.get_merge_commits()
        rename_commit = next(c for c in commits if c.sha == renamed_file_repo["shas"]["rename"])
        assert len(rename_commit.files_changed) >= 1

    def test_survival_original_commit(self, renamed_file_repo):
        tracker = SurvivalTracker(renamed_file_repo["repo_dir"])
        fate = tracker.analyze_commit(renamed_file_repo["shas"]["add_file"])
        # Lines were moved to new file; blame under old path may show 0 surviving
        # The key assertion: this doesn't crash
        assert fate is not None


class TestEmptyCommits:
    def test_diff_stats_empty(self, empty_commit_repo):
        reader = GitReader(empty_commit_repo["repo_dir"])
        stats = reader.get_diff_stats(empty_commit_repo["shas"]["empty"])
        assert stats.files_changed == 0
        assert stats.lines_added == 0

    def test_merge_commits_includes_empty(self, empty_commit_repo):
        reader = GitReader(empty_commit_repo["repo_dir"])
        commits = reader.get_merge_commits()
        empty_commit = next(
            (c for c in commits if c.sha == empty_commit_repo["shas"]["empty"]), None
        )
        if empty_commit:
            assert empty_commit.lines_added == 0

    def test_analyze_empty_commit_returns_none(self, empty_commit_repo):
        tracker = SurvivalTracker(empty_commit_repo["repo_dir"])
        fate = tracker.analyze_commit(empty_commit_repo["shas"]["empty"])
        assert fate is None


class TestShallowClone:
    def test_get_merge_commits_shallow(self, shallow_clone_repo):
        reader = GitReader(shallow_clone_repo["repo_dir"])
        commits = reader.get_merge_commits()
        assert len(commits) >= 1

    def test_get_diff_stats_shallow(self, shallow_clone_repo):
        reader = GitReader(shallow_clone_repo["repo_dir"])
        commits = reader.get_merge_commits()
        if commits:
            stats = reader.get_diff_stats(commits[0].sha)
            assert isinstance(stats.files_changed, int)

    def test_blame_works_on_shallow(self, shallow_clone_repo):
        reader = GitReader(shallow_clone_repo["repo_dir"])
        head = reader.get_head_sha()
        files = reader.get_files_at_commit(head)
        if files:
            blame = reader.get_blame_snapshot(files[0], head)
            assert isinstance(blame, dict)


class TestLargeFile:
    def test_blame_large_file(self, large_file_repo):
        """Ensure blame completes in reasonable time for 1000+ line file."""
        reader = GitReader(large_file_repo["repo_dir"])
        head = reader.get_head_sha()
        start = time.monotonic()
        blame = reader.get_blame_snapshot("large_file.py", head)
        elapsed = time.monotonic() - start
        assert len(blame) >= 600
        assert elapsed < 30  # generous limit for CI

    def test_survival_large_file(self, large_file_repo):
        tracker = SurvivalTracker(large_file_repo["repo_dir"])
        fate = tracker.analyze_commit(large_file_repo["shas"]["add_large"])
        assert fate is not None
        assert fate.original_lines_added >= 600
        # Some lines were modified by the later commit, so survival < 100%
        assert fate.surviving_lines < fate.original_lines_added

    def test_merge_commits_large_numstat(self, large_file_repo):
        reader = GitReader(large_file_repo["repo_dir"])
        commits = reader.get_merge_commits()
        large_commit = next(c for c in commits if c.sha == large_file_repo["shas"]["add_large"])
        assert large_commit.lines_added >= 600
        assert "large_file.py" in large_commit.file_details


class TestSubmodule:
    def test_diff_stats_with_submodule(self, submodule_repo):
        reader = GitReader(submodule_repo["repo_dir"])
        stats = reader.get_diff_stats(submodule_repo["shas"]["add_submodule"])
        # Should not crash; submodule gitlink shows as binary (- -) in numstat
        assert isinstance(stats.files_changed, int)

    def test_get_merge_commits_with_submodule(self, submodule_repo):
        reader = GitReader(submodule_repo["repo_dir"])
        commits = reader.get_merge_commits()
        assert len(commits) >= 1

    def test_blame_ignores_submodule_path(self, submodule_repo):
        reader = GitReader(submodule_repo["repo_dir"])
        head = reader.get_head_sha()
        # Blaming a submodule path should return empty, not crash
        blame = reader.get_blame_snapshot("vendor/sub", head)
        assert isinstance(blame, dict)
