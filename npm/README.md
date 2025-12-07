# @agent-genesis/claude-config

> Configure Claude Desktop/Code to use Agent Genesis MCP server

This CLI tool automatically configures your Claude installation to use Agent Genesis for searching your conversation history.

## Installation

```bash
npx @agent-genesis/claude-config setup
```

Or install globally:

```bash
npm install -g @agent-genesis/claude-config
agent-genesis-config setup
```

## What it does

1. Checks if `agent-genesis-mcp` Python package is installed
2. Installs it via pip if missing
3. Adds Agent Genesis to your Claude configuration file
4. Provides next steps for running the API

## Commands

### Setup (default)

Configure Claude to use Agent Genesis:

```bash
npx @agent-genesis/claude-config setup
```

### Status

Check current configuration status:

```bash
npx @agent-genesis/claude-config status
```

### Remove

Remove Agent Genesis from Claude configuration:

```bash
npx @agent-genesis/claude-config remove
```

## Prerequisites

- Node.js 16+
- Python 3.10+
- Docker (for running Agent Genesis API)

## Full Setup

For the complete Agent Genesis setup:

```bash
# 1. Clone the repository
git clone https://github.com/agentgenesis/agent-genesis.git
cd agent-genesis

# 2. Run the setup script
./scripts/setup.sh

# 3. Configure Claude (if not done by setup.sh)
npx @agent-genesis/claude-config setup

# 4. Restart Claude Desktop/Code
```

## Configuration File Locations

| Platform | Path |
|----------|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |
| WSL | Uses Windows path automatically |

## License

MIT
