# Agent Genesis

> 🔍 **Semantic search across your Claude Code conversation history**

Agent Genesis indexes and searches your Claude Code and Claude Desktop conversations, making it easy to find past discussions, decisions, and solutions.

![Version](https://img.shields.io/badge/version-1.1.0-green.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![Docker](https://img.shields.io/badge/docker-required-blue.svg)

## Features

- 🔎 **Semantic Search** - Find conversations by meaning, not just keywords
- 📚 **Dual Source Support** - Index both Claude Code (JSONL) and Claude Desktop (LevelDB)
- 🔌 **MCP Integration** - Use directly from Claude Code via MCP protocol
- 🐳 **Docker Deployment** - Easy setup with Docker Compose
- ⚡ **Fast Indexing** - ChromaDB-powered vector search
- 📊 **Statistics** - Track your indexed conversations

## System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **Memory** | 4GB | 6GB+ |
| **Disk** | 2GB | 5GB+ |
| **Docker** | 20.10+ | Latest |
| **Python** | 3.10+ | 3.11+ |

> **Note**: ChromaDB + sentence-transformers require ~3.5GB baseline memory. The default configuration allocates 6GB to prevent OOM issues during indexing operations.

## Quick Start

### Option 1: Automated Setup (Recommended)

```bash
git clone https://github.com/agentgenesis/agent-genesis.git
cd agent-genesis
./scripts/setup.sh
```

The setup script will:
1. Detect your Claude data paths
2. Create configuration files
3. Build and start the Docker container
4. Optionally install the MCP server

### Option 2: Manual Setup

1. **Clone and configure:**
```bash
git clone https://github.com/agentgenesis/agent-genesis.git
cd agent-genesis
cp .env.example .env
cp docker-compose.template.yml docker-compose.yml
```

2. **Edit `.env` with your paths:**
```bash
# Linux/macOS
CLAUDE_PROJECTS_PATH=~/.claude/projects

# Claude Desktop path varies by OS:
# macOS: ~/Library/Application Support/Claude/Local Storage/leveldb
# Linux: ~/.config/Claude/Local Storage/leveldb
# Windows (WSL): /mnt/c/Users/YOUR_USER/AppData/Roaming/Claude/Local Storage/leveldb
CLAUDE_DESKTOP_LEVELDB_PATH=/path/to/leveldb
```

3. **Start the service:**
```bash
docker-compose up -d
```

4. **Trigger initial indexing:**
```bash
curl -X POST http://localhost:8080/index/trigger
```

## Usage

### REST API

**Search conversations:**
```bash
curl -X POST http://localhost:8080/search \
  -H "Content-Type: application/json" \
  -d '{"query": "how to implement authentication", "n_results": 10}'
```

**Get statistics:**
```bash
curl http://localhost:8080/stats
```

**Health check:**
```bash
curl http://localhost:8080/health
```

### MCP Integration (Claude Code)

Install the MCP server:
```bash
pip install agent-genesis-mcp
```

Add to your Claude Code configuration (`~/.claude/claude_desktop_config.json`):
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

Now you can search your conversation history directly from Claude Code!

**Available MCP Tools:**
- `search_conversations` - Semantic search across your history
- `get_api_stats` - Get corpus statistics
- `check_api_health` - Verify API connectivity
- `manage_scheduler` - Control automatic indexing
- `index_conversations` - Trigger manual indexing

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Claude Code                             │
│                          │                                   │
│                     MCP Protocol                             │
│                          ▼                                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              agent-genesis-mcp                        │   │
│  │           (pip install agent-genesis-mcp)             │   │
│  └──────────────────────────────────────────────────────┘   │
│                          │                                   │
│                     HTTP REST API                            │
│                          ▼                                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Agent Genesis API                        │   │
│  │                (Docker Container)                     │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────────┐  │   │
│  │  │  Indexer   │  │  ChromaDB  │  │ Embedding Gen  │  │   │
│  │  └────────────┘  └────────────┘  └────────────────┘  │   │
│  └──────────────────────────────────────────────────────┘   │
│                          │                                   │
│              ┌───────────┴───────────┐                      │
│              ▼                       ▼                       │
│  ┌──────────────────┐    ┌──────────────────┐               │
│  │  Claude Code     │    │  Claude Desktop   │               │
│  │  ~/.claude/      │    │  LevelDB          │               │
│  │  projects/       │    │                   │               │
│  └──────────────────┘    └──────────────────┘               │
└─────────────────────────────────────────────────────────────┘
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CLAUDE_PROJECTS_PATH` | Path to Claude Code projects | Required |
| `CLAUDE_DESKTOP_LEVELDB_PATH` | Path to Claude Desktop LevelDB | Optional |
| `API_PORT` | Port for REST API | `8080` |
| `LOG_LEVEL` | Logging level | `INFO` |

### Automatic Indexing

Enable scheduled indexing via the MCP server:
```python
manage_scheduler(action="enable", frequency_minutes=30)
```

Or via API:
```bash
curl -X POST http://localhost:8080/index/trigger
```

## Data Privacy

- **Local only**: All data stays on your machine
- **Read-only access**: Agent Genesis only reads your conversation files
- **No telemetry**: ChromaDB telemetry is disabled
- **Your data, your control**: Delete the Docker volume to remove all indexed data

## Health Monitoring

Agent Genesis includes scripts for monitoring container health and preventing issues.

### Health Check Script

Run every 5 minutes via cron to ensure the service stays healthy:

```bash
# Add to crontab
*/5 * * * * /path/to/agent-genesis/scripts/health-check.sh
```

Features:
- Automatic container restart if unhealthy
- Memory usage monitoring (alerts at 80% threshold)
- Automatic backup trigger if daily backup is missing

### Memory Alert Script

Standalone memory monitoring with configurable threshold:

```bash
# Check memory (default 80% threshold)
./scripts/memory-alert.sh

# Custom threshold
./scripts/memory-alert.sh 70
```

### Backup Script

Daily ChromaDB backup with rotation:

```bash
# Add to crontab for daily 2 AM backup
0 2 * * * /path/to/agent-genesis/scripts/backup-chromadb.sh
```

Backups are stored in `./backups/` with 7-day retention.

## Troubleshooting

### API not responding
```bash
# Check container status
docker-compose ps

# View logs
docker-compose logs -f agent-genesis

# Restart
docker-compose restart
```

### Path not found errors
Ensure your `.env` paths are correct and accessible:
```bash
# Test Claude Code path
ls -la ~/.claude/projects/

# Test Claude Desktop path (varies by OS)
ls -la "/path/to/leveldb"
```

### Indexing takes too long
For initial indexing of large histories, expect 5-10 minutes. Subsequent incremental indexes are much faster.

## Development

### Run locally (without Docker)
```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start API server
python -m daemon.api_server
```

### Run tests
```bash
cd mcp-server
pip install -e ".[dev]"
pytest
```

## Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

MIT License - see [LICENSE](LICENSE) for details.

## Changelog

### v1.1.0 (2024-12-31)
- **Memory**: Increased container memory limit from 4GB to 6GB
- **Stability**: Added `mem_limit`/`memswap_limit` for better OOM prevention
- **Monitoring**: Added health check script with memory monitoring
- **Monitoring**: Added standalone memory alert script
- **Backup**: Added automated ChromaDB backup script with rotation
- **Portability**: All scripts now use relative paths

### v1.0.0 (2024-11-02)
- Initial release
- Semantic search for Claude Code and Claude Desktop conversations
- MCP integration for use within Claude Code
- Docker-based deployment
- REST API for search and indexing

## Acknowledgments

- Built with [ChromaDB](https://www.trychroma.com/) for vector storage
- Uses [sentence-transformers](https://www.sbert.net/) for embeddings
- MCP integration via [FastMCP](https://github.com/jlowin/fastmcp)
