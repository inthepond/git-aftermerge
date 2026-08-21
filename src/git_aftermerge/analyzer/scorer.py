"""Survival score computation (0-100)."""


from git_aftermerge.storage.models import CommitFate, EventType


def compute_score(commit_fate: CommitFate) -> int:
    if commit_fate.original_lines_added == 0:
        return 100  # No lines to track

    # Base score: percentage of lines surviving
    base = (commit_fate.surviving_lines / commit_fate.original_lines_added) * 100

    adjustments = 0

    # Penalty: reverted within 24h / 72h
    for event in commit_fate.downstream_events:
        if event.event_type == EventType.REVERT_LINKED:
            hours_to_revert = (event.date - commit_fate.merged_at).total_seconds() / 3600
            if hours_to_revert <= 24:
                adjustments -= 20
            elif hours_to_revert <= 72:
                adjustments -= 10

    # Penalty: >50% of lines gone within 7 days (from the 7d checkpoint
    # observation when available, else legacy MODIFICATION events)
    if commit_fate.early_survival_ratio is not None:
        if commit_fate.early_survival_ratio < 0.5:
            adjustments -= 10
    else:
        early_modifications = sum(
            e.lines_affected for e in commit_fate.downstream_events
            if e.event_type == EventType.MODIFICATION
            and (e.date - commit_fate.merged_at).days <= 7
        )
        if early_modifications > commit_fate.original_lines_added * 0.5:
            adjustments -= 10

    # Bonus: survived 30+ days with zero downstream events
    if commit_fate.days_since_merge >= 30 and len(commit_fate.downstream_events) == 0:
        adjustments += 10

    # Bonus: other commits build on top (extending, not replacing)
    extensions = [
        e for e in commit_fate.downstream_events
        if e.event_type == EventType.MODIFICATION
        and ("extend" in e.commit_message.lower() or "add" in e.commit_message.lower())
    ]
    if extensions:
        adjustments += 5

    # Penalty: linked to bug fixes
    bug_fixes = [
        e for e in commit_fate.downstream_events
        if e.event_type == EventType.BUG_FIX_LINKED
    ]
    adjustments -= len(bug_fixes) * 5

    # Penalty: incident tags
    incidents = [e for e in commit_fate.downstream_events if e.event_type == EventType.INCIDENT_TAG]
    adjustments -= len(incidents) * 10

    return max(0, min(100, int(base + adjustments)))
