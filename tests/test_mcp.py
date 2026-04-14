"""Tests for MCP server tools (via direct function calls)."""

import json

import pytest
from typer.testing import CliRunner

from git_aftermerge.cli import app

runner = CliRunner()


@pytest.fixture
def initialized_repo(synthetic_repo, monkeypatch):
    repo_dir = synthetic_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)
    runner.invoke(app, ["init"])
    return synthetic_repo


def test_aftermerge_get_fate(initialized_repo, monkeypatch):
    from git_aftermerge.mcp.server import aftermerge_get_fate
    repo_dir = initialized_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)

    sha = initialized_repo["shas"]["feat_utils"]
    result = aftermerge_get_fate(sha)
    data = json.loads(result)
    assert "commit_sha" in data
    assert data["commit_sha"] == sha


def test_aftermerge_get_fate_not_found(initialized_repo, monkeypatch):
    from git_aftermerge.mcp.server import aftermerge_get_fate
    repo_dir = initialized_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)

    result = aftermerge_get_fate("0000000000000000000000000000000000000000")
    data = json.loads(result)
    assert "error" in data


def test_aftermerge_get_patterns(initialized_repo, monkeypatch):
    from git_aftermerge.mcp.server import aftermerge_get_patterns
    repo_dir = initialized_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)

    result = aftermerge_get_patterns()
    data = json.loads(result)
    assert "total_commits_analyzed" in data
    assert "patterns" in data


def test_aftermerge_get_risky_areas(initialized_repo, monkeypatch):
    from git_aftermerge.mcp.server import aftermerge_get_risky_areas
    repo_dir = initialized_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)

    result = aftermerge_get_risky_areas(limit=5)
    data = json.loads(result)
    assert isinstance(data, list)


def test_aftermerge_get_recent_failures(initialized_repo, monkeypatch):
    from git_aftermerge.mcp.server import aftermerge_get_recent_failures
    repo_dir = initialized_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)

    result = aftermerge_get_recent_failures(since="365 days ago")
    data = json.loads(result)
    assert isinstance(data, list)


def test_aftermerge_get_context(initialized_repo, monkeypatch):
    from git_aftermerge.mcp.server import aftermerge_get_context
    repo_dir = initialized_repo["repo_dir"]
    monkeypatch.chdir(repo_dir)

    result = aftermerge_get_context(max_tokens=2000)
    assert isinstance(result, str)
    assert "Post-Merge" in result or "Overview" in result
