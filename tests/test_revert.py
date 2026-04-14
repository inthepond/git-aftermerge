"""Tests for analyzer/revert.py."""

from git_aftermerge.analyzer.git_reader import GitReader
from git_aftermerge.analyzer.revert import RevertDetector
from git_aftermerge.storage.models import EventType


def test_standard_revert_detected(synthetic_repo):
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    detector = RevertDetector(repo_dir)
    commits = reader.get_merge_commits()

    events = detector.find_all_reverts(commits)
    feat_auth_sha = synthetic_repo["shas"]["feat_auth"]

    assert feat_auth_sha in events
    revert_events = events[feat_auth_sha]
    assert len(revert_events) >= 1
    assert all(e.event_type == EventType.REVERT_LINKED for e in revert_events)


def test_reverted_shas_set(synthetic_repo):
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    detector = RevertDetector(repo_dir)
    commits = reader.get_merge_commits()

    reverted = detector.get_reverted_shas(commits)
    feat_auth_sha = synthetic_repo["shas"]["feat_auth"]
    assert feat_auth_sha in reverted


def test_non_revert_not_detected(synthetic_repo):
    """feat_api and feat_utils should not be in reverted set."""
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    detector = RevertDetector(repo_dir)
    commits = reader.get_merge_commits()

    reverted = detector.get_reverted_shas(commits)
    assert synthetic_repo["shas"]["feat_utils"] not in reverted
    assert synthetic_repo["shas"]["feat_api"] not in reverted
