# How It Works

## Overview

git-aftermerge performs deterministic, read-only analysis of your git history. It never modifies your repository. All data is stored in a local SQLite database at `.aftermerge/aftermerge.db`.

## Survival Tracking

For each commit being analyzed:

1. Get the list of files modified and lines added
2. Run `git blame` at current HEAD for each file
3. Count how many lines are still attributed to the original commit
4. Compute a survival ratio: `surviving_lines / original_lines_added`

Binary files (images, compiled assets, etc.) are automatically skipped.

## Fate Determination

Each commit is assigned a fate based on its survival ratio and events:

| Fate | Condition |
|------|-----------|
| **REVERTED** | Commit was explicitly reverted |
| **SURVIVED** | 90%+ of lines still present |
| **MODIFIED** | 30–90% of lines still present |
| **SUPERSEDED** | 0% surviving, replaced by a single commit |
| **DECAYED** | <30% surviving, gradually rewritten |

## Survival Score (0–100)

The score starts from the survival percentage and is adjusted:

**Base score:** `(surviving_lines / original_lines_added) * 100`

**Penalties:**

- Reverted within 24 hours: **-20**
- Reverted within 72 hours: **-10**
- >50% of lines modified within 7 days: **-10**
- Each linked bug fix: **-5**
- Each incident tag: **-10**

**Bonuses:**

- Survived 30+ days with zero downstream events: **+10**
- Code extended (not replaced) by later commits: **+5**

The final score is clamped to [0, 100].

## Revert Detection

Two methods, in priority order:

1. **Standard git revert** — commit message starts with "Revert" and body contains `This reverts commit <sha>`
2. **Manual revert heuristic** — a commit that deletes >80% of the lines added by a previous commit to the same files, within 7 days

## Bug-Fix Correlation

For each commit identified as a bug fix (commit type `fix` or message contains fix/bug/hotfix keywords):

1. Get the files and lines touched
2. Run `git blame` at the commit before the fix
3. Find which earlier commits last touched those lines
4. If the earlier commit was merged within the last 14 days, link them as `BUG_FIX_LINKED`

## Churn Spike Detection

1. Group commits by file, then by week
2. Compute median weekly modification count per file
3. Weeks with >3x the median are flagged as churn spikes
4. Commits from the preceding 2 weeks are tagged with `CHURN_SPIKE`

## Pattern Aggregation

Commits are grouped along five dimensions:

| Dimension | Grouping |
|-----------|----------|
| **by_path** | First 2-3 directory levels (e.g., `src/auth/`) |
| **by_type** | Conventional commit prefix (feat, fix, refactor, etc.) |
| **by_author** | Committer email/name |
| **by_size** | Small (<20 lines), Medium (20–100), Large (>100) |
| **by_language** | File extension of modified files |

For each group: average score, commit count, revert count, and bug-fix count.

## Risky Area Recommendations

Auto-generated based on simple rules:

- **3+ reverts in 90 days** — "Consider architectural review before modifying."
- **Average score < 50** — "Low survival rate. Check existing patterns before committing."
- **Average score > 90 with 10+ commits** — "High stability area. Changes here tend to stick."

## Performance

- Commit metadata and diff stats are fetched in a single `git log --numstat` call
- Downstream events are batch-loaded from SQLite (no N+1 queries)
- Database writes are batched in groups of 500 commits
- Binary files are skipped during blame operations
- Incremental scan only processes new commits since the last scan
