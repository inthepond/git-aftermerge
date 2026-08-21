"""Typer CLI entry point for git-aftermerge."""

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from git_aftermerge.analyzer.attribution import attribute
from git_aftermerge.analyzer.correlation import CorrelationEngine
from git_aftermerge.analyzer.curves import compute_curves
from git_aftermerge.analyzer.git_reader import GitReader
from git_aftermerge.analyzer.patterns import PatternAggregator
from git_aftermerge.analyzer.revert import RevertDetector
from git_aftermerge.analyzer.survival import SurvivalTracker, build_commit_fact
from git_aftermerge.output import json_out, markdown, terminal
from git_aftermerge.storage.db import Database
from git_aftermerge.storage.models import HEAD_LABEL, EventType

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


def _build_prev_touch(
    reader: GitReader, tracked_shas: set[str]
) -> dict[str, dict[str, datetime]]:
    """For each tracked commit, when each of its files was previously touched.

    One full-history pass; a file absent from a commit's map was created by
    that commit. This is the raw fact behind code-maturity tiers.
    """
    prev_touch: dict[str, dict[str, datetime]] = {}
    last_touch: dict[str, datetime] = {}
    for record in reader.get_touch_history():
        if record.sha in tracked_shas:
            prev_touch[record.sha] = {
                p: last_touch[p] for p in record.paths if p in last_touch
            }
        for p in record.paths:
            last_touch[p] = record.date
    return prev_touch


def _run_scan(
    repo_root: Path, db: Database, full: bool = False, since: Optional[str] = None,
) -> int:
    """Core scan logic. Returns count of commits processed."""
    reader = GitReader(repo_root)
    tracker = SurvivalTracker(repo_root)
    reverter = RevertDetector(repo_root)
    correlator = CorrelationEngine(repo_root)

    # Determine scan range
    scan_since: Optional[str] = since
    if not full and not since:
        last = db.get_last_scan()
        if last:
            last_sha, last_time = last
            scan_since = last_time.strftime("%Y-%m-%d")

    commits = reader.get_merge_commits(since=scan_since)
    tracked_shas = {c.sha for c in commits}
    prev_touch = _build_prev_touch(reader, tracked_shas) if commits else {}

    # Detect reverts and correlations (facts about commit relationships)
    if commits:
        revert_events = reverter.find_all_reverts(commits, since=scan_since)
        bug_events = correlator.find_bug_fix_correlations(commits)
        churn_events = correlator.find_churn_spikes(commits)
        incident_events = correlator.find_incident_tags(commits)
    else:
        revert_events = bug_events = churn_events = incident_events = {}

    from git_aftermerge.analyzer.correlation import BUG_FIX_DETECTOR

    all_events: dict[str, list] = {}
    for source, detector in (
        (revert_events, "revert-1"),
        (bug_events, BUG_FIX_DETECTOR),
        (churn_events, "churn-1"),
        (incident_events, BUG_FIX_DETECTOR),
    ):
        for sha, evts in source.items():
            all_events.setdefault(sha, []).extend((e, detector) for e in evts)

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
            fact = build_commit_fact(
                commit,
                attribution=attribute(commit.author_name, commit.author, commit.body),
                prev_touch=prev_touch.get(commit.sha),
            )
            if not fact.files:
                progress.advance(task)
                continue

            db.upsert_commit_fact(fact)

            # Replace links for this commit
            db.delete_links(commit.sha)
            for event, detector in all_events.get(commit.sha, []):
                db.insert_link(commit.sha, event, detector=detector)

            # Checkpoint + HEAD survival observations. Historical checkpoints
            # are immutable, so skip ones already recorded unless --full.
            skip = set() if full else db.get_observation_labels(commit.sha) - {HEAD_LABEL}
            for obs in tracker.observe_commit(
                commit.sha, fact.authored_at, [f.path for f in fact.files], skip_labels=skip
            ):
                db.upsert_observation(obs)

            count += 1
            progress.advance(task)

            # Commit in batches for large repos
            if count % batch_size == 0:
                db.conn.commit()

        # Refresh previously stored commits: newly crossed checkpoints + HEAD.
        stored = [f for f in db.get_commit_facts() if f.sha not in tracked_shas]
        if stored:
            refresh_task = progress.add_task("Refreshing observations…", total=len(stored))
            for fact in stored:
                skip = db.get_observation_labels(fact.sha) - {HEAD_LABEL}
                for obs in tracker.observe_commit(
                    fact.sha, fact.authored_at, [f.path for f in fact.files],
                    skip_labels=skip,
                ):
                    db.upsert_observation(obs)
                progress.advance(refresh_task)

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

    since_dt = _parse_since(since)

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
def curve(
    by: str = typer.Option(
        "cohort", "--by",
        help="Grouping: cohort, maturity, or cohort+maturity",
    ),
    json: bool = typer.Option(False, "--json", help="Output as JSON"),
    since: Optional[str] = typer.Option(None, "--since", help="Limit analysis window"),
    min_commits: int = typer.Option(
        3, "--min-commits", help="Hide groups with fewer commits than this"
    ),
) -> None:
    """Show survival curves: % of lines still alive T days after merge.

    Grouped by author cohort (ai-agent / human / bot) and optionally by the
    maturity of the code each commit touched. Compare cohorts within this
    repo; absolute values are not comparable across repos.
    """
    repo_root = _find_repo_root()
    db_path = repo_root / ".aftermerge" / "aftermerge.db"
    if not db_path.exists():
        console.print("[red]Error:[/red] Run [cyan]git-aftermerge init[/cyan] first.")
        raise typer.Exit(1)

    db = Database(db_path)
    with db:
        rows = db.get_curve_rows(since=_parse_since(since))

    try:
        curves = compute_curves(rows, by=by, min_commits=min_commits)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)

    if json:
        typer.echo(json_out.to_json(curves))
    else:
        terminal.print_curves(curves, console, total_commits=len(rows))


@app.command()
def attribution(
    repo: Optional[Path] = typer.Option(
        None, "--repo", help="Repository to inspect (default: current)"
    ),
    json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Detect AI/bot attribution already present in commit history.

    Standalone backfill detector: reads trailers (Co-Authored-By: Claude,
    Generated-By, …), agent/bot author identities, and message markers.
    Needs no database — point it at any repo to see how much attributed
    history is recoverable.
    """
    repo_root = repo.resolve() if repo else _find_repo_root()
    if not (repo_root / ".git").exists():
        console.print(f"[red]Error:[/red] {repo_root} is not a git repository.")
        raise typer.Exit(1)

    reader = GitReader(repo_root)
    commits = reader.get_merge_commits()
    results = [
        (c, attribute(c.author_name, c.author, c.body)) for c in commits
    ]

    if json:
        import json as json_lib
        payload = [
            {
                "sha": c.sha,
                "authored_at": c.date.isoformat(),
                "cohort": a.cohort.value,
                "tool": a.tool,
                "model": a.model,
                "session": a.session_id,
                "human_ratio": a.human_ratio,
                "source": a.source,
            }
            for c, a in results
        ]
        typer.echo(json_lib.dumps(payload, indent=2))
    else:
        terminal.print_attribution_summary(repo_root.name, results, console)


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
        curve_rows = db.get_curve_rows()

    aggregator = PatternAggregator()
    pat_report = aggregator.build_report(repo_name=repo_root.name, fates=all_fates)
    curves = compute_curves(curve_rows, by="cohort")

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
        content = markdown.generate_context(
            pat_report, recent_failures, curves=curves, max_lines=max_lines
        )

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


def _parse_since(since: Optional[str]) -> Optional[datetime]:
    if not since:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(since, fmt)
        except ValueError:
            pass
    if "days ago" in since:
        try:
            days = int(since.split()[0])
            return datetime.utcnow() - timedelta(days=days)
        except ValueError:
            pass
    return None


if __name__ == "__main__":
    app()
