#!/bin/bash
#
# Code Hacker — One-Click Installer (macOS / Linux)
#
# Usage:
#   bash install.sh           # Full install: deps + VS Code config + start servers
#   bash install.sh --no-vscode   # Skip VS Code configuration
#   bash install.sh --servers-only  # Only start MCP servers (skip install)
#

set -e

# ─── Colors ───────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# ─── Banner ───────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════════╗"
echo "  ║        Code Hacker — Installer           ║"
echo "  ║   VS Code Custom Agent + MCP Servers     ║"
echo "  ╚══════════════════════════════════════════╝"
echo -e "${NC}"

# ─── Parse Args ───────────────────────────────────────────────────────
SKIP_VSCODE=false
SERVERS_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --no-vscode)     SKIP_VSCODE=true ;;
        --servers-only)  SERVERS_ONLY=true ;;
        --help|-h)
            echo "Usage: bash install.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --no-vscode      Skip VS Code configuration"
            echo "  --servers-only   Only start MCP servers (skip install steps)"
            echo "  --help           Show this help"
            exit 0
            ;;
    esac
done

# ─── Step 1: Check Prerequisites ─────────────────────────────────────
if [ "$SERVERS_ONLY" = false ]; then
    echo -e "${BOLD}Step 1/5: Checking prerequisites${NC}"
    echo ""

    # Python 3.11+
    PYTHON_CMD=""
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
                PYTHON_CMD="$cmd"
                ok "Python $ver found ($cmd)"
                break
            fi
        fi
    done
    if [ -z "$PYTHON_CMD" ]; then
        fail "Python 3.11+ is required. Install from https://python.org or: brew install python@3.12"
    fi

    # uv (preferred) or pip
    USE_UV=false
    if command -v uv &>/dev/null; then
        ok "uv found ($(uv --version))"
        USE_UV=true
    elif "$PYTHON_CMD" -m pip --version &>/dev/null; then
        ok "pip found"
    else
        info "Installing uv (recommended Python package manager)..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        if command -v uv &>/dev/null; then
            ok "uv installed successfully"
            USE_UV=true
        else
            fail "Failed to install uv. Please install manually: https://docs.astral.sh/uv/"
        fi
    fi

    # VS Code CLI (optional)
    VSCODE_AVAILABLE=false
    if command -v code &>/dev/null; then
        ok "VS Code CLI found"
        VSCODE_AVAILABLE=true
    else
        warn "VS Code CLI (code) not found — skipping VS Code configuration"
        warn "Install from: https://code.visualstudio.com"
        warn "Then run 'Shell Command: Install code command in PATH' from VS Code"
    fi

    # Git
    if command -v git &>/dev/null; then
        ok "Git found"
    else
        fail "Git is required. Install: brew install git"
    fi

    # ag (optional)
    if command -v ag &>/dev/null; then
        ok "ag (The Silver Searcher) found"
    else
        warn "ag not found — search_files_ag will use fallback grep"
        warn "Recommended: brew install the_silver_searcher"
    fi

    echo ""

    # ─── Step 2: Install Python Dependencies ─────────────────────────
    echo -e "${BOLD}Step 2/5: Installing Python dependencies${NC}"
    echo ""

    cd "$SCRIPT_DIR"

    if [ "$USE_UV" = true ]; then
        info "Running uv sync..."
        uv sync
        ok "Dependencies installed via uv"
        PYTHON_CMD="uv run python"
    else
        info "Running pip install..."
        "$PYTHON_CMD" -m pip install -e ".[dev]" --quiet
        ok "Dependencies installed via pip"
    fi

    echo ""

    # ─── Step 3: Configure VS Code ───────────────────────────────────
    echo -e "${BOLD}Step 3/5: Configuring VS Code${NC}"
    echo ""

    if [ "$SKIP_VSCODE" = true ]; then
        info "Skipping VS Code configuration (--no-vscode)"
    elif [ "$VSCODE_AVAILABLE" = true ]; then

        # Install Copilot extension if missing
        if code --list-extensions 2>/dev/null | grep -qi "github.copilot-chat"; then
            ok "GitHub Copilot Chat extension installed"
        else
            info "Installing GitHub Copilot Chat extension..."
            code --install-extension GitHub.copilot-chat --force 2>/dev/null || warn "Could not install Copilot Chat extension — install manually"
        fi

        if code --list-extensions 2>/dev/null | grep -qi "github.copilot"; then
            ok "GitHub Copilot extension installed"
        else
            info "Installing GitHub Copilot extension..."
            code --install-extension GitHub.copilot --force 2>/dev/null || warn "Could not install Copilot extension — install manually"
        fi

        # Inject MCP server config into VS Code user settings
        VSCODE_SETTINGS_DIR="$HOME/Library/Application Support/Code/User"
        if [ "$(uname)" = "Linux" ]; then
            VSCODE_SETTINGS_DIR="$HOME/.config/Code/User"
        fi
        VSCODE_SETTINGS="$VSCODE_SETTINGS_DIR/settings.json"

        if [ -f "$VSCODE_SETTINGS" ]; then
            info "Updating VS Code user settings with MCP servers..."
        else
            info "Creating VS Code user settings with MCP servers..."
            mkdir -p "$VSCODE_SETTINGS_DIR"
            echo "{}" > "$VSCODE_SETTINGS"
        fi

        # Use Python to safely merge JSON (avoid breaking existing settings)
        $PYTHON_CMD -c "
import json, sys, os

settings_path = '$VSCODE_SETTINGS'
try:
    with open(settings_path, 'r') as f:
        content = f.read().strip()
        if not content:
            settings = {}
        else:
            settings = json.loads(content)
except (json.JSONDecodeError, FileNotFoundError):
    settings = {}

mcp_servers = {
    'filesystem-command': {'type': 'sse', 'url': 'http://localhost:8001/mcp'},
    'git-tools':          {'type': 'sse', 'url': 'http://localhost:8002/mcp'},
    'code-intel':         {'type': 'sse', 'url': 'http://localhost:8003/mcp'},
    'memory-store':       {'type': 'sse', 'url': 'http://localhost:8004/mcp'},
    'code-review':        {'type': 'sse', 'url': 'http://localhost:8005/mcp'},
    'multi-project':      {'type': 'sse', 'url': 'http://localhost:8007/mcp'},
}

# Merge into existing mcp.servers without overwriting other MCP servers
if 'mcp' not in settings:
    settings['mcp'] = {}
if 'servers' not in settings['mcp']:
    settings['mcp']['servers'] = {}

settings['mcp']['servers'].update(mcp_servers)

# Enable agent mode and chat
settings.setdefault('chat.agent.enabled', True)

with open(settings_path, 'w') as f:
    json.dump(settings, f, indent=4)

print('OK')
"
        ok "MCP servers registered in VS Code user settings"

        # Verify agent file exists
        if [ -f "$SCRIPT_DIR/code-hacker.agent.md" ]; then
            ok "Agent definition file: code-hacker.agent.md"
        else
            warn "code-hacker.agent.md not found in project root"
        fi
    else
        info "VS Code not available — skipping"
    fi

    echo ""

fi  # end SERVERS_ONLY check

# ─── Step 4: Start MCP Servers ────────────────────────────────────────
STEP_NUM="4"
[ "$SERVERS_ONLY" = true ] && STEP_NUM="1"
echo -e "${BOLD}Step ${STEP_NUM}/5: Starting MCP servers${NC}"
echo ""

cd "$SCRIPT_DIR"

# Determine python command for servers (uv > .venv > system python)
if [ -z "$PYTHON_CMD" ]; then
    if command -v uv &>/dev/null && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
        PYTHON_CMD="uv run python"
    elif [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
        PYTHON_CMD="$SCRIPT_DIR/.venv/bin/python"
    else
        PYTHON_CMD="python3"
    fi
fi

# Pre-flight: check that MCP dependencies are importable
if ! $PYTHON_CMD -c "import mcp, fastapi, uvicorn" 2>/dev/null; then
    warn "MCP dependencies not found (mcp, fastapi, uvicorn)"
    if [ "$SERVERS_ONLY" = true ]; then
        fail "Dependencies missing. Run 'bash install.sh' for full install, or 'uv sync' / 'pip install -e .' first."
    fi
    info "Auto-installing dependencies..."
    if command -v uv &>/dev/null; then
        uv sync
        PYTHON_CMD="uv run python"
    elif $PYTHON_CMD -m pip --version &>/dev/null; then
        $PYTHON_CMD -m pip install -e . --quiet
    else
        fail "No package manager available. Install uv: https://docs.astral.sh/uv/"
    fi
    # Verify again
    if ! $PYTHON_CMD -c "import mcp, fastapi, uvicorn" 2>/dev/null; then
        fail "Dependencies still not importable after install. Check logs above."
    fi
    ok "Dependencies installed successfully"
fi

info "Using: $PYTHON_CMD"

PID_DIR="$SCRIPT_DIR/.mcp_pids"
mkdir -p "$PID_DIR"

SERVERS=(
    "filesystem.py:8001:filesystem-command"
    "git_tools.py:8002:git-tools"
    "code_intel.py:8003:code-intel"
    "memory_store.py:8004:memory-store"
    "code_review.py:8005:code-review"
    "multi_project.py:8007:multi-project"
)

ALL_UP=true
for entry in "${SERVERS[@]}"; do
    IFS=':' read -r script port name <<< "$entry"
    sname="${script%.py}"
    pid_file="$PID_DIR/$sname.pid"

    # Check if already running
    if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        ok "$name (port $port) — already running (PID $(cat "$pid_file"))"
        continue
    fi

    echo -n "  Starting $name on port $port ... "
    cd "$SCRIPT_DIR"
    NO_PROXY=localhost,127.0.0.1 $PYTHON_CMD "$script" > "$PID_DIR/$sname.log" 2>&1 &
    pid=$!
    echo "$pid" > "$pid_file"
    sleep 0.5

    if kill -0 "$pid" 2>/dev/null; then
        echo -e "${GREEN}OK${NC} (PID $pid)"
    else
        echo -e "${RED}FAILED${NC}"
        echo "    Log: $PID_DIR/$sname.log"
        tail -3 "$PID_DIR/$sname.log" 2>/dev/null | sed 's/^/    /'
        ALL_UP=false
    fi
done

echo ""

# ─── Step 5: Health Check ────────────────────────────────────────────
STEP_NUM="5"
[ "$SERVERS_ONLY" = true ] && STEP_NUM="2"
echo -e "${BOLD}Step ${STEP_NUM}/5: Verifying servers${NC}"
echo ""

sleep 1
PASS=0
TOTAL=0

for entry in "${SERVERS[@]}"; do
    IFS=':' read -r script port name <<< "$entry"
    TOTAL=$((TOTAL + 1))

    http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$port/mcp" --max-time 3 2>/dev/null || echo "000")

    if [ "$http_code" = "200" ] || [ "$http_code" = "405" ]; then
        ok "$name  http://localhost:$port/mcp"
        PASS=$((PASS + 1))
    else
        warn "$name  http://localhost:$port/mcp  (HTTP $http_code)"
    fi
done

echo ""

# ─── Summary ─────────────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}══════════════════════════════════════════${NC}"
if [ "$PASS" -eq "$TOTAL" ]; then
    echo -e "${GREEN}${BOLD}  Installation complete! ($PASS/$TOTAL servers running)${NC}"
else
    echo -e "${YELLOW}${BOLD}  Installation complete. ($PASS/$TOTAL servers running)${NC}"
    echo -e "  Check logs: ls $PID_DIR/*.log"
fi
echo -e "${BOLD}${CYAN}══════════════════════════════════════════${NC}"
echo ""
echo -e "${BOLD}Next Steps:${NC}"
echo "  1. Open VS Code in this project directory:"
echo "       code $SCRIPT_DIR"
echo ""
echo "  2. Open Copilot Chat (Ctrl+Shift+I / Cmd+Shift+I)"
echo "     Select 'Code Hacker' from the agent dropdown"
echo ""
echo "  3. MCP Server Endpoints:"
for entry in "${SERVERS[@]}"; do
    IFS=':' read -r script port name <<< "$entry"
    echo "       $name: http://localhost:$port/mcp"
done
echo ""
echo -e "${BOLD}Management:${NC}"
echo "  Stop servers:    bash $SCRIPT_DIR/start_servers.sh stop"
echo "  Restart servers: bash $SCRIPT_DIR/start_servers.sh restart"
echo "  Server status:   bash $SCRIPT_DIR/start_servers.sh status"
echo ""
