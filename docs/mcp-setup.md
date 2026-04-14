# MCP Setup

git-aftermerge includes a Model Context Protocol (MCP) server that exposes all analysis tools to AI coding agents.

## Configuration

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aftermerge": {
      "command": "git-aftermerge",
      "args": ["mcp-serve"],
      "cwd": "/path/to/your/repo"
    }
  }
}
```

### Claude Code

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "aftermerge": {
      "command": "git-aftermerge",
      "args": ["mcp-serve"]
    }
  }
}
```

### Other MCP-Compatible Agents

Any agent supporting MCP stdio transport can use the same configuration pattern. The server reads from stdin and writes to stdout.

## Available Tools

### `aftermerge_get_fate`

Get the post-merge fate of a specific commit.

**Parameters:**

- `commit_sha` (string, required) — the commit SHA to look up

**Returns:** JSON-serialized `CommitFate` with survival score, fate, downstream events, and file paths.

### `aftermerge_get_patterns`

Get aggregate survival patterns for the repo.

**Parameters:**

- `path_filter` (string, optional) — filter to a directory, e.g. `"src/auth/"`
- `since` (string, optional) — only include commits since this date, e.g. `"30 days ago"`
- `author_filter` (string, optional) — filter to a specific author, e.g. `"claude"`

**Returns:** JSON-serialized `PatternReport` with patterns grouped by path, type, author, size, and language.

### `aftermerge_get_risky_areas`

List directories/files with lowest survival scores.

**Parameters:**

- `limit` (int, optional, default 10) — number of results

**Returns:** JSON array of risky areas with scores, revert counts, and recommendations.

### `aftermerge_get_recent_failures`

Get commits that were reverted or caused bug-fixes recently.

**Parameters:**

- `since` (string, optional, default `"30 days ago"`) — time window

**Returns:** JSON array of `CommitFate` objects with REVERT_LINKED or BUG_FIX_LINKED events.

### `aftermerge_get_context`

Get a structured markdown summary for agent context injection.

**Parameters:**

- `max_tokens` (int, optional, default 2000) — approximate maximum output length

**Returns:** Markdown-formatted string suitable for inclusion in agent context.

## Usage in Agent Prompts

Once configured, agents can query aftermerge data naturally:

> "Before modifying `src/auth/`, check aftermerge for risky areas in that directory."

> "What's the fate of my last commit?"

> "Show me recent failures in this repo."

The `aftermerge_get_context` tool is designed for automatic context injection — include its output in your agent's system prompt or reference file for continuous awareness of code quality patterns.
