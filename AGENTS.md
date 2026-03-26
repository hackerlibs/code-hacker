# AGENTS.md — How to work in this repo (for coding agents + humans)

This file is the repo's **always-on working agreement**. If you are an agent, follow it by default unless a task explicitly overrides it.

## North Star

- **Make small, reviewable changes.** Prefer tiny diffs that are easy to reason about.
- **Keep workflows repeatable.** If a step matters, document it or script it.
- **Don't leak secrets.** Never commit credentials, tokens, API keys, or `.env` files.

## Read Before You Act

Before changing anything, locate the source of truth:

1. **Docs / run commands**
   - `README.md`
   - `start_servers.sh` (server lifecycle management)
2. **Build configuration**
   - `pyproject.toml` (Python deps + build config)
   - `subagents.yaml` (DeepAgent subagent definitions)
3. **Agent definitions**
   - `code-hacker.agent.md` (VS Code Custom Agent system prompt + tool bindings)

If a task involves server ports, MCP endpoints, or tool names, **read the relevant source file first** and avoid "guess changes".

## Repo Map (high level)

This section is a convenience only. If it gets out of date, trust the repo tree.

- `filesystem.py` — MCP Server 1: File CRUD, search, command exec (port 8001)
- `git_tools.py` — MCP Server 2: Git operations (port 8002)
- `code_intel.py` — MCP Server 3: AST analysis, symbols, dependency graph (port 8003)
- `memory_store.py` — MCP Server 4: Persistent memory + scratchpad + QA experience (port 8004)
- `code_review.py` — MCP Server 5: Code quality review + health score (port 8005)
- `code_refactor.py` — MCP Server 6: Auto refactoring + ydiff structural diff (port 8006)
- `multi_project.py` — MCP Server 7: Multi-project workspace (port 8007)
- `lib/` — Internal engines (ydiff AST diff, refactor auto-split)
- `web_app.py` — DeepAgent web interface (FastAPI + WebSocket)
- `tui_app.py` — Claude Code-style TUI (Rich + prompt_toolkit)
- `subagents.yaml` — 4 subagent definitions
- `static/` — Hacker-style web terminal UI
- `tests/` — LLM-powered integration test scenarios
- `start_servers.sh` — Start/stop/status/restart all 7 MCP servers
- `codehacker/` — Package directory

## Architecture Constraints

- **Three Frontends, One Backend** — VS Code, web_app.py, and tui_app.py all share the same 7 MCP servers. Changes to a server affect all three frontends.
- **Streamable HTTP transport** — All MCP servers use `streamable-http` on ports 8001–8007. Do not change transport type or port assignments without updating all consumers.
- **Security sandbox** — Each server has independent security policies (path checks, command blocklists, file whitelists). Respect these boundaries.
- **Tool names are API** — Tool names (e.g., `edit_file`, `git_status`, `workspace_search`) are referenced in `code-hacker.agent.md`, `web_app.py`, `tui_app.py`, `subagents.yaml`, and tests. Renaming a tool is a breaking change.

## Default Workflow Expectations

- Use `bash start_servers.sh` for all server lifecycle management.
- Use `uv sync` to install dependencies, `uv run python ...` to run.
- When adding a new MCP tool, register it in the relevant server file, then update `code-hacker.agent.md`'s tool listing and `README.md`.
- When adding a new MCP server, update `start_servers.sh`, `MCP_SERVERS` dict in `web_app.py` and `tui_app.py`, VS Code `mcp.json`, and `code-hacker.agent.md`.

## Hard Rules (agents)

- **No dependency upgrades**, formatting sweeps, or refactor-only changes unless explicitly requested.
- Avoid touching unrelated files "while you're there". If you must, explain why.
- **Never change port assignments** (8001–8007) without explicit approval — they're hardcoded in multiple places.
- **Never modify security policies** (command blocklists, path checks) without explicit approval.

## Change Hygiene

- **One concern per PR/commit** where possible (server-only, frontend-only, test-only).
- Avoid drive-by refactors. If you touch unrelated code, explain why.
- Always include:
  - what problem you're solving
  - what changed
  - how to verify

## Verification (minimum bar)

For any change that affects MCP servers or agent behavior:

1. Start all servers: `bash start_servers.sh`
2. Check status: `bash start_servers.sh status` — all 7 should be `[UP]`
3. Run tests if applicable:
   ```bash
   NO_PROXY=localhost,127.0.0.1 uv run pytest tests/test_scenarios.py -v -s
   ```

If tests don't exist for your change, write "How to verify" instead.

## Dependency Policy

- Don't add dependencies unless necessary.
- Prefer small, well-supported libraries.
- All deps go in `pyproject.toml` — use `uv sync` after changes.

## Documentation Standards

- README must not contain credentials or shared test accounts.
- Keep docs concise and actionable: requirements → commands → troubleshooting.

## Communication / Outputs

When responding in PRs/issues or proposing changes:

- Prefer bullet points.
- Include exact commands and file paths.
- End with a short **How to verify** section.

## When in Doubt

- Ask for clarification on expected behavior and supported environments.
- Default to the smallest safe improvement that's easy to review.
