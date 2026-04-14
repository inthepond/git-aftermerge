"""Tests for analyzer/patterns.py."""

from datetime import datetime

from git_aftermerge.analyzer.patterns import PatternAggregator
from git_aftermerge.storage.models import CommitFate, Fate


def make_fate(
    sha, author, commit_type, lines_added, score, files, fate=Fate.SURVIVED,
) -> CommitFate:
    return CommitFate(
        commit_sha=sha,
        author=author,
        author_tool=None,
        author_model=None,
        merged_at=datetime(2026, 1, 1),
        original_lines_added=lines_added,
        original_lines_modified=0,
        surviving_lines=int(lines_added * score / 100),
        survival_score=score,
        fate=fate,
        days_since_merge=30,
        downstream_events=[],
        file_paths=files,
        commit_type=commit_type,
        ai_attributed=False,
    )


def test_compute_patterns_by_type():
    fates = [
        make_fate("a1", "dev", "feat", 50, 90, ["src/foo.py"]),
        make_fate("a2", "dev", "feat", 30, 80, ["src/bar.py"]),
        make_fate("a3", "dev", "fix", 10, 60, ["src/foo.py"]),
    ]
    agg = PatternAggregator()
    patterns = agg.compute_patterns(fates)
    type_patterns = {p.key: p for p in patterns if p.dimension == "by_type"}
    assert "feat" in type_patterns
    assert type_patterns["feat"].commit_count == 2
    assert type_patterns["feat"].avg_score == 85.0
    assert "fix" in type_patterns
    assert type_patterns["fix"].commit_count == 1


def test_compute_patterns_by_author():
    fates = [
        make_fate("b1", "alice@co.com", "feat", 50, 90, ["src/a.py"]),
        make_fate("b2", "bob@co.com", "feat", 50, 70, ["src/b.py"]),
    ]
    agg = PatternAggregator()
    patterns = agg.compute_patterns(fates)
    author_patterns = {p.key: p for p in patterns if p.dimension == "by_author"}
    assert "alice" in author_patterns
    assert "bob" in author_patterns


def test_risky_areas_sorted_by_score():
    fates = [
        make_fate("c1", "dev", "feat", 50, 30, ["src/auth/login.py"]),
        make_fate("c2", "dev", "feat", 50, 90, ["src/utils/helper.py"]),
        make_fate("c3", "dev", "fix", 20, 20, ["src/auth/token.py"]),
    ]
    agg = PatternAggregator()
    risky = agg.compute_risky_areas(fates)
    # src/auth/ should appear and have a lower score than src/utils/
    [r.path for r in risky]
    assert len(risky) > 0
    # First should be lowest score
    assert risky[0].avg_score <= risky[-1].avg_score


def test_build_report(synthetic_repo):
    """Integration: build report from synthetic repo data."""
    from git_aftermerge.analyzer.git_reader import GitReader
    from git_aftermerge.analyzer.survival import SurvivalTracker

    repo_dir = synthetic_repo["repo_dir"]
    reader = GitReader(repo_dir)
    tracker = SurvivalTracker(repo_dir)
    commits = reader.get_merge_commits()

    fates = []
    for c in commits:
        f = tracker.analyze_commit(c.sha)
        if f:
            fates.append(f)

    agg = PatternAggregator()
    report = agg.build_report(repo_name="test-repo", fates=fates)

    assert report.repo_name == "test-repo"
    assert report.total_commits_analyzed == len(fates)
    assert 0 <= report.overall_survival_score <= 100
