#!/bin/bash
#
# Start all Code Hack AI Expert MCP servers.
#
# Usage:
#   bash start_servers.sh          # start all
#   bash start_servers.sh stop     # stop all
#   bash start_servers.sh status   # check status
#

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MCP_DIR="$SCRIPT_DIR"
PID_DIR="$SCRIPT_DIR/.mcp_pids"

SERVERS=(
    "filesystem.py:8001"
    "git_tools.py:8002"
    "code_intel.py:8003"
    "memory_store.py:8004"
    "code_review.py:8005"
    "multi_project.py:8007"
)

start_servers() {
    mkdir -p "$PID_DIR"
    echo "=== Starting MCP Servers ==="
    echo ""

    for entry in "${SERVERS[@]}"; do
        IFS=':' read -r script port <<< "$entry"
        name="${script%.py}"
        pid_file="$PID_DIR/$name.pid"

        # Check if already running
        if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
            echo "  [SKIP] $name (port $port) — already running (PID $(cat "$pid_file"))"
            continue
        fi

        echo -n "  Starting $name on port $port ... "
        cd "$MCP_DIR" && python "$script" > "$PID_DIR/$name.log" 2>&1 &
        local pid=$!
        echo "$pid" > "$pid_file"
        sleep 0.3

        if kill -0 "$pid" 2>/dev/null; then
            echo "OK (PID $pid)"
        else
            echo "FAILED (check $PID_DIR/$name.log)"
        fi
    done

    echo ""
    echo "All servers started. Logs in: $PID_DIR/*.log"
    echo ""
    echo "MCP Server Endpoints:"
    for entry in "${SERVERS[@]}"; do
        IFS=':' read -r script port <<< "$entry"
        name="${script%.py}"
        echo "  $name: http://localhost:$port/mcp"
    done
    echo ""
    echo "Next: Run 'uv run python web_app.py' to start the web interface."
}

stop_servers() {
    echo "=== Stopping MCP Servers ==="
    echo ""

    for entry in "${SERVERS[@]}"; do
        IFS=':' read -r script port <<< "$entry"
        name="${script%.py}"
        pid_file="$PID_DIR/$name.pid"

        if [ -f "$pid_file" ]; then
            local pid=$(cat "$pid_file")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid"
                echo "  [STOP] $name (PID $pid)"
            else
                echo "  [SKIP] $name — not running"
            fi
            rm -f "$pid_file"
        else
            echo "  [SKIP] $name — no PID file"
        fi
    done

    echo ""
    echo "All servers stopped."
}

status_servers() {
    echo "=== MCP Server Status ==="
    echo ""

    for entry in "${SERVERS[@]}"; do
        IFS=':' read -r script port <<< "$entry"
        name="${script%.py}"
        pid_file="$PID_DIR/$name.pid"

        if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
            echo "  [UP]   $name — port $port — PID $(cat "$pid_file")"
        else
            echo "  [DOWN] $name — port $port"
        fi
    done
}

case "${1:-start}" in
    start)  start_servers ;;
    stop)   stop_servers ;;
    status) status_servers ;;
    restart)
        stop_servers
        sleep 1
        start_servers
        ;;
    *)
        echo "Usage: $0 {start|stop|status|restart}"
        exit 1
        ;;
esac
