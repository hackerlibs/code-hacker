---
name: write-mcp
description: Scaffold a new MCP server from scratch using FastMCP — generate the server, well-designed tools, validation, and client wiring. Use when the user says "write an MCP", "make an MCP for X", "create a tool server", or wants to expose some capability to an LLM.
user_invocable: true
---

# Write an MCP Server (from scratch)

This skill scaffolds a working MCP server using the **FastMCP** pattern, following the
design principles in `docs/how-to-write-an-mcp.md`. The goal: the user describes a
capability ("query weather", "search our DB", "call our internal API") and you produce
a runnable MCP server with well-designed tools.

Companion article: `docs/how-to-write-an-mcp.md`.

## The one mental model

An MCP server is just **a set of `@mcp.tool()` functions**. Writing an MCP = writing tools.
A good tool gives the model, in **one call**, what it would otherwise need 5 round-trips to
assemble. *Precise tool = precise context.*

## The 3-part skeleton (every MCP server is this)

```python
from mcp.server.fastmcp import FastMCP

# ① a server with a name + a free port
mcp = FastMCP(name="<server-name>", host="localhost", port=<PORT>)

# ② one function per tool
@mcp.tool()
async def <verb_noun>(arg: str, opt: int = 10) -> str:
    """One-line: what this tool does (written FOR THE MODEL, not humans).

    Args:
        arg: what it is, with an example
        opt: what it controls (default: 10)
    """
    # validate → act → return self-describing string
    return f"...result with context..."

# ③ run it
if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

## Steps to follow when asked to write an MCP

1. **Clarify the capability and split into verbs.** Don't make one god-tool that takes a
   `subcommand` string. One tool = one core verb (see `git_tools.py`: `git_status`,
   `git_diff`, `git_log` — not a single `git(cmd)`).

2. **Pick a non-conflicting port.** In *this* repo, 8001–8005 and 8007 are taken; use 8008+.
   For a standalone project, anything free (e.g. 8010).

3. **Write each tool to these rules** (the body-memory checklist):
   - **docstring is a prompt for the model** — first line is one clear verb; document every `Arg`.
   - **type signature is the contract** — descriptive param names (`repo_path`, not `p`),
     sensible defaults (`= "."`).
   - **return value is the next prompt** — include identifying context, not just raw data.
     E.g. `return f"File: {path}\nSize: {n} chars\n\n{content}"`, not `return content`.
   - **install guardrails before acting** — for anything that changes the world (write/exec/
     delete), validate first and `return "Error: ..."` (a sentence the model can recover from)
     instead of raising. Mirror `filesystem.py`: `is_safe_path`, `is_allowed_file`, size caps,
     blocked-command sets.

4. **Minimum viable first.** Generate a tool that returns stub data so the link can be tested,
   then swap in the real API/DB/filesystem call.

5. **Give the client wiring + run command.** Provide the `mcp.json` snippet and how to verify:
   ```json
   { "mcpServers": { "<server-name>": { "url": "http://localhost:<PORT>/mcp" } } }
   ```
   ```bash
   pip install "mcp[cli]"   # or: uv add "mcp[cli]"
   python <file>.py         # then ask the client to use the new tool
   ```

6. **If this is for the code-hacker repo specifically**, also follow `mcp-server.md` (register
   in `start_servers.sh`, `web_app.py`, `tui_app.py`, `.vscode/mcp.json`, `code-hacker.agent.md`).

## Minimal template to emit

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(name="weather", host="localhost", port=8010)

@mcp.tool()
async def get_weather(city: str) -> str:
    """Get the current weather for a city.

    Args:
        city: City name, e.g. "Beijing"
    """
    if not city.strip():
        return "Error: city must not be empty."
    # TODO: replace stub with a real API call
    return f"Weather in {city}: Sunny, 23°C, humidity 40%."

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

## Anti-patterns to refuse / fix

- ❌ A single tool with a free-form `command`/`subcommand` arg → the schema tells the model nothing.
- ❌ Returning bare data with no context → the model loses track across turns.
- ❌ Raising exceptions on bad input → crashes the call; return an error sentence instead.
- ❌ Building the "complete" server first → start with one stub tool, verify the loop, then grow.
