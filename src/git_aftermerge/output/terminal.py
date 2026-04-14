"""Rich terminal output formatting."""

from datetime import datetime

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from git_aftermerge.storage.models import (
    CommitFate,
    EventType,
    Fate,
    PatternReport,
)

console = Console()

FATE_STYLES = {
    Fate.SURVIVED: "green",
    Fate.MODIFIED: "yellow",
    Fate.REVERTED: "red bold",
    Fate.SUPERSEDED: "magenta",
    Fate.DECAYED: "red",
}

EVENT_LABELS = {
    EventType.BUG_FIX_LINKED: "BUG_FIX",
    EventType.REVERT_LINKED: "REVERT",
    EventType.CHURN_SPIKE: "CHURN",
    EventType.INCIDENT_TAG: "INCIDENT",
    EventType.MODIFICATION: "MODIFIED",
}


def _score_style(score: int) -> str:
    if score >= 80:
        return "green"
    if score >= 50:
        return "yellow"
    return "red"


def _days_ago(dt: datetime) -> str:
    days = (datetime.utcnow() - dt).days
    if days == 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


def print_commit_fate(fate: CommitFate, c: Console = console) -> None:
    sha_short = fate.commit_sha[:7]
    score_style = _score_style(fate.survival_score)
    fate_style = FATE_STYLES.get(fate.fate, "white")

    c.print()
    c.print(f"[bold]Commit:[/bold]    [cyan]{sha_short}[/cyan]")
    tool_str = f" (via {fate.author_tool})" if fate.author_tool else ""
    c.print(f"[bold]Author:[/bold]    {fate.author}" + tool_str)
    merged_str = fate.merged_at.strftime('%Y-%m-%d')
    c.print(f"[bold]Merged:[/bold]    {merged_str}  ({_days_ago(fate.merged_at)})")
    score_part = f"[{score_style}]{fate.survival_score} / 100[/{score_style}]"
    fate_part = f"[{fate_style}]{fate.fate.value}[/{fate_style}]"
    c.print(f"[bold]Score:[/bold]     {score_part}  {fate_part}")
    c.print()
    if fate.original_lines_added > 0:
        pct = fate.surviving_lines / fate.original_lines_added * 100
        added = fate.original_lines_added
        modified = fate.original_lines_modified
        c.print(f"[bold]Lines:[/bold]     +{added} added, +{modified} modified")
        surviving = fate.surviving_lines
        c.print(f"[bold]Surviving:[/bold] {surviving} / {added} ({pct:.1f}%)")
    c.print()

    if fate.downstream_events:
        c.print("[bold]Events:[/bold]")
        for i, event in enumerate(sorted(fate.downstream_events, key=lambda e: e.date)):
            prefix = "└─" if i == len(fate.downstream_events) - 1 else "├─"
            label = EVENT_LABELS.get(event.event_type, event.event_type.value)
            date_str = event.date.strftime("%Y-%m-%d")
            msg = event.commit_message[:60]
            c.print(
                f"  {prefix} [dim]{date_str}[/dim]  "
                f"[yellow]{label:<10}[/yellow] {msg}"
            )
            if event.lines_affected:
                inner = "   " if i == len(fate.downstream_events) - 1 else "│  "
                affected = event.lines_affected
                c.print(
                    f"  {inner}             "
                    f"[dim]↳ {affected} lines affected by {event.author}[/dim]"
                )
    else:
        c.print("[dim]No downstream events recorded.[/dim]")
    c.print()


def print_report(report: PatternReport, c: Console = console) -> None:
    c.print()
    c.print(Panel(
        f"[bold]{report.repo_name}[/bold] — Post-Merge Analysis",
        subtitle=(
            f"{report.analysis_window_start.strftime('%Y-%m-%d')}"
            f" → {report.analysis_window_end.strftime('%Y-%m-%d')}"
        ),
        box=box.ROUNDED,
    ))

    # Overview
    c.print("\n[bold]Overview[/bold]")
    score_style = _score_style(int(report.overall_survival_score))
    c.print(f"  Total commits analyzed: {report.total_commits_analyzed}")
    c.print(f"  AI-attributed commits:  {report.ai_commits}")
    score_val = f"{report.overall_survival_score:.1f}"
    c.print(f"  Overall survival score: [{score_style}]{score_val}[/{score_style}]")

    # Risky areas
    if report.risky_areas:
        c.print("\n[bold red]Riskiest Areas[/bold red]")
        tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        tbl.add_column("Path")
        tbl.add_column("Score", justify="right")
        tbl.add_column("Commits", justify="right")
        tbl.add_column("Reverts", justify="right")
        tbl.add_column("Bug Fixes", justify="right")
        for area in report.risky_areas[:5]:
            score_s = _score_style(int(area.avg_score))
            tbl.add_row(
                area.path,
                f"[{score_s}]{area.avg_score:.0f}[/{score_s}]",
                str(area.commit_count),
                str(area.revert_count),
                str(area.bug_fix_count),
            )
        c.print(tbl)
        # Recommendations
        for area in report.risky_areas[:3]:
            if area.recommendation:
                c.print(f"  [yellow]⚠[/yellow] {area.path}: {area.recommendation}")

    # Stable areas
    stable = sorted(report.risky_areas, key=lambda x: -x.avg_score)[:5]
    if stable:
        c.print("\n[bold green]Most Stable Areas[/bold green]")
        for area in stable:
            c.print(f"  [green]✓[/green] {area.path}  [green]{area.avg_score:.0f}[/green]")

    # Author breakdown
    author_patterns = [p for p in report.patterns if p.dimension == "by_author"]
    if len(author_patterns) > 1:
        c.print("\n[bold]By Author[/bold]")
        tbl2 = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        tbl2.add_column("Author")
        tbl2.add_column("Score", justify="right")
        tbl2.add_column("Commits", justify="right")
        for p in sorted(author_patterns, key=lambda x: -x.avg_score):
            score_s = _score_style(int(p.avg_score))
            tbl2.add_row(p.key, f"[{score_s}]{p.avg_score:.0f}[/{score_s}]", str(p.commit_count))
        c.print(tbl2)

    c.print()


def print_short_fate(fate: CommitFate, c: Console = console) -> None:
    sha_short = fate.commit_sha[:7]
    score_style = _score_style(fate.survival_score)
    fate_style = FATE_STYLES.get(fate.fate, "white")
    c.print(
        f"[cyan]{sha_short}[/cyan]  [{score_style}]{fate.survival_score:3d}/100[/{score_style}]  "
        f"[{fate_style}]{fate.fate.value:<10}[/{fate_style}]  {fate.author}"
    )
