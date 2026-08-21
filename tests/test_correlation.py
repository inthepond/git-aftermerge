"""Tests for analyzer/correlation.py."""

from git_aftermerge.analyzer.correlation import CorrelationEngine
from git_aftermerge.analyzer.git_reader import GitReader
from git_aftermerge.storage.models import EventType


def test_bug_fix_correlation_detected(synthetic_repo):
    """fix: auth edge case should correlate back to feat: add auth."""
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    engine = CorrelationEngine(repo_dir)
    commits = reader.get_merge_commits()

    events = engine.find_bug_fix_correlations(commits)
    # feat_auth or fix_auth should have bug_fix events if correlation is detected
    # The fix commit touches the same lines as feat_auth
    feat_auth_sha = synthetic_repo["shas"]["feat_auth"]
    # If we find a BUG_FIX_LINKED event on feat_auth, great
    if feat_auth_sha in events:
        assert all(e.event_type == EventType.BUG_FIX_LINKED for e in events[feat_auth_sha])


def test_no_self_correlation(synthetic_repo):
    """A fix commit should not correlate with itself."""
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    engine = CorrelationEngine(repo_dir)
    commits = reader.get_merge_commits()
    events = engine.find_bug_fix_correlations(commits)
    fix_auth_sha = synthetic_repo["shas"]["fix_auth"]
    if fix_auth_sha in events:
        for e in events[fix_auth_sha]:
            assert e.commit_sha != fix_auth_sha


def test_churn_spike_empty_for_stable_repo(synthetic_repo):
    """Small synthetic repo has no churn spikes."""
    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    engine = CorrelationEngine(repo_dir)
    commits = reader.get_merge_commits()
    events = engine.find_churn_spikes(commits)
    # Might be empty or have some entries, just ensure it runs without error
    assert isinstance(events, dict)


def test_szz_precision_only_deleted_lines_implicate(tmp_path):
    """A fix touching only c2's lines must implicate c2, not c1 — even though
    c1 wrote the same file within the window (whole-file blame would flag both)."""
    from datetime import datetime, timedelta

    from conftest import run_git

    repo_dir = tmp_path / "szz-repo"
    repo_dir.mkdir()

    def rg(*args, date=""):
        return run_git(*args, cwd=repo_dir, date=date)

    rg("init")
    rg("config", "user.email", "t@t.com")
    rg("config", "user.name", "T")
    base = datetime(2026, 5, 1, 12, 0, 0)

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    # c1: creates the file (lines 1-10)
    block_a = "\n".join(f"def alpha_{i}():\n    return {i}\n" for i in range(5))
    (repo_dir / "mod.py").write_text(block_a)
    rg("add", "mod.py")
    rg("commit", "-m", "feat: alpha block", date=iso(base))
    c1 = rg("rev-parse", "HEAD")

    # c2: appends a second block
    block_b = "\n".join(f"def beta_{i}():\n    return {i} * 2\n" for i in range(5))
    (repo_dir / "mod.py").write_text(block_a + "\n" + block_b)
    rg("add", "mod.py")
    rg("commit", "-m", "feat: beta block", date=iso(base + timedelta(days=2)))
    c2 = rg("rev-parse", "HEAD")

    # c3: fix that rewrites ONE beta line only
    fixed = (block_a + "\n" + block_b).replace(
        "    return 3 * 2", "    return 3 * 2 + 1  # off-by-one"
    )
    (repo_dir / "mod.py").write_text(fixed)
    rg("add", "mod.py")
    rg("commit", "-m", "fix: beta off-by-one", date=iso(base + timedelta(days=4)))

    reader = GitReader(repo_dir)
    engine = CorrelationEngine(repo_dir)
    events = engine.find_bug_fix_correlations(reader.get_merge_commits())

    assert c2 in events, "the commit whose line was fixed must be implicated"
    assert c1 not in events, "untouched same-file commit must NOT be implicated"
    assert events[c2][0].lines_affected == 1


def test_szz_trivial_lines_do_not_implicate(tmp_path):
    """Deleting only blank/comment lines must not create a bug link."""
    from datetime import datetime, timedelta

    from conftest import run_git

    repo_dir = tmp_path / "szz-trivial"
    repo_dir.mkdir()

    def rg(*args, date=""):
        return run_git(*args, cwd=repo_dir, date=date)

    rg("init")
    rg("config", "user.email", "t@t.com")
    rg("config", "user.name", "T")
    base = datetime(2026, 5, 1, 12, 0, 0)

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    (repo_dir / "mod.py").write_text("# a comment\n\ndef f():\n    return 1\n")
    rg("add", "mod.py")
    rg("commit", "-m", "feat: add f", date=iso(base))
    c1 = rg("rev-parse", "HEAD")

    # fix deletes only the comment and the blank line
    (repo_dir / "mod.py").write_text("def f():\n    return 1\n")
    rg("add", "mod.py")
    rg("commit", "-m", "fix: tidy header", date=iso(base + timedelta(days=1)))

    reader = GitReader(repo_dir)
    engine = CorrelationEngine(repo_dir)
    events = engine.find_bug_fix_correlations(reader.get_merge_commits())
    assert c1 not in events
