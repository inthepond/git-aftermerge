"""Markdown context file generation for agent consumption."""

from datetime import datetime

from git_aftermerge.storage.models import CommitFate, EventType, PatternReport


def generate_context(
    report: PatternReport,
    recent_failures: list[CommitFate],
    max_lines: int = 80,
) -> str:
    lines: list[str] = []

    lines.append("## Post-Merge Survival Context")
    lines.append(f"_Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} · "
                 f"Window: {report.analysis_window_start.strftime('%Y-%m-%d')} → "
                 f"{report.analysis_window_end.strftime('%Y-%m-%d')}_")
    lines.append("")

    # Overview paragraph
    lines.append("### Overview")
    total = report.total_commits_analyzed
    ai_pct = (report.ai_commits / total * 100) if total else 0
    lines.append(
        f"Analyzed **{report.total_commits_analyzed}** commits "
        f"({report.ai_commits} AI-attributed, {ai_pct:.0f}%). "
        f"Overall survival score: **{report.overall_survival_score:.0f}/100**."
    )
    lines.append("")

    # Top risky areas
    risky = report.risky_areas[:3]
    if risky:
        lines.append("### Risky Areas (avoid / review carefully)")
        for area in risky:
            lines.append(
                f"- **{area.path}** — score {area.avg_score:.0f}/100, "
                f"{area.revert_count} reverts, {area.bug_fix_count} bug-fixes"
            )
            if area.recommendation:
                lines.append(f"  - _{area.recommendation}_")
        lines.append("")

    # Recent failures
    if recent_failures:
        lines.append("### Recent Failures (last 30 days)")
        for fate in recent_failures[:5]:
            sha_short = fate.commit_sha[:7]
            event_types = {e.event_type for e in fate.downstream_events}
            tags = []
            if EventType.REVERT_LINKED in event_types:
                tags.append("REVERTED")
            if EventType.BUG_FIX_LINKED in event_types:
                tags.append("BUG-FIX")
            tag_str = ", ".join(tags)
            lines.append(
                f"- `{sha_short}` by {fate.author} — [{tag_str}] score {fate.survival_score}/100"
            )
        lines.append("")

    # Per-directory survival scores
    path_patterns = [p for p in report.patterns if p.dimension == "by_path"]
    if path_patterns:
        lines.append("### Directory Survival Scores")
        for p in sorted(path_patterns, key=lambda x: x.avg_score)[:10]:
            bar = "█" * int(p.avg_score / 10) + "░" * (10 - int(p.avg_score / 10))
            lines.append(f"- `{p.key}` {bar} {p.avg_score:.0f}/100 ({p.commit_count} commits)")
        lines.append("")

    # Trim to max_lines
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1]
        lines.append("_…truncated_")

    return "\n".join(lines)
