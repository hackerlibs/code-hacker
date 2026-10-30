#!/bin/bash
#
# Install / uninstall the macOS LaunchAgent for Code Hack MCP servers autostart.
#
# Usage:
#   bash scripts/install_autostart.sh install    # install + load (default)
#   bash scripts/install_autostart.sh uninstall  # unload + remove
#   bash scripts/install_autostart.sh status     # show launchd job status
#
# What it does:
#   1. Renders scripts/com.codehacker.mcp-servers.plist with the real
#      project path (replacing __PROJECT_DIR__).
#   2. Copies it to ~/Library/LaunchAgents/com.codehacker.mcp-servers.plist
#   3. Registers with launchd (launchctl bootstrap / bootout).
#
# After install, all 7 MCP servers start automatically at login.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.codehacker.mcp-servers"
PLIST_TEMPLATE="$SCRIPT_DIR/scripts/${LABEL}.plist"
PLIST_INSTALL="$HOME/Library/LaunchAgents/${LABEL}.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

render_plist() {
    [ -f "$PLIST_TEMPLATE" ] || { echo "ERROR: template not found: $PLIST_TEMPLATE"; exit 1; }
    sed "s|__PROJECT_DIR__|$SCRIPT_DIR|g" "$PLIST_TEMPLATE"
}

uid() {
    # Current user id (for launchctl bootstrap gui/UID)
    id -u
}

uninstall() {
    echo "=== Uninstalling LaunchAgent: $LABEL ==="
    if [ -f "$PLIST_INSTALL" ]; then
        # Stop all servers via the project script first
        bash "$SCRIPT_DIR/start_servers.sh" stop 2>/dev/null || true
        launchctl bootout "gui/$(uid)/$LABEL" 2>/dev/null || \
            launchctl unload "$PLIST_INSTALL" 2>/dev/null || true
        rm -f "$PLIST_INSTALL"
        echo "  [OK] unloaded and removed: $PLIST_INSTALL"
    else
        echo "  [SKIP] not installed"
    fi
}

install_agent() {
    echo "=== Installing LaunchAgent: $LABEL ==="
    echo "  Project dir: $SCRIPT_DIR"

    # Re-render every time so path stays in sync after repo moves
    mkdir -p "$LAUNCH_AGENTS_DIR"
    render_plist > "$PLIST_INSTALL"

    # Unload old copy if present, then register the fresh one
    launchctl bootout "gui/$(uid)/$LABEL" 2>/dev/null || \
        launchctl unload "$PLIST_INSTALL" 2>/dev/null || true

    # Modern API (macOS >= 10.11); fall back to legacy load
    if launchctl bootstrap "gui/$(uid)" "$PLIST_INSTALL" 2>/dev/null; then
        echo "  [OK] registered via launchctl bootstrap"
    else
        launchctl load "$PLIST_INSTALL"
        echo "  [OK] registered via launchctl load (legacy)"
    fi

    # Kick an immediate start (RunAtLoad only triggers at login)
    launchctl kickstart "gui/$(uid)/$LABEL" 2>/dev/null || true

    echo ""
    echo "  Installed: $PLIST_INSTALL"
    echo "  Servers will auto-start at every login."
    echo "  Check server status: bash start_servers.sh status"
}

status_agent() {
    echo "=== LaunchAgent status: $LABEL ==="
    if [ -f "$PLIST_INSTALL" ]; then
        echo "  Installed: $PLIST_INSTALL"
    else
        echo "  Not installed. Run: bash scripts/install_autostart.sh install"
        exit 0
    fi
    if launchctl print "gui/$(uid)/$LABEL" &>/dev/null; then
        echo "  launchd job: loaded"
    else
        echo "  launchd job: not loaded"
    fi
    echo ""
    bash "$SCRIPT_DIR/start_servers.sh" status
}

case "${1:-install}" in
    install)   install_agent ;;
    uninstall) uninstall ;;
    status)    status_agent ;;
    *)
        echo "Usage: $0 {install|uninstall|status}"
        exit 1
        ;;
esac