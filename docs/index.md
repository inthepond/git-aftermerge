# git-aftermerge

> Track the downstream fate of every commit. Feed structured post-merge feedback to AI coding agents.

## What This Is

`git-aftermerge` is a Python CLI + MCP server that analyzes what happens to code *after* it gets merged — tracking survival, reverts, churn, and bug-fix correlations — then feeds structured feedback back to coding agents so they learn from their own history.

## The Problem

AI coding agents (Claude Code, Codex, Cursor, etc.) generate code, open PRs, and get them merged. Then the feedback loop ends. The agent never learns whether that code survived a week, got reverted the next day, caused a downstream bug, or was completely rewritten in the next sprint.

**git-aftermerge closes that loop.**

## Key Features

- **Survival tracking** — line-level analysis of how code changes persist over time
- **Revert detection** — automatic detection of standard and manual reverts
- **Bug-fix correlation** — links downstream bug fixes back to originating commits
- **Churn spike detection** — identifies areas of unusually high modification activity
- **Pattern aggregation** — roll-up of survival data by path, author, commit type, size, and language
- **MCP server** — expose all tools to AI coding agents via Model Context Protocol
- **Watch mode** — auto-scan on new commits for continuous monitoring

## How It Works

```
Commit merged → git-aftermerge scan → survival tracked via git blame
                                     → reverts detected
                                     → bug-fix correlations found
                                     → patterns aggregated
                                     → context file generated for agents
```

## Quick Links

- [Quick Start](quickstart.md) — install and run your first scan
- [CLI Reference](cli-reference.md) — all commands and options
- [MCP Setup](mcp-setup.md) — connect to Claude Code and other agents
- [How It Works](how-it-works.md) — algorithms and scoring details
