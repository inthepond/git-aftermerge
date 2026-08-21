# How It Works

## Overview

git-aftermerge performs deterministic, read-only analysis of your git history. It never modifies your repository. All data is stored in a local SQLite database at `.aftermerge/aftermerge.db`.

## Facts vs. Derived Metrics

The database stores **only facts** recoverable from git plus deterministic
attribution: commit metadata, per-file line counts, when each file was
previously touched, detected commit links (reverts, fix correlations), and
blame-snapshot observations. Every judged or scored quantity — survival
score, fate label, maturity tier, pattern aggregate, survival curve — is
computed **at query time**. Scoring rules can change without a rescan, and
the whole database can always be rebuilt with `scan --full`.

All diffing uses `--diff-algorithm=histogram` with whitespace ignored
(`-w`, including blame), so formatting and indentation churn does not
pollute the survival signal.

## Attribution (who wrote this commit)

Every commit is assigned a **cohort**:

| Cohort | Meaning |
|--------|---------|
| `ai-agent` | LLM coding agents: Claude Code, Cursor, Copilot, aider, Devin, … |
| `bot` | Deterministic automation: Dependabot, Renovate, … |
| `human` | Everything else |

Detection priority:

1. **Convention trailers** (recommended going forward): `Generated-By:`,
   `AI-Model:`, `AI-Session:`, `AI-Human-Ratio:`
2. **Co-author trailers** already in history: `Co-Authored-By: Claude
   <noreply@anthropic.com>` (Claude Code's default), Cursor Agent, Copilot, …
3. **Author identity**: agent/bot accounts (`devin-ai-integration[bot]`,
   `dependabot[bot]`, aider's `(aider)` suffix, …)
4. **Message markers**: "Generated with Claude Code", `aider:` prefixes

The `bot` cohort is a **negative control**: bots are fully automated but
deterministic, so if bot commits show AI-like churn, the detection logic is
suspect — not the bots.

## Survival Observations & Curves

For each commit, blame snapshots are recorded at fixed checkpoints — **7,
30, 90, 180, and 365 days** after authoring — and at HEAD:

1. Find the repo state at each checkpoint (`git rev-list --before`)
2. Verify the state actually **contains** the commit (`git merge-base
   --is-ancestor`) — under squash/rebase PR flows a checkpoint state can
   predate the commit landing, and blaming there would record a false death
3. Run `git blame -w` on the commit's files at that state
4. Record how many lines are still attributed to the commit

The survival clock is anchored at the **committer date** — when the change
landed on the line of history — not the author date, which under PR flows
can be weeks earlier. A commit only contributes to checkpoints it has aged
past and to states that contain it (right-censoring); historical
observations are immutable and never recomputed. Survival curves
(`git-aftermerge curve`) aggregate these observations per cohort ×
maturity tier, line-weighted.

**Curves are within-repo comparisons only.** Repos that accept AI commits
skew young/small/single-maintainer, so absolute rates are not comparable
across repositories — compare the AI-vs-human *difference* inside one repo,
where project maturity, team, domain, and test culture are held constant.

## Code Maturity Tiers

Each file change records when the file was previously touched (one cheap
full-history pass). At query time this becomes a maturity tier:

| Tier | Age of touched code |
|------|---------------------|
| `new` | < 30 days (or file created by the commit) |
| `young` | 30–365 days |
| `mature` | > 365 days |

Without this layer, churn numbers are noise: healthy iteration on new
features and AI breakage of mature logic would count as the same thing.
A commit's tier is the lines-weighted dominant tier of its files. Renames
appear as new files (known limitation of the coarse pass).

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

Commits are grouped along seven dimensions (computed at query time):

| Dimension | Grouping |
|-----------|----------|
| **by_path** | First 2-3 directory levels (e.g., `src/auth/`) |
| **by_type** | Conventional commit prefix (feat, fix, refactor, etc.) |
| **by_author** | Committer email/name |
| **by_size** | Small (<20 lines), Medium (20–100), Large (>100) |
| **by_language** | File extension of modified files |
| **by_cohort** | ai-agent / bot / human |
| **by_maturity** | new / young / mature |

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
