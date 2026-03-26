---
name: mcp-server
description: Create, modify, or debug MCP servers in the Code Hacker toolchain
user_invocable: true
---

# MCP Server Management Skill

Guide for creating, modifying, or debugging MCP servers in the Code Hacker project.

## Architecture Overview

Code Hacker uses 7 MCP servers, all using `FastMCP` with streamable-http transport:

| Server | File | Port | Tools |
|--------|------|------|-------|
| filesystem-command | `filesystem.py` | 8001 | 12 |
| git-tools | `git_tools.py` | 8002 | 11 |
| code-intel | `code_intel.py` | 8003 | 5 |
| memory-store | `memory_store.py` | 8004 | 7+ |
| code-review | `code_review.py` | 8005 | 8 |
| code-refactor | `code_refactor.py` | 8006 | 4 |
| multi-project | `multi_project.py` | 8007 | 15 |

## Adding a New Tool to an Existing Server

1. Open the server `.py` file (e.g., `code_intel.py`)
2. Add a new function decorated with `@mcp.tool()`:
   ```python
   @mcp.tool()
   async def my_new_tool(param1: str, param2: int = 10) -> str:
       """Tool description shown to the LLM."""
       # Implementation
       return json.dumps(result)
   ```
3. Update these files to document the new tool:
   - `code-hacker.agent.md` — Add to the relevant section
   - `README.md` — Add to the tool list table
   - `web_app.py` SYSTEM_PROMPT — Add tool name to the listing
   - `tui_app.py` TOOL_ICONS — Add display mapping
   - `web_app.py` `get_tool_display()` — Add display mapping

## Adding a New MCP Server

1. Create `new_server.py` using the FastMCP pattern:
   ```python
   from mcp.server.fastmcp import FastMCP
   mcp = FastMCP("new-server-name")

   @mcp.tool()
   async def my_tool(...) -> str:
       ...

   if __name__ == "__main__":
       mcp.run(transport="streamable-http", host="0.0.0.0", port=800X)
   ```
2. Update `start_servers.sh` — Add to the `SERVERS` array
3. Update `web_app.py` — Add to `MCP_SERVERS` dict
4. Update `tui_app.py` — Add to `MCP_SERVERS` dict
5. Update `code-hacker.agent.md` — Add `"new-server-name/*"` to tools list
6. Update `.vscode/mcp.json` — Add server URL
7. Update `README.md` — Document the new server and its tools

## Debugging Servers

```bash
# Check all server status
bash start_servers.sh status

# View a specific server's log
cat .mcp_pids/filesystem.log

# Restart all servers
bash start_servers.sh restart

# Test a specific server endpoint
curl -s http://localhost:8001/mcp -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "id": 1}'
```

## Important Constraints

- **Port range**: 8001–8007 are reserved. New servers use 8008+.
- **Tool names are API**: Renaming breaks `code-hacker.agent.md`, `subagents.yaml`, `web_app.py`, `tui_app.py`, and tests.
- **Security**: Each server must implement its own security checks (path validation, command blocklists, etc.).
- **Proxy bypass**: Clients must set `NO_PROXY=localhost,127.0.0.1` to avoid proxy interference.
