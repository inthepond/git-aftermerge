"""FastMCP server exposing aftermerge tools."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    from mcp.server.fastmcp import FastMCP as _Server  # mcp SDK 1.x
except ImportError:  # mcp SDK 2.x renamed FastMCP
    from mcp.server.mcpserver import MCPServer as _Server

from git_aftermerge.analyzer.curves import compute_curves
from git_aftermerge.analyzer.patterns import PatternAggregator
from git_aftermerge.output import json_out, markdown
from git_aftermerge.storage.db import Database
from git_aftermerge.storage.models import EventType

mcp = _Server("git-aftermerge")


def _get_db() -> Database:
    """Find and open the aftermerge database from cwd upward."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        db_path = parent / ".aftermerge" / "aftermerge.db"
        if db_path.exists():
            db = Database(db_path)
            db.connect()
            return db
    raise FileNotFoundError(
        "No aftermerge.db found. Run `git-aftermerge init` in your repository first."
    )


@mcp.tool()
def aftermerge_get_fate(commit_sha: str) -> str:
    """Get the post-merge fate of a commit: survival score, events, patterns."""
    db = _get_db()
    try:
        fate = db.get_commit_fate(commit_sha)
        if fate is None:
            return f'{{"error": "Commit {commit_sha} not found in aftermerge database."}}'
        return json_out.to_json(fate)
    finally:
        db.close()


@mcp.tool()
def aftermerge_get_patterns(
    path_filter: str = "",
    since: str = "",
    author_filter: str = "",
) -> str:
    """Get aggregate survival patterns, optionally filtered by path/date/author."""
    db = _get_db()
    try:
        since_dt: Optional[datetime] = None
        if since:
            for fmt in ("%Y-%m-%d",):
                try:
                    since_dt = datetime.strptime(since, fmt)
                    break
                except ValueError:
                    pass
            if since_dt is None and "days ago" in since:
                try:
                    days = int(since.strip().split()[0])
                    since_dt = datetime.utcnow() - timedelta(days=days)
                except ValueError:
                    pass

        fates = db.get_all_commit_fates(
            since=since_dt,
            path_filter=path_filter or None,
            author_filter=author_filter or None,
        )

        # Find repo name
        cwd = Path.cwd()
        for parent in [cwd, *cwd.parents]:
            if (parent / ".aftermerge").exists():
                repo_name = parent.name
                break
        else:
            repo_name = cwd.name

        aggregator = PatternAggregator()
        report = aggregator.build_report(repo_name=repo_name, fates=fates, since=since_dt)
        return json_out.to_json(report)
    finally:
        db.close()


@mcp.tool()
def aftermerge_get_risky_areas(limit: int = 10) -> str:
    """List directories/files with lowest survival scores."""
    db = _get_db()
    try:
        fates = db.get_all_commit_fates()
        aggregator = PatternAggregator()
        risky = aggregator.compute_risky_areas(fates)
        risky_limited = risky[:limit]
        return json_out.to_json(risky_limited)
    finally:
        db.close()


@mcp.tool()
def aftermerge_get_recent_failures(since: str = "30 days ago") -> str:
    """Get commits that were reverted or caused bug-fixes recently."""
    db = _get_db()
    try:
        since_dt: Optional[datetime] = None
        if "days ago" in since:
            try:
                days = int(since.strip().split()[0])
                since_dt = datetime.utcnow() - timedelta(days=days)
            except ValueError:
                pass
        elif since:
            for fmt in ("%Y-%m-%d",):
                try:
                    since_dt = datetime.strptime(since, fmt)
                    break
                except ValueError:
                    pass

        fates = db.get_all_commit_fates(since=since_dt)
        failures = [
            f for f in fates
            if any(
                e.event_type in (EventType.REVERT_LINKED, EventType.BUG_FIX_LINKED)
                for e in f.downstream_events
            )
        ]
        return json_out.to_json(failures)
    finally:
        db.close()


@mcp.tool()
def aftermerge_get_survival_curves(by: str = "cohort") -> str:
    """Survival curves: fraction of merged lines still alive at 7/30/90/180/365
    days, grouped by author cohort (ai-agent/human/bot), maturity of the touched
    code, or both ("cohort+maturity"). Within-repo comparison only."""
    db = _get_db()
    try:
        rows = db.get_curve_rows()
        curves = compute_curves(rows, by=by)
        return json_out.to_json(curves)
    finally:
        db.close()


@mcp.tool()
def aftermerge_get_context(max_tokens: int = 2000) -> str:
    """Get a structured markdown summary for agent context injection."""
    db = _get_db()
    try:
        since_30 = datetime.utcnow() - timedelta(days=30)
        all_fates = db.get_all_commit_fates()
        recent_fates = db.get_all_commit_fates(since=since_30)

        cwd = Path.cwd()
        for parent in [cwd, *cwd.parents]:
            if (parent / ".aftermerge").exists():
                repo_name = parent.name
                break
        else:
            repo_name = cwd.name

        aggregator = PatternAggregator()
        report = aggregator.build_report(repo_name=repo_name, fates=all_fates)

        recent_failures = [
            f for f in recent_fates
            if any(
                e.event_type in (EventType.REVERT_LINKED, EventType.BUG_FIX_LINKED)
                for e in f.downstream_events
            )
        ]

        # Approximate max_lines from max_tokens (avg ~10 chars per token, ~80 chars per line)
        max_lines = max_tokens * 10 // 80

        return markdown.generate_context(report, recent_failures, max_lines=max_lines)
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run(transport="stdio")
