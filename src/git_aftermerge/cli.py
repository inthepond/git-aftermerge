"""Typer CLI entry point for git-aftermerge."""

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from git_aftermerge.analyzer.correlation import CorrelationEngine
from git_aftermerge.analyzer.git_reader import GitReader
from git_aftermerge.analyzer.patterns import PatternAggregator
from git_aftermerge.analyzer.revert import RevertDetector
from git_aftermerge.analyzer.scorer import compute_score
from git_aftermerge.analyzer.survival import SurvivalTracker
from git_aftermerge.output import json_out, markdown, terminal
from git_aftermerge.storage.db import Database
from git_aftermerge.storage.models import EventType, Fate

app = typer.Typer(
    name="git-aftermerge",
    help="Track the downstream fate of every commit. Post-merge feedback for AI coding agents.",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


def _find_repo_root() -> Path:
    """Walk up from cwd to find .git directory."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists():
            return parent
    typer.echo("Error: not inside a git repository.", err=True)
    raise typer.Exit(1)


def _get_db(repo_root: Path) -> Database:
    aftermerge_dir = repo_root / ".aftermerge"
    aftermerge_dir.mkdir(exist_ok=True)
    db = Database(aftermerge_dir / "aftermerge.db")
    return db


def _run_scan(
    repo_root: Path, db: Database, full: bool = False, since: Optional[str] = None,
) -> int:
    """Core scan logic. Returns count of commits processed."""
    reader = GitReader(repo_root)
    tracker = SurvivalTracker(repo_root)
    reverter = RevertDetector(repo_root)
    correlator = CorrelationEngine(repo_root)
    aggregator = PatternAggregator()

    # Determine scan range
    scan_since: Optional[str] = since
    if not full and not since:
        last = db.get_last_scan()
        if last:
            last_sha, last_time = last
            scan_since = last_time.strftime("%Y-%m-%d")

    commits = reader.get_merge_commits(since=scan_since)
    if not commits:
        return 0

    # Detect reverts
    reverted_shas = reverter.get_reverted_shas(commits)
    revert_events = reverter.find_all_reverts(commits, since=scan_since)

    # Bug-fix correlations
    bug_events = correlator.find_bug_fix_correlations(commits)
    churn_events = correlator.find_churn_spikes(commits)
    incident_events = correlator.find_incident_tags(commits)

    # Merge all events
    all_events: dict[str, list] = {}
    for sha, evts in {**revert_events, **bug_events, **churn_events, **incident_events}.items():
        all_events.setdefault(sha, []).extend(evts)

    total = len(commits)
    if total > 5000:
        console.print(
            f"[yellow]Warning:[/yellow] {total} commits to analyze. This may take a while."
        )
        console.print("Tip: use [cyan]--since[/cyan] to limit the scan range.")

    count = 0
    batch_size = 500
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Analyzing commits…", total=total)
        for commit in commits:
            fate = tracker.analyze_commit(commit.sha, reverted_shas, commit_info=commit)
            if fate is None:
                progress.advance(task)
                continue

            # Attach events
            fate.downstream_events = all_events.get(commit.sha, [])

            # Recompute score with events attached
            fate.survival_score = compute_score(fate)

            # Determine final fate (may be REVERTED based on events)
            if any(e.event_type == EventType.REVERT_LINKED for e in fate.downstream_events):
                fate.fate = Fate.REVERTED

            db.upsert_commit_fate(fate)
            # Delete old events and re-insert
            db.delete_downstream_events(commit.sha)
            for event in fate.downstream_events:
                db.insert_downstream_event(commit.sha, event)

            count += 1
            progress.advance(task)

            # Commit in batches for large repos
            if count % batch_size == 0:
                db.conn.commit()

    db.conn.commit()

    # Update patterns
    all_fates = db.get_all_commit_fates()
    patterns = aggregator.compute_patterns(all_fates)
    for p in patterns:
        db.upsert_pattern(p)
    db.conn.commit()

    # Update scan meta
    head_sha = reader.get_head_sha()
    db.update_scan_meta(head_sha, str(repo_root))

    return count


@app.command()
def init() -> None:
    """Initialize aftermerge tracking in the current git repository."""
    repo_root = _find_repo_root()
    aftermerge_dir = repo_root / ".aftermerge"
    aftermerge_dir.mkdir(exist_ok=True)

    db = _get_db(repo_root)
    with db:
        db.initialize_schema()

    # Add to .gitignore
    gitignore = repo_root / ".gitignore"
    entry = ".aftermerge/"
    if gitignore.exists():
        content = gitignore.read_text()
        if entry not in content:
            gitignore.write_text(content.rstrip() + f"\n{entry}\n")
    else:
        gitignore.write_text(f"{entry}\n")

    console.print(f"[green]✓[/green] Initialized git-aftermerge in [cyan]{repo_root}[/cyan]")
    console.print("Running initial scan…")

    with _get_db(repo_root) as db:
        count = _run_scan(repo_root, db, full=True)

    console.print(f"[green]✓[/green] Analyzed [bold]{count}[/bold] commits.")


@app.command()
def scan(
    full: bool = typer.Option(False, "--full", help="Re-scan entire history"),
    since: Optional[str] = typer.Option(
        None, "--since", help="Only scan commits since this date/ref"
    ),
) -> None:
    """Scan git history and update commit fate data."""
    repo_root = _find_repo_root()
    db = _get_db(repo_root)

    if not (repo_root / ".aftermerge" / "aftermerge.db").exists():
        console.print("[red]Error:[/red] Run [cyan]git-aftermerge init[/cyan] first.", style="red")
        raise typer.Exit(1)

    with db:
        db.initialize_schema()  # ensure schema is up to date
        count = _run_scan(repo_root, db, full=full, since=since)

    console.print(f"[green]✓[/green] Processed [bold]{count}[/bold] commits.")


@app.command()
def fate(
    commit_sha: str = typer.Argument(..., help="The commit SHA to inspect"),
    json: bool = typer.Option(False, "--json", help="Output as JSON"),
    short: bool = typer.Option(False, "--short", help="One-line summary only"),
) -> None:
    """Show the fate of a specific commit."""
    repo_root = _find_repo_root()
    db_path = repo_root / ".aftermerge" / "aftermerge.db"
    if not db_path.exists():
        console.print("[red]Error:[/red] Run [cyan]git-aftermerge init[/cyan] first.")
        raise typer.Exit(1)

    db = Database(db_path)
    with db:
        # Try prefix match
        result = db.get_commit_fate(commit_sha)
        if result is None:
            # Try full SHA lookup via git
            reader = GitReader(repo_root)
            full_sha = reader.resolve_sha(commit_sha)
            if full_sha:
                result = db.get_commit_fate(full_sha)

    if result is None:
        console.print(f"[red]Commit {commit_sha} not found in aftermerge database.[/red]")
        console.print("Tip: run [cyan]git-aftermerge scan[/cyan] to update the database.")
        raise typer.Exit(1)

    if json:
        typer.echo(json_out.to_json(result))
    elif short:
        terminal.print_short_fate(result, console)
    else:
        terminal.print_commit_fate(result, console)


@app.command()
def report(
    json: bool = typer.Option(False, "--json", help="Output as JSON"),
    since: Optional[str] = typer.Option(None, "--since", help="Limit analysis window"),
    path: Optional[str] = typer.Option(None, "--path", help="Filter to a specific directory"),
) -> None:
    """Show aggregate patterns for the entire repo."""
    repo_root = _find_repo_root()
    db_path = repo_root / ".aftermerge" / "aftermerge.db"
    if not db_path.exists():
        console.print("[red]Error:[/red] Run [cyan]git-aftermerge init[/cyan] first.")
        raise typer.Exit(1)

    since_dt: Optional[datetime] = None
    if since:
        # Try parsing common formats
        for fmt in ("%Y-%m-%d", "%Y-%m"):
            try:
                since_dt = datetime.strptime(since, fmt)
                break
            except ValueError:
                pass
        if since_dt is None and "days ago" in since:
            try:
                days = int(since.split()[0])
                since_dt = datetime.utcnow() - timedelta(days=days)
            except ValueError:
                pass

    db = Database(db_path)
    with db:
        fates = db.get_all_commit_fates(since=since_dt, path_filter=path)

    aggregator = PatternAggregator()
    pat_report = aggregator.build_report(
        repo_name=repo_root.name,
        fates=fates,
        since=since_dt,
    )

    if json:
        typer.echo(json_out.to_json(pat_report))
    else:
        terminal.print_report(pat_report, console)


@app.command()
def context(
    format: str = typer.Option("md", "--format", help="Output format: md or json"),
    max_lines: int = typer.Option(80, "--max-lines", help="Maximum output lines"),
    output: Optional[Path] = typer.Option(None, "--output", help="Write to file instead of stdout"),
) -> None:
    """Generate an agent-consumable context file."""
    repo_root = _find_repo_root()
    db_path = repo_root / ".aftermerge" / "aftermerge.db"
    if not db_path.exists():
        console.print("[red]Error:[/red] Run [cyan]git-aftermerge init[/cyan] first.")
        raise typer.Exit(1)

    db = Database(db_path)
    since_30 = datetime.utcnow() - timedelta(days=30)
    with db:
        all_fates = db.get_all_commit_fates()
        recent_fates = db.get_all_commit_fates(since=since_30)

    aggregator = PatternAggregator()
    pat_report = aggregator.build_report(repo_name=repo_root.name, fates=all_fates)

    recent_failures = [
        f for f in recent_fates
        if any(
            e.event_type in (EventType.REVERT_LINKED, EventType.BUG_FIX_LINKED)
            for e in f.downstream_events
        )
    ]

    if format == "json":
        content = json_out.to_json(pat_report)
    else:
        content = markdown.generate_context(pat_report, recent_failures, max_lines=max_lines)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content)
        console.print(f"[green]✓[/green] Context written to [cyan]{output}[/cyan]")
    else:
        typer.echo(content)


@app.command()
def watch(
    interval: int = typer.Option(30, "--interval", "-i", help="Poll interval in seconds"),
) -> None:
    """Watch for new commits and auto-scan."""
    repo_root = _find_repo_root()
    db_path = repo_root / ".aftermerge" / "aftermerge.db"
    if not db_path.exists():
        console.print("[red]Error:[/red] Run [cyan]git-aftermerge init[/cyan] first.")
        raise typer.Exit(1)

    reader = GitReader(repo_root)
    last_head = reader.get_head_sha()

    console.print(f"[cyan]Watching[/cyan] {repo_root.name} for new commits (every {interval}s)")
    console.print(f"Current HEAD: [dim]{last_head[:10]}[/dim]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    scan_count = 0
    try:
        while True:
            current_head = reader.get_head_sha()
            if current_head != last_head:
                timestamp = datetime.utcnow().strftime("%H:%M:%S")
                console.print(
                    f"[green]{timestamp}[/green] New HEAD detected: "
                    f"[cyan]{current_head[:10]}[/cyan]"
                )

                db = _get_db(repo_root)
                with db:
                    db.initialize_schema()
                    count = _run_scan(repo_root, db)

                console.print(f"  Processed [bold]{count}[/bold] commits.\n")
                last_head = current_head
                scan_count += 1

            # Sleep in small increments for responsive Ctrl+C
            for _ in range(interval):
                time.sleep(1)
    except KeyboardInterrupt:
        pass

    console.print(f"\n[dim]Watch stopped. {scan_count} scans performed.[/dim]")


@app.command(name="mcp-serve")
def mcp_serve() -> None:
    """Start the MCP server (stdio transport)."""
    from git_aftermerge.mcp.server import mcp
    mcp.run(transport="stdio")


if __name__ == "__main__":
    app()
