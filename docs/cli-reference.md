# CLI Reference

## `git-aftermerge init`

Initialize aftermerge tracking in the current git repository.

- Creates `.aftermerge/` directory with SQLite database
- Adds `.aftermerge/` to `.gitignore`
- Runs a full initial scan

```bash
git-aftermerge init
```

## `git-aftermerge scan`

Scan git history and update commit fate data.

```bash
git-aftermerge scan [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--full` | Re-scan entire history (default: incremental since last scan) |
| `--since TEXT` | Only scan commits since this date or ref |

By default, scans only new commits since the last scan. For repos with 5000+ commits, a warning is shown with a suggestion to use `--since`.

## `git-aftermerge fate`

Show the fate of a specific commit.

```bash
git-aftermerge fate <COMMIT_SHA> [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--json` | Output as JSON |
| `--short` | One-line summary only |

Accepts full or partial (prefix) SHAs.

## `git-aftermerge report`

Show aggregate patterns for the entire repo.

```bash
git-aftermerge report [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--json` | Output as JSON |
| `--since TEXT` | Limit analysis window (e.g., `2026-01-01`, `30 days ago`) |
| `--path TEXT` | Filter to a specific directory |

Sections: overview, riskiest areas, most stable areas, author comparison, recent failures.

## `git-aftermerge context`

Generate an agent-consumable context file.

```bash
git-aftermerge context [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--format [md\|json]` | Output format (default: `md`) |
| `--max-lines INT` | Maximum output lines (default: `80`) |
| `--output FILE` | Write to file instead of stdout |

Typical usage:

```bash
git-aftermerge context --output .aftermerge/CONTEXT.md
```

## `git-aftermerge watch`

Watch for new commits and auto-scan.

```bash
git-aftermerge watch [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--interval, -i INT` | Poll interval in seconds (default: `30`) |

Polls `git rev-parse HEAD` at the specified interval. When a new HEAD is detected, runs an incremental scan. Press `Ctrl+C` to stop.

## `git-aftermerge mcp-serve`

Start the MCP server using stdio transport.

```bash
git-aftermerge mcp-serve
```

This command is used in MCP server configuration files, not typically invoked directly. See [MCP Setup](mcp-setup.md) for configuration details.
