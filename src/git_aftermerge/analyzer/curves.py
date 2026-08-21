"""Survival curves: fraction of a cohort's lines still alive T days after merge.

Computed entirely at query time from ``survival_observations`` facts.
Right-censoring is handled by construction: a commit only contributes to a
checkpoint it has actually aged past (younger commits simply have no
observation at that label yet).

Methodology note: curves are meaningful as *within-repo* comparisons —
AI-agent vs human vs bot cohorts of the same repository, where project
maturity, team, domain, and test culture are all held constant. Absolute
values are not comparable across repositories; cross-repo work should
compare the cohort *differences*, never the raw rates. The ``bot`` cohort
(Dependabot, Renovate…) is a negative control: it is fully automated but
deterministic, so bot curves showing AI-like churn indicate a detection bug
rather than a real effect.
"""

from collections import defaultdict
from typing import Optional

from git_aftermerge.storage.models import (
    CHECKPOINT_DAYS,
    CurvePoint,
    SurvivalCurve,
    checkpoint_label,
)


def compute_curves(
    rows: list[dict],
    by: str = "cohort",
    min_commits: int = 1,
) -> list[SurvivalCurve]:
    """Compute survival curves from ``Database.get_curve_rows()`` output.

    ``by`` selects the grouping: "cohort", "maturity", or "cohort+maturity".
    Rates are line-weighted (total surviving lines / total tracked lines) so
    a 500-line commit counts 500× a one-liner.
    """
    groups: dict[tuple[str, Optional[str]], list[dict]] = defaultdict(list)
    for row in rows:
        if by == "cohort":
            key = (row["cohort"], None)
        elif by == "maturity":
            key = ("all", row["maturity"] or "unknown")
        elif by == "cohort+maturity":
            key = (row["cohort"], row["maturity"] or "unknown")
        else:
            raise ValueError(f"Unknown grouping: {by!r}")
        groups[key].append(row)

    curves = []
    for (cohort, maturity), group_rows in sorted(groups.items(), key=lambda kv: kv[0]):
        if len(group_rows) < min_commits:
            continue
        points = [CurvePoint(
            days=0,
            survival_rate=1.0,
            commit_count=len(group_rows),
            lines_tracked=sum(r["lines_added"] for r in group_rows),
        )]
        for days in CHECKPOINT_DAYS:
            label = checkpoint_label(days)
            eligible = [r for r in group_rows if label in r["observations"]]
            if not eligible:
                continue
            total_lines = sum(r["lines_added"] for r in eligible)
            if total_lines == 0:
                continue
            # Clamp per commit: blame's internal diff can attribute slightly
            # more lines than numstat's histogram diff counted as added, so
            # uncapped sums can exceed 100% on small high-survival cohorts.
            surviving = sum(
                min(r["observations"][label], r["lines_added"]) for r in eligible
            )
            points.append(CurvePoint(
                days=days,
                survival_rate=round(surviving / total_lines, 4),
                commit_count=len(eligible),
                lines_tracked=total_lines,
            ))
        curves.append(SurvivalCurve(cohort=cohort, maturity=maturity, points=points))
    return curves
