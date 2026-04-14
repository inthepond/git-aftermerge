"""Integration tests for CLI commands."""


from typer.testing import CliRunner

from git_aftermerge.cli import app

runner = CliRunner()


def test_init_command(synthetic_repo, monkeypatch):
    repo_dir = synthetic_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert "Initialized" in result.output or "Analyzed" in result.output
    assert (repo_dir / ".aftermerge" / "aftermerge.db").exists()
    # .gitignore should contain .aftermerge/
    gitignore = (repo_dir / ".gitignore").read_text()
    assert ".aftermerge/" in gitignore


def test_scan_requires_init(synthetic_repo, monkeypatch):
    repo_dir = synthetic_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)
    result = runner.invoke(app, ["scan"])
    assert result.exit_code != 0 or "init" in result.output.lower()


def test_scan_after_init(synthetic_repo, monkeypatch):
    repo_dir = synthetic_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["scan", "--full"])
    assert result.exit_code == 0, result.output


def test_fate_command(synthetic_repo, monkeypatch):
    repo_dir = synthetic_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)
    runner.invoke(app, ["init"])
    sha = synthetic_repo["shas"]["feat_utils"][:7]
    result = runner.invoke(app, ["fate", sha])
    assert result.exit_code == 0, result.output


def test_fate_json_output(synthetic_repo, monkeypatch):
    import json
    repo_dir = synthetic_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)
    runner.invoke(app, ["init"])
    sha = synthetic_repo["shas"]["feat_utils"]
    result = runner.invoke(app, ["fate", sha, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "commit_sha" in data
    assert data["commit_sha"] == sha


def test_fate_short_output(synthetic_repo, monkeypatch):
    repo_dir = synthetic_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)
    runner.invoke(app, ["init"])
    sha = synthetic_repo["shas"]["feat_utils"][:7]
    result = runner.invoke(app, ["fate", sha, "--short"])
    assert result.exit_code == 0


def test_report_command(synthetic_repo, monkeypatch):
    repo_dir = synthetic_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0, result.output


def test_report_json(synthetic_repo, monkeypatch):
    import json
    repo_dir = synthetic_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["report", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "total_commits_analyzed" in data


def test_context_command(synthetic_repo, monkeypatch, tmp_path):
    repo_dir = synthetic_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)
    runner.invoke(app, ["init"])
    output_file = repo_dir / ".aftermerge" / "CONTEXT.md"
    result = runner.invoke(app, ["context", "--output", str(output_file)])
    assert result.exit_code == 0, result.output
    assert output_file.exists()
    content = output_file.read_text()
    assert "Post-Merge" in content


def test_context_stdout(synthetic_repo, monkeypatch):
    repo_dir = synthetic_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["context"])
    assert result.exit_code == 0
    assert "Post-Merge" in result.output
