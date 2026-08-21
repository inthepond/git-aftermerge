# git-aftermerge

> Track the downstream fate of every commit. Feed structured post-merge feedback to AI coding agents.

## What This Is

`git-aftermerge` is a Python CLI + MCP server that analyzes what happens to code *after* it gets merged — tracking survival, reverts, churn, and bug-fix correlations — then feeds structured feedback back to coding agents so they learn from their own history.

## The Problem

AI coding agents (Claude Code, Codex, Cursor, etc.) generate code, open PRs, and get them merged. Then the feedback loop ends. The agent never learns whether that code survived a week, got reverted the next day, caused a downstream bug, or was completely rewritten in the next sprint.

**git-aftermerge closes that loop.**

## Key Features

- **Attribution** — assigns every commit a cohort (ai-agent / bot / human) from trailers (`Co-Authored-By: Claude`, `Generated-By:` convention), agent/bot identities, and message markers; works retroactively on existing history
- **Survival curves** — fraction of each cohort's lines still alive 7/30/90/180/365 days after merge, cross-cut by code maturity (new / young / mature)
- **Facts-only storage** — SQLite holds only facts from git; scores, fates, and curves are computed at query time, so metric definitions can change without a rescan
- **Revert detection** — automatic detection of standard and manual reverts
- **Bug-fix correlation** — links downstream bug fixes back to originating commits
- **Churn spike detection** — identifies areas of unusually high modification activity
- **Pattern aggregation** — roll-up of survival data by path, author, cohort, maturity, commit type, size, and language
- **MCP server** — expose all tools to AI coding agents via Model Context Protocol
- **Watch mode** — auto-scan on new commits for continuous monitoring

## How It Works

```
Commit merged → git-aftermerge scan → cohort attributed (trailers, identities)
                                     → blame snapshots at 7/30/90/180/365 days
                                     → reverts detected
                                     → bug-fix correlations found
                                     → survival curves per cohort × maturity
                                     → context file generated for agents
```

## Quick Links

- [Quick Start](quickstart.md) — install and run your first scan
- [CLI Reference](cli-reference.md) — all commands and options
- [MCP Setup](mcp-setup.md) — connect to Claude Code and other agents
- [How It Works](how-it-works.md) — algorithms and scoring details
