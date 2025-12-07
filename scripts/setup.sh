#!/bin/bash
# Agent Genesis - Cross-Platform Setup Script
# Works on Linux, macOS, and Windows (WSL/Git Bash)

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Banner
echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║           Agent Genesis - Installation Script                 ║"
echo "║     Search your Claude Code conversation history              ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# Detect OS
detect_os() {
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        if grep -qEi "(Microsoft|WSL)" /proc/version 2>/dev/null; then
            echo "wsl"
        else
            echo "linux"
        fi
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
        echo "windows"
    else
        echo "unknown"
    fi
}

OS=$(detect_os)
log_info "Detected OS: $OS"

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."

    # Check Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed. Please install Docker first."
        log_info "Visit: https://docs.docker.com/get-docker/"
        exit 1
    fi
    log_success "Docker found: $(docker --version)"

    # Check Docker Compose
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        log_error "Docker Compose is not installed."
        exit 1
    fi
    log_success "Docker Compose found"

    # Check if Docker daemon is running
    if ! docker info &> /dev/null; then
        log_error "Docker daemon is not running. Please start Docker."
        exit 1
    fi
    log_success "Docker daemon is running"
}

# Get Claude Desktop LevelDB path
get_leveldb_path() {
    local default_path=""

    case $OS in
        wsl)
            # Try to find Windows username
            WIN_USER=$(cmd.exe /c "echo %USERNAME%" 2>/dev/null | tr -d '\r' || echo "")
            if [[ -n "$WIN_USER" ]]; then
                default_path="/mnt/c/Users/$WIN_USER/AppData/Roaming/Claude/Local Storage/leveldb"
            fi
            ;;
        macos)
            default_path="$HOME/Library/Application Support/Claude/Local Storage/leveldb"
            ;;
        linux)
            default_path="$HOME/.config/Claude/Local Storage/leveldb"
            ;;
        windows)
            default_path="$APPDATA/Claude/Local Storage/leveldb"
            ;;
    esac

    echo ""
    log_info "Claude Desktop LevelDB path stores your Claude Desktop conversations."

    if [[ -n "$default_path" ]] && [[ -d "$default_path" ]]; then
        log_success "Found Claude Desktop data at: $default_path"
        read -p "Use this path? [Y/n]: " use_default
        if [[ "$use_default" != "n" ]] && [[ "$use_default" != "N" ]]; then
            CLAUDE_DESKTOP_LEVELDB_PATH="$default_path"
            return
        fi
    else
        log_warn "Could not auto-detect Claude Desktop path."
    fi

    read -p "Enter Claude Desktop LevelDB path: " CLAUDE_DESKTOP_LEVELDB_PATH

    if [[ ! -d "$CLAUDE_DESKTOP_LEVELDB_PATH" ]]; then
        log_warn "Path does not exist: $CLAUDE_DESKTOP_LEVELDB_PATH"
        log_info "You can leave this empty if you don't use Claude Desktop."
        read -p "Continue anyway? [y/N]: " continue_anyway
        if [[ "$continue_anyway" != "y" ]] && [[ "$continue_anyway" != "Y" ]]; then
            exit 1
        fi
    fi
}

# Get Claude Code projects path
get_projects_path() {
    local default_path="$HOME/.claude/projects"

    echo ""
    log_info "Claude Code projects path stores your Claude Code conversations."

    if [[ -d "$default_path" ]]; then
        log_success "Found Claude Code projects at: $default_path"
        read -p "Use this path? [Y/n]: " use_default
        if [[ "$use_default" != "n" ]] && [[ "$use_default" != "N" ]]; then
            CLAUDE_PROJECTS_PATH="$default_path"
            return
        fi
    else
        log_warn "Default path not found: $default_path"
    fi

    read -p "Enter Claude Code projects path: " CLAUDE_PROJECTS_PATH

    if [[ ! -d "$CLAUDE_PROJECTS_PATH" ]]; then
        log_error "Path does not exist: $CLAUDE_PROJECTS_PATH"
        log_info "Make sure you've used Claude Code at least once."
        exit 1
    fi
}

# Get API port
get_api_port() {
    echo ""
    read -p "API port [8080]: " API_PORT
    API_PORT=${API_PORT:-8080}

    # Check if port is in use
    if lsof -i:$API_PORT &> /dev/null 2>&1 || netstat -tuln 2>/dev/null | grep -q ":$API_PORT "; then
        log_warn "Port $API_PORT appears to be in use."
        read -p "Continue anyway? [y/N]: " continue_anyway
        if [[ "$continue_anyway" != "y" ]] && [[ "$continue_anyway" != "Y" ]]; then
            get_api_port
        fi
    fi
}

# Create .env file
create_env_file() {
    log_info "Creating .env file..."

    cat > .env << EOF
# Agent Genesis Configuration
# Generated by setup.sh on $(date)

CLAUDE_DESKTOP_LEVELDB_PATH=$CLAUDE_DESKTOP_LEVELDB_PATH
CLAUDE_PROJECTS_PATH=$CLAUDE_PROJECTS_PATH
API_PORT=$API_PORT
EOF

    log_success "Created .env file"
}

# Create docker-compose.yml from template
create_docker_compose() {
    log_info "Creating docker-compose.yml..."

    if [[ -f "docker-compose.template.yml" ]]; then
        cp docker-compose.template.yml docker-compose.yml
        log_success "Created docker-compose.yml from template"
    else
        log_error "docker-compose.template.yml not found!"
        exit 1
    fi
}

# Build and start containers
start_containers() {
    echo ""
    log_info "Building and starting Agent Genesis..."

    docker-compose build
    docker-compose up -d

    log_success "Agent Genesis is starting..."

    # Wait for health check
    log_info "Waiting for API to be ready..."
    for i in {1..30}; do
        if curl -s "http://localhost:$API_PORT/health" &> /dev/null; then
            log_success "API is healthy!"
            break
        fi
        sleep 1
        echo -n "."
    done
    echo ""
}

# Install MCP server
install_mcp_server() {
    echo ""
    log_info "Would you like to install the MCP server for Claude Code integration?"
    read -p "Install MCP server? [Y/n]: " install_mcp

    if [[ "$install_mcp" != "n" ]] && [[ "$install_mcp" != "N" ]]; then
        if command -v pip &> /dev/null; then
            log_info "Installing agent-genesis-mcp..."
            pip install -e ./mcp-server || pip install ./mcp-server
            log_success "MCP server installed!"

            log_info "To add to Claude Code, add this to your claude_desktop_config.json:"
            echo ""
            echo '  "agent-genesis": {'
            echo '    "command": "python",'
            echo '    "args": ["-m", "agent_genesis_mcp"]'
            echo '  }'
            echo ""
        else
            log_warn "pip not found. Install MCP server manually: pip install ./mcp-server"
        fi
    fi
}

# Print summary
print_summary() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║                    Installation Complete!                     ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo ""
    log_success "Agent Genesis is running at: http://localhost:$API_PORT"
    echo ""
    echo "Quick commands:"
    echo "  - Check status:  docker-compose ps"
    echo "  - View logs:     docker-compose logs -f"
    echo "  - Stop:          docker-compose down"
    echo "  - Restart:       docker-compose restart"
    echo ""
    echo "API Endpoints:"
    echo "  - Health:  GET  http://localhost:$API_PORT/health"
    echo "  - Search:  POST http://localhost:$API_PORT/search"
    echo "  - Stats:   GET  http://localhost:$API_PORT/stats"
    echo ""
    echo "To trigger initial indexing:"
    echo "  curl -X POST http://localhost:$API_PORT/index/trigger"
    echo ""
}

# Main execution
main() {
    check_prerequisites
    get_leveldb_path
    get_projects_path
    get_api_port
    create_env_file
    create_docker_compose
    start_containers
    install_mcp_server
    print_summary
}

# Run main
main "$@"
