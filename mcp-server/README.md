# Agent Genesis MCP Server

> MCP server for searching Claude Code conversation history

This package provides an MCP (Model Context Protocol) server that enables Claude Code to search through your conversation history using the Agent Genesis API.

## Prerequisites

- Python 3.10+
- Agent Genesis API running (via Docker)

## Installation

### From PyPI

```bash
pip install agent-genesis-mcp
```

### From Source

```bash
git clone https://github.com/agentgenesis/agent-genesis.git
cd agent-genesis/mcp-server
pip install -e .
```

## Configuration

Add to your Claude Code configuration:

**Linux/macOS:** `~/.claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "agent-genesis": {
      "command": "agent-genesis-mcp",
      "args": []
    }
  }
}
```

Or if installed in a virtual environment:

```json
{
  "mcpServers": {
    "agent-genesis": {
      "command": "/path/to/venv/bin/agent-genesis-mcp",
      "args": []
    }
  }
}
```

## Available Tools

### `search_conversations`
Search through your Claude Code conversation history.

**Parameters:**
- `query` (str): Search query string
- `limit` (int, optional): Max results (default: 5, max: 50)
- `project` (str, optional): Filter by project name

**Example:**
```python
search_conversations(query="authentication flow", limit=10, project="my-app")
```

**Returns:**
```json
{
  "success": true,
  "results": [
    {
      "score": 0.856,
      "project": "my-app",
      "timestamp": "2025-01-15T10:30:00Z",
      "conversation_id": "conv_abc123",
      "content_snippet": "Implementing authentication...",
      "full_content_length": 1245
    }
  ],
  "total_found": 3,
  "query": "authentication flow"
}
```

### `get_api_stats`
Get statistics about the indexed conversation corpus.

**Returns:**
- Total conversations indexed
- Number of unique projects
- Index health status

### `check_api_health`
Verify that the Agent Genesis API is running and healthy.

### `manage_scheduler`
Control automatic conversation indexing.

**Parameters:**
- `action` (str): One of `status`, `enable`, `disable`, `remove`, `configure`
- `frequency_minutes` (int, optional): Indexing interval (default: 30)

**Examples:**
```python
# Check status
manage_scheduler(action="status")

# Enable with 1-hour interval
manage_scheduler(action="enable", frequency_minutes=60)

# Disable
manage_scheduler(action="disable")
```

### `index_conversations`
Manually trigger conversation indexing.

**Parameters:**
- `full_reindex` (bool): Reindex all conversations (default: False)
- `time_range` (str, optional): Time filter - "1h", "24h", "7d", "30d"
- `force` (bool): Bypass rate limiting (default: False)

**Examples:**
```python
# Index new conversations only
index_conversations()

# Full reindex
index_conversations(full_reindex=True, force=True)

# Index last 24 hours
index_conversations(time_range="24h")
```

## API Configuration

By default, the MCP server connects to `http://localhost:8080`.

To use a different endpoint, set the environment variable:
```bash
export AGENT_GENESIS_API_URL=http://your-host:port
```

## Resources

The server provides MCP resources for documentation:

- `config://api-endpoints` - API endpoint documentation
- `agentgenesis://stats` - Current corpus statistics
- `agentgenesis://health` - API health status

## Troubleshooting

### "API Connection Failed"
Ensure the Agent Genesis Docker container is running:
```bash
docker-compose ps
curl http://localhost:8080/health
```

### "Timeout" errors
The API might be busy indexing. Try again in a few seconds.

### Tool not found in Claude
1. Verify the MCP server is in your config file
2. Restart Claude Code / Claude Desktop
3. Check MCP logs (View → Developer → MCP Logs)

## Development

```bash
# Clone the repo
git clone https://github.com/agentgenesis/agent-genesis.git
cd agent-genesis/mcp-server

# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black .
ruff check .
```

## License

MIT License - see [LICENSE](../LICENSE) for details.
