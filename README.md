# Agent Genesis

> 🔍 **Semantic search across your Claude Code conversation history**

Agent Genesis indexes and searches your Claude Code and Claude.ai/Desktop conversations, making it easy to find past discussions, decisions, and solutions.

🔒 **Privacy First**: Runs 100% locally. No API keys required. Your conversations never leave your machine.

---

## What Does It Do?

Ask questions like:
- *"Find my conversations about authentication"*
- *"When did I discuss database optimization?"*
- *"What was that solution for the API rate limiting issue?"*

Agent Genesis finds relevant conversations using **semantic search** (meaning-based, not just keyword matching).

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Stopping the Service](#stopping-the-service)
- [Importing Claude.ai Data](#importing-claudeai--claude-desktop-data)
- [Searching Your Conversations](#searching-your-conversations)
- [MCP Integration](#mcp-integration-optional)
- [Troubleshooting](#troubleshooting)
- [How It Compares](#how-it-compares)
- [Configuration Reference](#configuration-reference)
- [Contributing](#contributing)

---

## Prerequisites

Before you start, you need:

- **Docker** (version 20.10+) - [Install Docker](https://docs.docker.com/get-docker/)
- **6GB+ RAM** available for Docker (required for the local embedding model)
- **Claude Code conversations** in your projects folder (created automatically when you use Claude Code)

**Verify Docker is installed:**
```bash
docker --version
# Should output: Docker version 20.10.x or higher
```

**Find your Claude Code projects path:**

| OS | Path |
|----|------|
| Linux | `~/.claude/projects/` |
| macOS | `~/.claude/projects/` |
| Windows | `C:\Users\YourName\.claude\projects\` |
| Windows (WSL) | `/home/youruser/.claude/projects/` |

---

## Quick Start

### Step 1: Clone the Repository

```bash
git clone https://github.com/agentgenesis/agent-genesis.git
cd agent-genesis
```

### Step 2: Create Your Configuration

```bash
cp .env.example .env
cp docker-compose.template.yml docker-compose.yml
```

### Step 3: Set Your Claude Code Path

Open `.env` in any text editor and set your Claude Code projects path:

**Linux/macOS:**
```bash
CLAUDE_PROJECTS_PATH=/home/youruser/.claude/projects
```

**Windows (use forward slashes):**
```bash
CLAUDE_PROJECTS_PATH=C:/Users/YourName/.claude/projects
```

**Verify your path exists:**
```bash
# Linux/macOS
ls ~/.claude/projects/

# Windows (PowerShell)
dir $env:USERPROFILE\.claude\projects
```

You should see folders with names like `-home-user-project-myapp`.

### Step 4: Start the Service

```bash
docker-compose up -d
```

Wait about 30 seconds for startup, then verify it's running:
```bash
curl http://localhost:8080/health
# Should return: {"status": "OK", ...}
```

**Windows (PowerShell):**
```powershell
Invoke-RestMethod http://localhost:8080/health
```

### Step 5: Index Your Conversations

```bash
curl -X POST http://localhost:8080/index/trigger
```

This scans your Claude Code conversations and indexes them. First run may take 1-5 minutes depending on history size.

### Step 6: Search!

```bash
curl -X POST http://localhost:8080/search \
  -H "Content-Type: application/json" \
  -d '{"query": "authentication", "n_results": 5}'
```

**Example output:**
```json
{
  "query": "authentication",
  "results_count": 3,
  "results": [
    {
      "content": "Let me help you implement JWT authentication...",
      "project": "my-webapp",
      "score": 0.89
    },
    {
      "content": "For OAuth2, you'll need to set up...",
      "project": "api-server",
      "score": 0.76
    }
  ]
}
```

**🎉 Done!** Your Claude Code conversations are now searchable.

---

## Stopping the Service

When you're done, stop the service to free up resources:

```bash
# Stop the containers
docker-compose down

# To restart later
docker-compose up -d
```

**Note:** Your indexed data is preserved. You don't need to re-index after restarting.

---

## Importing Claude.ai / Claude Desktop Data

> ⚠️ **Important**: Claude.ai and Claude Desktop store conversations **in the cloud**, not on your computer. You must request a data export from Anthropic.

### Step 1: Request Your Data

1. Go to [claude.ai](https://claude.ai)
2. Click your profile → Settings → Account
3. Request a data export
4. Wait for the email (can take 24-48 hours)
5. Download the ZIP file

### Step 2: Import the ZIP

```bash
# Create the data folder if it doesn't exist
mkdir -p ./data

# Copy your ZIP file
cp /path/to/your/data-export.zip ./data/

# Run the import
docker exec -it agent-genesis python /app/import_to_container.py /app/data/data-export.zip
```

### Step 3: Verify

```bash
curl http://localhost:8080/stats
```

You should see counts in both `alpha` (Claude Code) and `beta` (Claude.ai) collections.

---

## Searching Your Conversations

### Basic Search

```bash
curl -X POST http://localhost:8080/search \
  -H "Content-Type: application/json" \
  -d '{"query": "how to fix the login bug", "n_results": 10}'
```

### Search Only Claude Code

```bash
curl -X POST http://localhost:8080/search \
  -H "Content-Type: application/json" \
  -d '{"query": "database", "collections": ["alpha"]}'
```

### Search Only Claude.ai

```bash
curl -X POST http://localhost:8080/search \
  -H "Content-Type: application/json" \
  -d '{"query": "project planning", "collections": ["beta"]}'
```

### Filter by Project

```bash
curl -X POST http://localhost:8080/search \
  -H "Content-Type: application/json" \
  -d '{"query": "API design", "project_filter": "my-project"}'
```

---

## MCP Integration (Optional)

Search your conversations directly from Claude Code using the MCP protocol.

**How it works:** The MCP server runs on your host machine and acts as a bridge to the Agent Genesis API running in Docker.

### Step 1: Install the MCP Server

Run this on your **host machine** (not inside Docker):

```bash
pip install agent-genesis-mcp
```

> 📦 [View on PyPI](https://pypi.org/project/agent-genesis-mcp/)

### Step 2: Configure Claude Code

Add to your `~/.claude.json`:

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

### Step 3: Restart Claude Code

Restart Claude Code to load the new MCP server.

### Step 4: Use It

Ask Claude Code:

> *"Search my conversation history for discussions about authentication"*

Claude will use the `search_conversations` tool automatically.

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `search_conversations` | Search your conversation history |
| `get_api_stats` | See indexed conversation counts |
| `check_api_health` | Check if API is running |
| `index_conversations` | Trigger re-indexing |
| `manage_scheduler` | Set up automatic re-indexing |

---

## Troubleshooting

### Service won't start

```bash
# Check container status
docker-compose ps

# View logs for errors
docker-compose logs agent-genesis
```

### "Connection refused" when calling API

1. Wait 30 seconds after `docker-compose up -d`
2. Check if container is running: `docker-compose ps`
3. Check logs: `docker-compose logs agent-genesis`

### Port 8080 already in use

Edit `docker-compose.yml` and change the port:

```yaml
ports:
  - "8081:8080"  # Change 8081 to any free port
```

Then restart: `docker-compose down && docker-compose up -d`

### Search returns no results

1. Check if data is indexed:
   ```bash
   curl http://localhost:8080/stats
   # Should show count > 0
   ```

2. If count is 0, trigger indexing:
   ```bash
   curl -X POST http://localhost:8080/index/trigger
   ```

3. Verify your `.env` path is correct:
   ```bash
   ls ~/.claude/projects/
   # Should show folders
   ```

### Container runs out of memory

The service needs ~4-6GB RAM for the embedding model. Increase Docker memory:

1. Edit `docker-compose.yml`:
   ```yaml
   mem_limit: 8g
   ```

2. Restart: `docker-compose down && docker-compose up -d`

### ZIP import fails

- Ensure you're using an official Anthropic data export
- The ZIP must contain `conversations.json`
- Check the import output for specific errors

### MCP server not connecting

1. Verify Docker container is running: `curl http://localhost:8080/health`
2. Verify MCP is installed: `pip show agent-genesis-mcp`
3. Check Claude Code logs for MCP errors

---

## How It Compares

| Feature | Agent Genesis | [episodic-memory](https://github.com/obra/episodic-memory) | [claude-mem](https://github.com/thedotmack/claude-mem) |
|---------|--------------|-------------------|------------|
| **Deployment** | Docker container | Claude Code plugin | Claude Code plugin |
| **Claude Code** | ✅ | ✅ | ✅ |
| **Claude.ai/Desktop** | ✅ ZIP import | ❌ | ❌ |
| **REST API** | ✅ | ❌ | ❌ |
| **MCP Server** | ✅ | ✅ | ✅ |
| **100% Local** | ✅ | ✅ | ✅ |

**Choose Agent Genesis if you:**
- Need a **REST API** for external integrations
- Want to search **Claude.ai/Desktop** history (only option with ZIP import)
- Prefer a **standalone service** over a Claude Code plugin

---

## Configuration Reference

### Environment Variables (.env)

| Variable | Description | Default |
|----------|-------------|--------|
| `CLAUDE_PROJECTS_PATH` | Path to Claude Code projects | Required |
| `API_PORT` | Port for the API | `8080` |
| `LOG_LEVEL` | Logging level | `INFO` |

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/stats` | GET | Conversation counts |
| `/search` | POST | Search conversations |
| `/index/trigger` | POST | Trigger indexing |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Your Computer (everything runs locally)                    │
│                                                             │
│  ┌─────────────────┐    ┌───────────────────────────────┐    │
│  │  Claude Code    │    │  Agent Genesis (Docker)       │    │
│  │                 │    │                               │    │
│  │  ~/.claude/     │───▶│  ChromaDB + Local Embeddings  │    │
│  │  projects/      │    │  (bge-small model)            │    │
│  └─────────────────┘    │                               │    │
│                        │  ┌─────────────┐ ┌─────────────┐ │    │
│  ┌─────────────────┐    │  │ Alpha       │ │ Beta        │ │    │
│  │ MCP Server      │───▶│  │ Claude Code │ │ Claude.ai   │ │    │
│  │ (optional)      │    │  └─────────────┘ └─────────────┘ │    │
│  └─────────────────┘    └───────────────────────────────┘    │
│                                                             │
│  🔒 No data leaves your machine. No API keys needed.        │
└─────────────────────────────────────────────────────────────┘
```

---

## Development

### Run Without Docker

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m daemon.api_server
```

### Run Tests

```bash
cd mcp-server
pip install -e ".[dev]"
pytest
```

---

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests
5. Submit a pull request

---

## License

MIT License - see [LICENSE](LICENSE)

---

## Changelog

### v1.2.0 (2025-01-02)
- Complete documentation rewrite
- Fixed: Removed incorrect LevelDB claims (Claude.ai uses cloud storage)
- Added: Windows path instructions
- Added: Privacy statement (100% local)
- Added: Troubleshooting section
- Added: Stopping the service instructions

### v1.1.0 (2024-12-31)
- Increased container memory to 6GB
- Added health monitoring scripts
- Added automated backups

### v1.0.0 (2024-11-02)
- Initial release
