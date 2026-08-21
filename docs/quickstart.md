# Quick Start

## Installation

```bash
pip install git-aftermerge
```

## Initialize

Navigate to any git repository and run:

```bash
cd /path/to/your/repo
git-aftermerge init
```

This creates a `.aftermerge/` directory (auto-added to `.gitignore`), sets up a local SQLite database, and runs an initial full scan of your commit history.

## Check a Commit's Fate

```bash
git-aftermerge fate abc1234
```

Output:

```
Commit:    abc1234 — "feat: add JWT refresh token rotation"
Author:    claude (via Claude Code)
Merged:    2026-03-15  (29 days ago)
Score:     73 / 100

Lines:     +142 added, +38 modified
Surviving: 104 / 142 (73.2%)

Events:
  ├─ 2026-03-17  MODIFIED   fix: handle edge case in token expiry
  │              ↳ 12 lines changed by human (dekko)
  └─ 2026-04-01  BUG_FIX    fix: refresh token race condition
                 ↳ 10 lines deleted, replaced by mutex approach
```

Use `--json` for machine-readable output, or `--short` for a one-liner.

## See What's Already Attributed

```bash
git-aftermerge attribution
```

Works on any repo without `init`: counts commits already carrying AI traces (Claude Code's `Co-Authored-By: Claude`, Cursor/Copilot/aider identities, `Generated-By:` trailers) plus deterministic bots.

## Survival Curves

```bash
git-aftermerge curve                      # by cohort: ai-agent vs human vs bot
git-aftermerge curve --by cohort+maturity # cross-cut by age of the code touched
```

Shows the fraction of each cohort's merged lines still alive 7/30/90/180/365 days later. Compare cohorts within one repo — absolute rates are not comparable across repos.

## View Patterns

```bash
git-aftermerge report
```

Shows aggregate survival patterns: riskiest areas, most stable areas, author comparisons, and recent failures.

## Generate Agent Context

```bash
git-aftermerge context --output .aftermerge/CONTEXT.md
```

Then reference it in your `CLAUDE.md`:

```markdown
## Post-Merge Context
See .aftermerge/CONTEXT.md for code survival data and risky areas.
```

## Watch Mode

Auto-scan when new commits appear:

```bash
git-aftermerge watch --interval 30
```

## Update Data

Run incremental scans as your repo evolves:

```bash
git-aftermerge scan              # incremental (since last scan)
git-aftermerge scan --full       # re-scan entire history
git-aftermerge scan --since 2026-01-01
```

## Next Steps

- [CLI Reference](cli-reference.md) — all commands and options
- [MCP Setup](mcp-setup.md) — connect to AI agents
- [How It Works](how-it-works.md) — understand the scoring algorithm
