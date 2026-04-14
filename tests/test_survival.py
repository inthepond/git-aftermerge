"""Tests for analyzer/survival.py."""

from git_aftermerge.analyzer.survival import SurvivalTracker, detect_ai_author, parse_commit_type
from git_aftermerge.storage.models import Fate


def test_parse_commit_type():
    assert parse_commit_type("feat: add auth") == "feat"
    assert parse_commit_type("fix(auth): edge case") == "fix"
    assert parse_commit_type("refactor!: major change") == "refactor"
    assert parse_commit_type("no prefix here") is None
    assert parse_commit_type("docs: update readme") == "docs"


def test_detect_ai_author():
    tool, model = detect_ai_author("claude-code@anthropic.com")
    assert tool == "claude-code"

    tool2, _ = detect_ai_author("cursor-ai@cursor.com")
    assert tool2 == "cursor"

    tool3, _ = detect_ai_author("human@company.com")
    assert tool3 is None


def test_analyze_commit_stable(synthetic_repo):
    """feat_utils was never modified — should have high survival."""
    repo_dir = synthetic_repo["repo_dir"]
    tracker = SurvivalTracker(repo_dir)
    fate = tracker.analyze_commit(synthetic_repo["shas"]["feat_utils"])
    assert fate is not None
    assert fate.survival_score >= 80
    assert fate.fate in (Fate.SURVIVED, Fate.MODIFIED)
    assert fate.original_lines_added > 0
    assert "src/utils.py" in fate.file_paths


def test_analyze_commit_reverted(synthetic_repo):
    """feat_auth was reverted — surviving_lines should be low or zero."""
    repo_dir = synthetic_repo["repo_dir"]
    tracker = SurvivalTracker(repo_dir)
    reverted = {synthetic_repo["shas"]["feat_auth"]}
    fate = tracker.analyze_commit(synthetic_repo["shas"]["feat_auth"], reverted_shas=reverted)
    assert fate is not None
    assert fate.fate == Fate.REVERTED


def test_analyze_commit_api(synthetic_repo):
    """feat_api adds src/api.py which persists."""
    repo_dir = synthetic_repo["repo_dir"]
    tracker = SurvivalTracker(repo_dir)
    fate = tracker.analyze_commit(synthetic_repo["shas"]["feat_api"])
    assert fate is not None
    assert fate.original_lines_added > 0
    assert "src/api.py" in fate.file_paths


def test_analyze_commit_type_extracted(synthetic_repo):
    repo_dir = synthetic_repo["repo_dir"]
    tracker = SurvivalTracker(repo_dir)
    fate = tracker.analyze_commit(synthetic_repo["shas"]["feat_utils"])
    assert fate is not None
    assert fate.commit_type == "feat"
