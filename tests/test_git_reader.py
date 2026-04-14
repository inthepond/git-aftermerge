"""Tests for analyzer/git_reader.py."""

from git_aftermerge.analyzer.git_reader import GitReader


def test_get_merge_commits(synthetic_repo):
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    commits = reader.get_merge_commits()
    # Should have: feat_auth, fix_auth, feat_api, revert_auth, feat_utils (not initial)
    assert len(commits) >= 4
    shas = [c.sha for c in commits]
    assert synthetic_repo["shas"]["feat_utils"] in shas
    assert synthetic_repo["shas"]["feat_auth"] in shas


def test_get_diff_stats(synthetic_repo):
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    stats = reader.get_diff_stats(synthetic_repo["shas"]["feat_auth"])
    assert stats.lines_added > 0
    assert "src/auth.py" in stats.file_details


def test_get_diff_stats_no_lines(synthetic_repo):
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    stats = reader.get_diff_stats(synthetic_repo["shas"]["initial"])
    # Initial commit adds README
    assert "README.md" in stats.file_details


def test_get_blame_snapshot(synthetic_repo):
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    head = reader.get_head_sha()
    blame = reader.get_blame_snapshot("src/utils.py", head)
    assert len(blame) > 0
    # All lines should be attributed to feat_utils sha
    for entry in blame.values():
        assert entry.commit_sha == synthetic_repo["shas"]["feat_utils"]


def test_find_reverts(synthetic_repo):
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    reverts = reader.find_reverts()
    assert len(reverts) >= 1
    rev = reverts[0]
    assert rev.reverting_sha == synthetic_repo["shas"]["revert_auth"]
    assert rev.original_sha.startswith(synthetic_repo["shas"]["feat_auth"][:7])


def test_get_head_sha(synthetic_repo):
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    head = reader.get_head_sha()
    assert head == synthetic_repo["shas"]["feat_utils"]


def test_get_file_at_commit(synthetic_repo):
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    content = reader.get_file_at_commit("README.md", synthetic_repo["shas"]["initial"])
    assert content is not None
    assert "Test Repo" in content


def test_get_file_at_commit_missing(synthetic_repo):
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    # src/utils.py doesn't exist at initial commit
    content = reader.get_file_at_commit("src/utils.py", synthetic_repo["shas"]["initial"])
    assert content is None or content == ""
