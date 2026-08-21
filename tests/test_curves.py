"""Tests for survival curves, maturity tiers, and the facts-only storage layer."""

from typer.testing import CliRunner

from git_aftermerge.analyzer.curves import compute_curves
from git_aftermerge.cli import app
from git_aftermerge.storage.db import Database
from git_aftermerge.storage.models import MaturityTier, maturity_of

runner = CliRunner()


def _row(cohort, maturity, lines, obs, age=400):
    return {
        "sha": "x", "cohort": cohort, "tool": None, "maturity": maturity,
        "lines_added": lines, "age_days": age, "observations": obs,
    }


class TestCurveMath:
    def test_line_weighted_rates(self):
        rows = [
            _row("ai-agent", "new", 100, {"7d": 90, "30d": 50}),
            _row("ai-agent", "new", 100, {"7d": 70, "30d": 30}),
            _row("human", "new", 50, {"7d": 50}),
        ]
        curves = compute_curves(rows, by="cohort")
        by_cohort = {c.cohort: c for c in curves}
        ai_points = {p.days: p for p in by_cohort["ai-agent"].points}
        assert ai_points[0].survival_rate == 1.0
        assert ai_points[7].survival_rate == 0.8      # (90+70)/200
        assert ai_points[30].survival_rate == 0.4     # (50+30)/200
        human_points = {p.days: p for p in by_cohort["human"].points}
        assert human_points[7].survival_rate == 1.0   # 50/50

    def test_right_censoring(self):
        """A commit with no 30d observation (too young) doesn't drag the rate."""
        rows = [
            _row("human", "new", 100, {"7d": 100, "30d": 100}),
            _row("human", "new", 100, {"7d": 0}, age=10),  # young, heavily churned
        ]
        curves = compute_curves(rows, by="cohort")
        points = {p.days: p for p in curves[0].points}
        assert points[7].survival_rate == 0.5   # both eligible at 7d
        assert points[30].survival_rate == 1.0  # only the old commit is eligible
        assert points[30].commit_count == 1

    def test_grouping_by_maturity_and_both(self):
        rows = [
            _row("ai-agent", "new", 10, {"7d": 10}),
            _row("ai-agent", "mature", 10, {"7d": 5}),
            _row("human", "mature", 10, {"7d": 10}),
        ]
        m = compute_curves(rows, by="maturity")
        assert {c.maturity for c in m} == {"new", "mature"}
        both = compute_curves(rows, by="cohort+maturity")
        assert {(c.cohort, c.maturity) for c in both} == {
            ("ai-agent", "new"), ("ai-agent", "mature"), ("human", "mature"),
        }

    def test_min_commits_filter(self):
        rows = [_row("bot", "new", 10, {"7d": 10})]
        assert compute_curves(rows, by="cohort", min_commits=2) == []


def test_maturity_bucketing():
    assert maturity_of(None) == MaturityTier.NEW       # file created by the commit
    assert maturity_of(5) == MaturityTier.NEW
    assert maturity_of(29) == MaturityTier.NEW
    assert maturity_of(30) == MaturityTier.YOUNG
    assert maturity_of(365) == MaturityTier.YOUNG
    assert maturity_of(366) == MaturityTier.MATURE


class TestEndToEnd:
    def _init(self, ai_repo, monkeypatch):
        monkeypatch.chdir(ai_repo["repo_dir"])
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.output
        return Database(ai_repo["repo_dir"] / ".aftermerge" / "aftermerge.db")

    def test_facts_only_storage(self, ai_repo, monkeypatch):
        """No derived values on disk: no patterns table, no score/fate columns."""
        db = self._init(ai_repo, monkeypatch)
        with db:
            tables = {
                r[0] for r in db.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "patterns" not in tables
            assert "commit_fates" not in tables
            assert {"commits", "commit_files", "commit_links",
                    "survival_observations"} <= tables
            columns = {
                r[1] for r in db.conn.execute("PRAGMA table_info(commits)").fetchall()
            }
            assert "survival_score" not in columns
            assert "fate" not in columns

    def test_cohorts_stored_as_facts(self, ai_repo, monkeypatch):
        db = self._init(ai_repo, monkeypatch)
        with db:
            facts = {f.sha: f for f in db.get_commit_facts()}
        assert facts[ai_repo["shas"]["claude"]].cohort == "ai-agent"
        assert facts[ai_repo["shas"]["bot"]].cohort == "bot"
        assert facts[ai_repo["shas"]["human_rewrite"]].cohort == "human"
        conv = facts[ai_repo["shas"]["convention"]]
        assert conv.author_model == "claude-sonnet-4-6"
        assert conv.ai_session == "abc123"
        assert conv.human_ratio == 0.25

    def test_prev_touch_facts_and_maturity(self, ai_repo, monkeypatch):
        db = self._init(ai_repo, monkeypatch)
        with db:
            facts = {f.sha: f for f in db.get_commit_facts()}
            claude = facts[ai_repo["shas"]["claude"]]
            by_path = {f.path: f for f in claude.files}
            # core.py existed since human_old → has a prev_touch fact
            assert by_path["src/core.py"].prev_touch_at is not None
            # ai_feature.py was created by this commit → no prev touch
            assert by_path["src/ai_feature.py"].prev_touch_at is None

            fate = db.get_commit_fate(ai_repo["shas"]["claude"])
            # Dominated by the newly created file (40 lines vs 3) → "new"
            assert fate.maturity == "new"

    def test_observations_recorded(self, ai_repo, monkeypatch):
        db = self._init(ai_repo, monkeypatch)
        with db:
            labels = db.get_observation_labels(ai_repo["shas"]["claude"])
            # The commit is months old: early checkpoints must exist, plus head.
            assert {"7d", "30d", "head"} <= labels
            old_labels = db.get_observation_labels(ai_repo["shas"]["human_old"])
            assert "365d" in old_labels

    def test_ai_survival_dropped_by_rewrite(self, ai_repo, monkeypatch):
        """The human rewrite of half the AI feature must show up in the facts."""
        db = self._init(ai_repo, monkeypatch)
        with db:
            rows = db.get_curve_rows()
        claude_row = next(r for r in rows if r["sha"] == ai_repo["shas"]["claude"])
        head_surviving = claude_row["observations"]["head"]
        assert head_surviving < claude_row["lines_added"]

    def test_curve_command(self, ai_repo, monkeypatch):
        self._init(ai_repo, monkeypatch)
        result = runner.invoke(app, ["curve", "--min-commits", "1"])
        assert result.exit_code == 0, result.output
        assert "Line survival" in result.output.replace("\n", " ")
        assert "%" in result.output
        result_json = runner.invoke(app, ["curve", "--json", "--min-commits", "1"])
        assert result_json.exit_code == 0, result_json.output
        import json
        curves = json.loads(result_json.output)
        cohorts = {c["cohort"] for c in curves}
        assert {"ai-agent", "human", "bot"} <= cohorts

    def test_attribution_command(self, ai_repo, monkeypatch):
        monkeypatch.chdir(ai_repo["repo_dir"])
        result = runner.invoke(app, ["attribution"])
        assert result.exit_code == 0, result.output
        assert "ai-agent" in result.output
        import json
        result_json = runner.invoke(app, ["attribution", "--json"])
        assert result_json.exit_code == 0
        rows = json.loads(result_json.output)
        by_sha = {r["sha"]: r for r in rows}
        assert by_sha[ai_repo["shas"]["claude"]]["cohort"] == "ai-agent"
        assert by_sha[ai_repo["shas"]["bot"]]["cohort"] == "bot"


def test_survival_rate_clamped_at_100():
    """blame's diff can over-attribute vs numstat; rates must cap at 1.0."""
    rows = [_row("human", "mature", 12, {"7d": 13})]  # 13 surviving of 12 added
    curves = compute_curves(rows, by="cohort")
    points = {p.days: p for p in curves[0].points}
    assert points[7].survival_rate == 1.0
