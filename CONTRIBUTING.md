# Contributing to Agent Genesis

Thank you for your interest in contributing to Agent Genesis! This document provides guidelines and instructions for contributing.

## Getting Started

### Prerequisites

- Python 3.10+
- Docker & Docker Compose
- Node.js 16+ (for npm package development)
- Git

### Development Setup

1. **Fork and clone the repository:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/agent-genesis.git
   cd agent-genesis
   ```

2. **Set up Python environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # or `venv\Scripts\activate` on Windows
   pip install -r requirements.txt
   ```

3. **Set up MCP server development:**
   ```bash
   cd mcp-server
   pip install -e ".[dev]"
   ```

4. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your paths
   ```

5. **Start the development server:**
   ```bash
   docker-compose up -d
   ```

## Project Structure

```
agent-genesis/
├── daemon/              # Python backend (API server, indexer)
│   ├── api_server.py    # REST API server
│   ├── indexer.py       # Conversation indexer
│   ├── knowledge_db_dual.py  # ChromaDB integration
│   └── ...
├── mcp-server/          # MCP server (Python/FastMCP)
│   ├── agent_genesis_mcp.py  # MCP server implementation
│   └── pyproject.toml   # PyPI package config
├── npm/                 # npm configuration helper
│   ├── bin/setup.js     # CLI tool
│   └── package.json     # npm package config
├── scripts/             # Installation scripts
│   └── setup.sh         # Cross-platform installer
├── .github/workflows/   # CI/CD pipelines
└── docker-compose.template.yml
```

## Making Changes

### Code Style

**Python:**
- Follow PEP 8
- Use type hints
- Run `black` and `ruff` before committing:
  ```bash
  black daemon/ mcp-server/
  ruff check daemon/ mcp-server/
  ```

**JavaScript:**
- Use ES6+ features
- Prefer `const` over `let`
- Handle errors gracefully

### Testing

**Run Python tests:**
```bash
cd mcp-server
pytest
```

**Test Docker build:**
```bash
docker-compose build
docker-compose up -d
curl http://localhost:8080/health
```

**Test MCP server:**
```bash
cd mcp-server
python test_mcp.py
```

### Commit Messages

Use clear, descriptive commit messages:
- `feat: add project filtering to search`
- `fix: handle empty conversations gracefully`
- `docs: update installation instructions`
- `refactor: simplify indexer pipeline`

## Pull Request Process

1. **Create a feature branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes and test thoroughly**

3. **Update documentation if needed**

4. **Push and create a pull request:**
   ```bash
   git push origin feature/your-feature-name
   ```

5. **In the PR description:**
   - Describe what changes you made
   - Explain why the changes are needed
   - Note any breaking changes
   - Reference related issues

## Reporting Issues

When reporting issues, please include:

- Operating system and version
- Python version
- Docker version
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs or error messages

## Feature Requests

We welcome feature requests! Please:

- Check existing issues first
- Describe the use case
- Explain why it would be valuable
- Consider if you could implement it

## Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Help others learn and grow

## Questions?

If you have questions, feel free to:
- Open a discussion on GitHub
- Check existing issues and discussions

Thank you for contributing!
