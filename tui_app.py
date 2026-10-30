#!/usr/bin/env python3
"""
Code Hack AI Expert — Claude Code-style TUI Interface

A terminal UI that connects to 7 MCP servers and provides an interactive
code editing experience similar to Claude Code.

Usage:
    # 1. Start all MCP servers:
    bash start_servers.sh

    # 2. Start the TUI (optionally with a working directory):
    uv run python tui_app.py            # current directory is the workdir
    uv run python tui_app.py /path/to/project

    # 3. Sessions (persistent conversation history, like Claude Code):
    uv run python tui_app.py --continue            # resume the most recent session
    uv run python tui_app.py --resume <uuid>       # resume a specific session
    uv run python tui_app.py                        # start a new session (uuid)
    # In-session: /sessions lists past sessions, /quit shows the session id.
"""

import argparse
import asyncio
import json
import os
import sys
import signal
import socket
import uuid as uuid_mod
from urllib.parse import urlparse
import warnings
from datetime import datetime
from pathlib import Path
from contextlib import AsyncExitStack

os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1")
warnings.filterwarnings("ignore", message="Core Pydantic V1 functionality")

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule
from rich.table import Table
from rich.columns import Columns
from rich.syntax import Syntax
from rich.style import Style
from rich.theme import Theme

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.history import InMemoryHistory

from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from langchain_mcp_adapters.tools import load_mcp_tools

import yaml
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langgraph.errors import GraphRecursionError

# ─── Constants ────────────────────────────────────────────────────────────

EXPERT_DIR = Path(__file__).parent

# Working directory: set via CLI argument. FilesystemBackend root, relative
# paths of built-in tools (ls/glob/grep), and ./AGENTS.md memory all resolve
# against this directory. Defaults to the process CWD at startup.
WORK_DIR = Path.cwd().resolve()

# Session checkpoint database (conversation persistence, like Claude Code).
SESSIONS_DIR = Path(os.environ.get("TUI_SESSIONS_DIR", "./.sessions"))

# Agent step budget. deepagents hard-binds recursion_limit=1000 via
# .with_config(); the runtime config passed to astream/ainvoke overrides it
# (verified: subagents inherit the runtime value too).
RECURSION_LIMIT = int(os.environ.get("DEEPAGENT_RECURSION_LIMIT", "10000"))

MCP_SERVERS = {
    "filesystem-command": "http://localhost:8001/mcp",
    "git-tools":          "http://localhost:8002/mcp",
    "code-intel":         "http://localhost:8003/mcp",
    "memory-store":       "http://localhost:8004/mcp",
    "code-review":        "http://localhost:8005/mcp",
    "multi-project":      "http://localhost:8007/mcp",
    "mermaid-chart":      "http://localhost:8008/mcp",
}

THEME = Theme({
    "info":       "dim cyan",
    "warning":    "bold yellow",
    "error":      "bold red",
    "success":    "bold green",
    "tool":       "yellow",
    "tool.done":  "green",
    "tool.fail":  "red",
    "header":     "bold green",
    "prompt":     "bold cyan",
    "dim":        "dim white",
    "accent":     "bold magenta",
})

console = Console(theme=THEME)

def _port_open(url: str, timeout: float = 1.0) -> bool:
    """Quick TCP probe so a down server fails fast instead of hanging initialize()."""
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname, u.port), timeout=timeout):
            return True
    except OSError:
        return False


# ─── Workdir-aware MCP tool interception ──────────────────────────────────
#
# MCP servers run as separate processes started by start_servers.sh with their
# CWD fixed to this repo. os.chdir(workdir) in the TUI process cannot reach
# them, so relative path args ("." / "src/foo.py") sent to MCP tools would be
# resolved against the *server's* CWD, not the user's workdir.
#
# This interceptor rewrites relative path arguments to workdir-anchored
# absolute paths before each MCP tool call, so the agent's relative paths
# always mean "inside the workdir".

# tool name -> {arg name: has "."-default}.
# Only args that denote a path *relative to the working root* are listed.
# Args like git's file_path (relative to the repo) or workspace_* file_path
# (relative to a registered project) keep their own semantics and are NOT
# rewritten. The boolean marks args whose server-side default is "." —
# when such an arg is omitted, the workdir is injected explicitly.
TOOL_PATH_ARGS: dict[str, dict[str, bool]] = {
    # filesystem-command
    "read_file": {"file_path": False},
    "read_file_lines": {"file_path": False},
    "write_file": {"file_path": False},
    "append_file": {"file_path": False},
    "edit_file": {"file_path": False},
    "list_directory": {"directory_path": True},
    "get_file_info": {"file_path": False},
    "find_files": {"directory": True},
    "search_files_ag": {"search_path": True},
    "create_directory": {"directory_path": False},
    "execute_command": {"working_directory": True},
    # git-tools (repo_path default ".")
    "git_status": {"repo_path": True},
    "git_diff": {"repo_path": True},
    "git_log": {"repo_path": True},
    "git_show": {"repo_path": True},
    "git_branch": {"repo_path": True},
    "git_add": {"repo_path": True},
    "git_commit": {"repo_path": True},
    "git_checkout": {"repo_path": True},
    "git_create_branch": {"repo_path": True},
    "git_stash": {"repo_path": True},
    "git_blame": {"repo_path": True},
    # code-intel
    "analyze_python_file": {"file_path": False},
    "extract_symbols": {"file_path": False},
    "dependency_graph": {"file_path": False},
    "project_overview": {"directory": True},
    "find_references": {"directory": True},
    # code-review
    "review_file": {"file_path": False},
    "review_function": {"file_path": False},
    "ydiff_files": {"file_path1": False, "file_path2": False},
    "review_project": {"project_dir": False},
    "health_score": {"project_dir": False},
    "find_long_functions": {"project_dir": False},
    "find_complex_functions": {"project_dir": False},
    "suggest_reorg": {"project_dir": False},
    "ydiff_commit": {"project_dir": False},
    "ydiff_git_changes": {"project_dir": False},
    # multi-project
    "workspace_add": {"project_path": False},
}


def _resolve_path_arg(value, workdir: Path):
    """Rewrite a relative path arg into a workdir-anchored absolute path.

    Absolute paths, empty values and non-str values pass through untouched.
    """
    if not isinstance(value, str) or not value.strip():
        return value
    p = Path(value).expanduser()
    if p.is_absolute():
        # canonicalize (~ expansion included) so the server never sees "~"
        return str(p)
    # Path("."), "src/foo.py", "…" all resolve under the workdir.
    return str((workdir / p).resolve())


class WorkdirPathInterceptor:
    """MCP tool-call interceptor that anchors relative paths to the workdir.

    Installed via load_mcp_tools(tool_interceptors=[...]); rewrites the
    request args for tools listed in TOOL_PATH_ARGS, then delegates to the
    real handler. get_current_directory has no args to rewrite, so it is
    short-circuited to answer with the workdir directly.
    """

    def __init__(self, workdir: Path):
        self.workdir = workdir

    async def __call__(self, request, handler):
        tool = request.name
        args = dict(request.args or {})

        if tool == "get_current_directory":
            from mcp.types import CallToolResult, TextContent
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Current working directory: {self.workdir}",
                )]
            )

        path_params = TOOL_PATH_ARGS.get(tool)
        if path_params:
            changed = False
            for param, has_dot_default in path_params.items():
                if param in args:
                    new_val = _resolve_path_arg(args[param], self.workdir)
                    if new_val != args[param]:
                        args[param] = new_val
                        changed = True
                elif has_dot_default:
                    # Arg omitted, server default is ".": that would resolve
                    # against the *server's* CWD. Inject the workdir so the
                    # default behaves as the user's current directory.
                    args[param] = str(self.workdir)
                    changed = True
            if changed:
                request = request.override(args=args)

        return await handler(request)


# ─── Reuse models and config from web_app ─────────────────────────────────

def get_llm_model() -> ChatOpenAI:
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    model_name = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250514")
    if not api_key:
        raise ValueError("Please set OPENROUTER_API_KEY or OPENAI_API_KEY")
    return ChatOpenAI(model=model_name, base_url=base_url, api_key=api_key, max_tokens=16000)


def get_subagent_model() -> ChatOpenAI:
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    model_name = os.environ.get("LLM_SUBAGENT_MODEL", "anthropic/claude-haiku-4-5-20251001")
    return ChatOpenAI(model=model_name, base_url=base_url, api_key=api_key, max_tokens=8000)


def load_subagents(config_path: Path, mcp_tools: list) -> list:
    if not config_path.exists():
        return []
    tool_lookup = {t.name: t for t in mcp_tools}
    with open(config_path) as f:
        config = yaml.safe_load(f)
    subagent_model = get_subagent_model()
    subagents = []
    for name, spec in config.items():
        sa = {
            "name": name,
            "description": spec["description"],
            "system_prompt": spec["system_prompt"],
            "model": subagent_model,
        }
        if "tools" in spec:
            sa["tools"] = [tool_lookup[t] for t in spec["tools"] if t in tool_lookup]
        subagents.append(sa)
    return subagents


# ─── System Prompt (same as web_app) ──────────────────────────────────────

SYSTEM_PROMPT = """\
You are the **Code Hack AI Expert** — a full-featured multi-project programming agent.

## Your Toolset (8 MCP Servers, 71+ tools)

### 1. Filesystem (filesystem-command)
- `read_file` / `read_file_lines` / `write_file` / `append_file` / `edit_file`
- `find_files` / `search_files_ag` / `list_directory` / `get_file_info`
- `execute_command` / `create_directory` / `get_current_directory`

### 2. Git Operations (git-tools)
- `git_status` / `git_diff` / `git_log` / `git_show`
- `git_add` / `git_commit` / `git_branch` / `git_create_branch` / `git_checkout`
- `git_stash` / `git_blame`

### 3. Code Intelligence (code-intel)
- `analyze_python_file` / `extract_symbols` / `project_overview`
- `find_references` / `dependency_graph`

### 4. Persistent Memory (memory-store) — CozoDB-backed reusable experience knowledge base
- `memory_save(title, category, solution, problem, context, pattern, tags)` — save a reusable experience
- `memory_get(id)` — fetch by id (also bumps the usage counter)
- `memory_search(query, category, tag, limit)` — full-text search with combinable filters
- `memory_list(category, limit)` / `memory_delete(id)` / `memory_categories()` / `memory_top_used(limit)`
- Category-scoped finders: `find_email_template(query, to_customer)`, `find_jira_template`, `find_bugfix`, `find_pipeline`, `find_devops_lib`, `find_ai_knowledge`
- `qa_experience_save / qa_experience_search / qa_experience_get` — back-compat wrappers (stored under `qa_experience`)
- `scratchpad_write / scratchpad_read / scratchpad_append` — short-lived working memory

### 5. Code Review (code-review)
- `review_project` / `review_file` / `review_function` / `health_score`
- `find_long_functions` / `find_complex_functions` / `suggest_reorg` / `review_diff_text`
- `ydiff_files` / `ydiff_commit` / `ydiff_git_changes` — Structural AST-level diff (visual HTML reports)

### 8. Mermaid Chart (mermaid-chart)
- `render_mermaid` — Render Mermaid code to interactive HTML and open in browser
- `flowchart` — Generate flowcharts from structured node data
- `sequence_diagram` — Generate sequence diagrams from interaction data
- `list_charts` / `open_chart` — List and open generated chart files

### 7. Multi-Project Workspace (multi-project)
- `workspace_add` / `workspace_remove` / `workspace_list` / `workspace_overview`
- `workspace_search` / `workspace_find_files` / `workspace_find_dependencies`
- `workspace_read_file` / `workspace_edit_file` / `workspace_write_file`
- `workspace_git_status` / `workspace_git_diff` / `workspace_git_log` / `workspace_commit`
- `workspace_exec`

## Core Working Principles
- Understand first, act second: read files and analyze before modifying
- Prefer `edit_file` for precise replacements over rewriting entire files
- Check `git_status` before modifications
- Safety first: confirm before destructive operations

### Reusable Experience Memory (this is your long-term brain — USE IT)
The `memory-store` server is a CozoDB-backed knowledge base of **what worked before** —
prompts that succeeded, pipeline recipes, customer/internal email templates, JIRA templates,
bug-fix patterns, devops library snippets, full QA dialogues. Don't make the user solve the
same problem twice.

**Recall (BEFORE solving any new task):**
1. Search past experience with keywords from the user's request — `memory_search(query=...)`,
   or better, the category-scoped finder when you know the bucket: `find_pipeline`,
   `find_email_template(..., to_customer=True/False)`, `find_jira_template`, `find_bugfix`,
   `find_devops_lib`, `find_ai_knowledge`.
2. If a relevant hit exists, call `memory_get(<id>)` to read it (this bumps its usage counter so
   workhorses surface higher next time), then **tell the user** you found a prior experience and
   apply the same pattern.

**Save (when the user signals "remember it"):**
Trigger phrases — "记住", "记住它", "帮我记住", "remember this", "save this", "下次也这样做".
After the problem is solved, classify it and call `memory_save` with the right `category`:
- `pipeline` — CI/CD or data pipeline recipes
- `email_customer` / `email_internal` — email templates
- `jira_template` — JIRA / ticket templates
- `bug_fix` — bug-fix recipes
- `devops_lib` — devops/infra library usage notes
- `ai_knowledge` — AI prompts / model usage
- `qa_experience` — successful QA dialogue patterns

Fill `title`, `problem`, `context` (the **key dialogue turns** A→B→C that led to the breakthrough),
`solution` (the concrete answer to paste back next time), `pattern` (the **reusable strategy**, the
most valuable field), and `tags`.

If the user just nailed a non-trivial problem and is clearly satisfied, proactively offer to save it.
Use `scratchpad_*` only for short-lived current-task notes; cross-session knowledge belongs in `memory_save`.

### Two-Phase Commit (Reviewer-Friendly AI Changes)
When changes involve both structural reorganization and logic modifications, **split into two commits**:
1. **Mechanical commit** → add `#not-need-review` (moves, renames, reformats — identity transformation)
2. **Logic commit** → normal commit (behavior changes, bug fixes, new features — reviewer must read)

Reviewers use `git log --grep="#not-need-review" --invert-grep` to skip mechanical changes.

## Working Directory

Your current working directory (workdir) is `{workdir}`. Relative paths you pass to tools
(files, directories, repo paths) are automatically resolved against this directory — e.g.
`"src/foo.py"` means `{workdir}/src/foo.py`, and `"."` means `{workdir}` itself. Absolute
paths pass through unchanged. This is also what `get_current_directory` returns.
"""


# ─── Tool Display ─────────────────────────────────────────────────────────

TOOL_ICONS = {
    # Filesystem
    "read_file": ("Read", "cyan"),
    "read_file_lines": ("Read", "cyan"),
    "write_file": ("Write", "yellow"),
    "edit_file": ("Edit", "yellow"),
    "append_file": ("Write", "yellow"),
    "list_directory": ("List", "cyan"),
    "execute_command": ("Bash", "magenta"),
    "search_files_ag": ("Grep", "cyan"),
    "find_files": ("Glob", "cyan"),
    "create_directory": ("Mkdir", "yellow"),
    "get_file_info": ("Stat", "cyan"),
    "get_current_directory": ("Pwd", "cyan"),
    # Git
    "git_status": ("Git", "green"),
    "git_diff": ("Git", "green"),
    "git_log": ("Git", "green"),
    "git_show": ("Git", "green"),
    "git_add": ("Git", "yellow"),
    "git_commit": ("Git", "yellow"),
    "git_branch": ("Git", "green"),
    "git_create_branch": ("Git", "yellow"),
    "git_checkout": ("Git", "yellow"),
    "git_stash": ("Git", "yellow"),
    "git_blame": ("Git", "green"),
    # Code Intel
    "analyze_python_file": ("Analyze", "magenta"),
    "extract_symbols": ("Symbols", "magenta"),
    "project_overview": ("Overview", "magenta"),
    "find_references": ("Refs", "magenta"),
    "dependency_graph": ("Deps", "magenta"),
    # Memory
    "memory_save": ("Memory+", "blue"),
    "memory_get": ("Memory", "blue"),
    "memory_search": ("MemSearch", "blue"),
    "memory_list": ("Memory", "blue"),
    "memory_delete": ("Memory-", "red"),
    "memory_categories": ("MemStats", "blue"),
    "memory_top_used": ("MemTop", "blue"),
    "find_email_template": ("FindEmail", "blue"),
    "find_jira_template": ("FindJira", "blue"),
    "find_bugfix": ("FindBug", "blue"),
    "find_pipeline": ("FindPipe", "blue"),
    "find_devops_lib": ("FindOps", "blue"),
    "find_ai_knowledge": ("FindAI", "blue"),
    "scratchpad_write": ("Scratch", "blue"),
    "scratchpad_read": ("Scratch", "blue"),
    "scratchpad_append": ("Scratch", "blue"),
    "qa_experience_save": ("QA+", "blue"),
    "qa_experience_search": ("QASearch", "blue"),
    "qa_experience_get": ("QA", "blue"),
    # Code Review
    "review_project": ("Review", "yellow"),
    "review_file": ("Review", "yellow"),
    "review_function": ("Review", "yellow"),
    "health_score": ("Health", "green"),
    "find_long_functions": ("Scan", "yellow"),
    "find_complex_functions": ("Scan", "yellow"),
    "suggest_reorg": ("Reorg", "yellow"),
    "review_diff_text": ("Review", "yellow"),
    # Code Refactor
    "auto_refactor": ("Refactor", "magenta"),
    "ydiff_files": ("YDiff", "cyan"),
    "ydiff_commit": ("YDiff", "cyan"),
    "ydiff_git_changes": ("YDiff", "cyan"),
    # Multi-Project
    "workspace_add": ("WS+", "green"),
    "workspace_remove": ("WS-", "red"),
    "workspace_list": ("WS", "cyan"),
    "workspace_overview": ("WS", "cyan"),
    "workspace_search": ("WSSearch", "cyan"),
    "workspace_find_files": ("WSGlob", "cyan"),
    "workspace_find_dependencies": ("WSDeps", "cyan"),
    "workspace_read_file": ("WSRead", "cyan"),
    "workspace_edit_file": ("WSEdit", "yellow"),
    "workspace_write_file": ("WSWrite", "yellow"),
    "workspace_git_status": ("WSGit", "green"),
    "workspace_git_diff": ("WSGit", "green"),
    "workspace_git_log": ("WSGit", "green"),
    "workspace_commit": ("WSCommit", "yellow"),
    "workspace_exec": ("WSExec", "magenta"),
    # Mermaid Chart
    "render_mermaid": ("Chart", "cyan"),
    "flowchart": ("Flow", "cyan"),
    "sequence_diagram": ("Seq", "cyan"),
    "list_charts": ("Charts", "cyan"),
    "open_chart": ("Open", "cyan"),
    # Subagent
    "task": ("Agent", "bright_magenta"),
}


def format_tool_call(name: str, args: dict) -> Text:
    """Format a tool call in Claude Code style."""
    label, color = TOOL_ICONS.get(name, ("Tool", "yellow"))
    text = Text()
    text.append("  ⏵ ", style="dim")
    text.append(f"{label}", style=f"bold {color}")

    # Show relevant argument
    detail = ""
    if name in ("read_file", "read_file_lines", "write_file", "edit_file",
                 "append_file", "analyze_python_file", "extract_symbols",
                 "dependency_graph", "review_file", "review_function"):
        detail = args.get("file_path", "")
    elif name == "execute_command":
        detail = args.get("command", "")[:80]
    elif name in ("search_files_ag", "workspace_search"):
        detail = args.get("pattern", "")
    elif name in ("find_files", "workspace_find_files"):
        detail = args.get("pattern", "")
    elif name == "list_directory":
        detail = args.get("directory_path", ".")
    elif name.startswith("git_"):
        if "file_path" in args:
            detail = args["file_path"]
        elif "message" in args:
            detail = args["message"][:60]
    elif name == "project_overview":
        detail = args.get("directory", ".")
    elif name == "memory_save":
        cat = args.get("category", "")
        title = args.get("title", "")
        detail = f"[{cat}] {title}" if cat else title
    elif name == "memory_get":
        detail = args.get("id", "")
    elif name == "memory_search":
        cat = args.get("category", "")
        tag = args.get("tag", "")
        q = args.get("query", "")
        suffix = " ".join(x for x in [cat and f"cat={cat}", tag and f"tag={tag}"] if x)
        detail = f"{q} {suffix}".strip()
    elif name == "memory_list":
        detail = args.get("category", "")
    elif name == "memory_delete":
        detail = args.get("id", "")
    elif name == "find_email_template":
        kind = "customer" if args.get("to_customer", True) else "internal"
        detail = f"[{kind}] {args.get('query', '')}"
    elif name in ("find_jira_template", "find_bugfix", "find_pipeline",
                  "find_devops_lib", "find_ai_knowledge"):
        detail = args.get("query", "")
    elif name in ("scratchpad_write", "scratchpad_read", "scratchpad_append"):
        detail = args.get("name", "default")
    elif name == "task":
        detail = args.get("description", "")[:60]
    elif name == "workspace_exec":
        detail = f"[{args.get('project', '')}] {args.get('command', '')[:50]}"
    elif name in ("workspace_read_file", "workspace_edit_file", "workspace_write_file"):
        detail = f"[{args.get('project', '')}] {args.get('file_path', '')}"
    elif name == "workspace_commit":
        detail = args.get("message", "")[:50]
    elif name in ("render_mermaid", "flowchart", "sequence_diagram"):
        detail = args.get("title", "")
    elif name == "open_chart":
        detail = args.get("file_path", "")
    elif name == "auto_refactor":
        detail = args.get("project_dir", "")
    elif name.startswith("ydiff_"):
        detail = args.get("commit_id", args.get("file_path", ""))
    elif name.startswith("qa_experience_"):
        detail = args.get("title", args.get("query", ""))

    if detail:
        # Shorten long paths
        if "/" in detail and len(detail) > 50:
            detail = "…/" + detail.rsplit("/", 1)[-1]
        text.append(f" {detail}", style="dim")

    return text


def format_tool_result(name: str, success: bool) -> Text:
    """Format a tool result line."""
    label, color = TOOL_ICONS.get(name, ("Tool", "yellow"))
    text = Text()
    if success:
        text.append("  ✓ ", style="bold green")
        text.append(f"{label}", style=f"green")
    else:
        text.append("  ✗ ", style="bold red")
        text.append(f"{label}", style=f"red")
        text.append(" failed", style="dim red")
    return text


# ─── Content Helpers ──────────────────────────────────────────────────────

def get_content_as_string(content) -> str:
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text", ""))
        return "\n".join(parts)
    return str(content)


# ─── Session Persistence ─────────────────────────────────────────────────

def _session_db_path() -> Path:
    """Sessions DB lives under the working directory (./.sessions/)."""
    db = (WORK_DIR / SESSIONS_DIR / "tui_sessions.sqlite").resolve()
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


def _list_saved_sessions(limit: int = 20) -> list:
    """List saved sessions (sync, for the /sessions command).

    Returns a list of (session_id, updated_at, message_count, preview) tuples,
    newest first.
    """
    import sqlite3
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    db_path = _session_db_path()
    if not db_path.exists():
        return []

    serde = JsonPlusSerializer()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        # checkpoint ids are time-ordered uuid6, so MAX() = most recent.
        # A brand-new DB has no tables until the first agent run creates
        # them (saver.setup() is lazy) — treat that as "no sessions".
        try:
            latest = conn.execute(
                """
                SELECT thread_id, MAX(checkpoint_id) AS latest
                FROM checkpoints
                WHERE checkpoint_ns = ''
                GROUP BY thread_id
                ORDER BY latest DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        if not latest:
            return []
        rows = []
        for thread_id, latest_id in latest:
            row = conn.execute(
                """
                SELECT type, checkpoint FROM checkpoints
                WHERE thread_id = ? AND checkpoint_ns = '' AND checkpoint_id = ?
                """,
                (thread_id, latest_id),
            ).fetchone()
            if row:
                rows.append((thread_id, *row))
    finally:
        conn.close()

    entries = []
    for thread_id, ser_type, checkpoint_blob in rows:
        try:
            checkpoint = serde.loads_typed((ser_type, checkpoint_blob))
            messages = checkpoint.get("channel_values", {}).get("messages", [])
            n_msgs = len(messages)
            ts = checkpoint.get("ts")
            updated = datetime.fromisoformat(ts) if ts else None
            first_human = next(
                (m for m in messages if isinstance(m, HumanMessage)), None
            )
            preview = first_human.content if first_human else ""
            if isinstance(preview, list):
                preview = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in preview
                )
            preview = str(preview).replace("\n", " ").strip()[:60]
        except Exception:
            n_msgs, updated, preview = 0, None, "(unreadable)"
        entries.append((thread_id, updated, n_msgs, preview))
    return entries


def _find_most_recent_session() -> str | None:
    """Return the most recently updated session id, or None."""
    entries = _list_saved_sessions(limit=1)
    return entries[0][0] if entries else None


# ─── TUI Application ─────────────────────────────────────────────────────

class CodeHackTUI:
    def __init__(self, work_dir: Path, session_id: str, checkpointer=None, resume: bool = False):
        self.agent = None
        self._exit_stack = None
        self.work_dir = work_dir
        self.session_id = session_id
        self.checkpointer = checkpointer
        self.resume = resume
        self.history = InMemoryHistory()
        self.running = True

    def print_header(self):
        """Print the startup banner."""
        banner = Text()
        banner.append("╔══════════════════════════════════════════════════════════════╗\n", style="green")
        banner.append("║", style="green")
        banner.append("            CODE::HACK  AI Expert  —  TUI Mode              ", style="bold green")
        banner.append("║\n", style="green")
        banner.append("║", style="green")
        banner.append("     8 MCP Servers  ·  71+ Tools  ·  4 Subagents            ", style="dim green")
        banner.append("║\n", style="green")
        banner.append("╚══════════════════════════════════════════════════════════════╝", style="green")
        console.print(banner)
        console.print()

    def print_server_status(self, name: str, count: int, ok: bool):
        """Print server connection status line."""
        if ok:
            console.print(f"  [green]●[/green] [bold]{name}[/bold] [dim]— {count} tools[/dim]")
        else:
            console.print(f"  [red]●[/red] [bold]{name}[/bold] [dim red]— connection failed[/dim red]")

    async def init_agent(self):
        """Initialize MCP connections and create the DeepAgent."""
        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()

        model = get_llm_model()
        all_tools = []

        console.print()
        console.print("  [dim]Connecting to MCP servers...[/dim]")
        console.print()

        for server_name, url in MCP_SERVERS.items():
            if not _port_open(url):
                self.print_server_status(server_name, 0, False)
                continue
            local = AsyncExitStack()
            try:
                async with asyncio.timeout(15):
                    transport = await local.enter_async_context(streamablehttp_client(url))
                    read_stream, write_stream, _ = transport
                    session = await local.enter_async_context(
                        ClientSession(read_stream, write_stream)
                    )
                    await session.initialize()
                    tools = await load_mcp_tools(
                        session,
                        server_name=server_name,
                        tool_interceptors=[WorkdirPathInterceptor(self.work_dir)],
                    )
                all_tools.extend(tools)
                # 成功: 把生命周期交给主 stack
                self._exit_stack.push_async_exit(local.pop_all())
                self.print_server_status(server_name, len(tools), True)
            except Exception:
                # 失败: 在同一个 task 内立即清理, 避免 cancel scope 跨 task 报错
                try:
                    await local.aclose()
                except BaseException:
                    pass
                self.print_server_status(server_name, 0, False)

        console.print()
        console.print(f"  [bold green]{len(all_tools)}[/bold green] [dim]tools loaded from[/dim] [bold]{len(MCP_SERVERS)}[/bold] [dim]servers[/dim]")

        # Load subagents
        subagents = load_subagents(EXPERT_DIR / "subagents.yaml", all_tools)
        if subagents:
            names = ", ".join(s["name"] for s in subagents)
            console.print(f"  [bold cyan]{len(subagents)}[/bold cyan] [dim]subagents:[/dim] {names}")

        console.print()

        # Memory: load AGENTS.md from the working directory (silently skipped
        # if missing — MemoryMiddleware treats file_not_found as optional).
        memory_files = ["./AGENTS.md"] if (self.work_dir / "AGENTS.md").exists() else None

        self.agent = create_deep_agent(
            model=model,
            tools=all_tools,
            subagents=subagents,
            memory=memory_files,
            backend=FilesystemBackend(root_dir=self.work_dir),
            # .replace, not .format: the prompt body contains other braces
            # (tool signatures) that must survive untouched.
            system_prompt=SYSTEM_PROMPT.replace("{workdir}", str(self.work_dir)),
            checkpointer=self.checkpointer,
        )

    async def cleanup(self):
        if self._exit_stack:
            await self._exit_stack.aclose()

    async def process_message(self, user_message: str):
        """Stream agent response for a user message."""
        # When resuming a persisted session, the checkpoint already contains
        # prior messages — the first values-chunk replays them all. Start
        # printing from the end of the existing history to skip the replay.
        printed_count = 0
        if self.checkpointer:
            try:
                snapshot = await self.agent.aget_state(
                    {"configurable": {"thread_id": self.session_id}}
                )
                if snapshot and snapshot.values and "messages" in snapshot.values:
                    printed_count = len(snapshot.values["messages"])
            except Exception:
                pass

        pending_tools = {}  # tool_call_id -> name
        accumulated_text = ""

        try:
            async for chunk in self.agent.astream(
                {"messages": [("user", user_message)]},
                config={
                    "configurable": {"thread_id": self.session_id},
                    "recursion_limit": RECURSION_LIMIT,
                },
                stream_mode="values",
            ):
                if "messages" not in chunk:
                    continue

                messages = chunk["messages"]

                for msg in messages[printed_count:]:
                    if isinstance(msg, AIMessage):
                        content = get_content_as_string(msg.content)

                        # Print text content
                        if content and content.strip():
                            # If we had accumulated text, print a separator
                            if accumulated_text:
                                console.print()
                            accumulated_text = content
                            console.print()
                            console.print(Markdown(content))

                        # Print tool calls
                        if msg.tool_calls:
                            for tc in msg.tool_calls:
                                name = tc.get("name", "unknown")
                                args = tc.get("args", {})
                                tc_id = tc.get("id", "")
                                pending_tools[tc_id] = name
                                console.print(format_tool_call(name, args))

                    elif isinstance(msg, ToolMessage):
                        content = get_content_as_string(msg.content)
                        success = not (content and "error" in content.lower()[:100])
                        tool_name = getattr(msg, "name", "")
                        tc_id = getattr(msg, "tool_call_id", "")

                        # Use stored tool name if available
                        if not tool_name and tc_id in pending_tools:
                            tool_name = pending_tools[tc_id]

                        console.print(format_tool_result(tool_name, success))

                printed_count = len(messages)

        except KeyboardInterrupt:
            console.print("\n  [yellow]⚡ Interrupted[/yellow]")
        except GraphRecursionError:
            console.print(
                f"\n  [bold red]Error:[/bold red] Recursion limit of {RECURSION_LIMIT} reached. "
                "The task is too long for one run — try splitting it into smaller steps."
            )
        except Exception as e:
            console.print(f"\n  [bold red]Error:[/bold red] {e}")

        console.print()

    async def run(self):
        """Main TUI loop."""
        self.print_header()

        # Show config info
        model_name = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250514")
        console.print(f"  [dim]Model:[/dim]    {model_name}")
        console.print(f"  [dim]Workdir:[/dim]  {self.work_dir}")
        if self.checkpointer:
            session_state = "[dim]resumed[/dim]" if self.resume else "[dim]new[/dim]"
            console.print(f"  [dim]Session:[/dim]  [bold cyan]{self.session_id}[/bold cyan] [dim]({session_state})[/dim]")

        await self.init_agent()

        # After a resume, show what was restored
        if self.resume and self.checkpointer:
            self._print_restored_history()

        # Print help
        console.print(Rule(style="dim green"))
        console.print("  [dim]Type your message and press Enter. Commands:[/dim]")
        console.print("  [cyan]/help[/cyan]    [dim]— Show help[/dim]")
        console.print("  [cyan]/clear[/cyan]   [dim]— Clear screen[/dim]")
        console.print("  [cyan]/status[/cyan]  [dim]— Show connection status[/dim]")
        console.print("  [cyan]/sessions[/cyan] [dim]— List past sessions[/dim]")
        console.print("  [cyan]/quit[/cyan]    [dim]— Exit (shows session id)[/dim]")
        console.print(Rule(style="dim green"))
        console.print()

        session = PromptSession(history=self.history)

        while self.running:
            try:
                with patch_stdout():
                    user_input = await session.prompt_async(
                        HTML('<style fg="ansibrightcyan" bg="" bold="true">❯ </style>'),
                    )

                user_input = user_input.strip()
                if not user_input:
                    continue

                # Handle slash commands
                if user_input.startswith("/"):
                    cmd = user_input.lower().split()[0]
                    if cmd in ("/quit", "/exit", "/q"):
                        self._print_farewell()
                        break
                    elif cmd == "/clear":
                        console.clear()
                        self.print_header()
                        continue
                    elif cmd == "/help":
                        self._print_help()
                        continue
                    elif cmd == "/status":
                        self._print_status()
                        continue
                    elif cmd == "/sessions":
                        self._print_sessions()
                        continue
                    elif cmd == "/resume":
                        self._print_sessions()
                        console.print("  [dim]To resume a session, restart with:[/dim]")
                        console.print("  [cyan]--resume <session-id>[/cyan] [dim]or[/dim] [cyan]--continue[/cyan]")
                        continue
                    # Otherwise treat as regular message (e.g. /review could be a prompt)

                # Show user message
                console.print()
                user_text = Text()
                user_text.append("  ❯ ", style="bold cyan")
                user_text.append(user_input, style="white")
                console.print(user_text)

                # Process
                await self.process_message(user_input)

            except KeyboardInterrupt:
                console.print("\n  [dim]Press Ctrl+C again or type /quit to exit[/dim]")
                try:
                    with patch_stdout():
                        await asyncio.sleep(0.5)
                except KeyboardInterrupt:
                    break
            except EOFError:
                break

        await self.cleanup()

    def _print_farewell(self):
        """Goodbye message with the session id (Claude Code style)."""
        console.print()
        if self.checkpointer:
            console.print(f"  [dim]Session saved:[/dim] [bold cyan]{self.session_id}[/bold cyan]")
            console.print(
                f"  [dim]Resume with:[/dim] [green]--resume {self.session_id}[/green] [dim]or[/dim] [green]--continue[/green]"
            )
        console.print("  [dim]Goodbye![/dim]")

    def _print_restored_history(self):
        """Show a hint after resuming a persisted session."""
        console.print("  [dim]✓ Conversation history restored from checkpoint.[/dim]")

    def _print_sessions(self):
        """List past sessions from the checkpoint database."""
        if not self.checkpointer:
            console.print("  [yellow]Sessions are disabled[/yellow] [dim]— no checkpoint database in use[/dim]")
            return
        try:
            entries = _list_saved_sessions()
        except Exception as e:
            console.print(f"  [bold red]Error listing sessions:[/bold red] {e}")
            return

        if not entries:
            console.print("  [dim]No saved sessions yet.[/dim]")
            return

        table = Table(box=None, padding=(0, 2))
        table.add_column("Session", style="cyan", no_wrap=True)
        table.add_column("Updated", style="dim")
        table.add_column("Messages", justify="right", style="dim")
        table.add_column("First message", style="white", overflow="fold")

        for sid, ts, n_msgs, preview in entries:
            updated = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "-"
            table.add_row(sid, updated, str(n_msgs), preview)

        console.print()
        console.print(Panel(table, title="[bold green]Saved Sessions[/bold green]", border_style="green"))
        console.print("  [dim]Resume with: tui_app.py --resume <session-id>[/dim]")
        console.print()

    def _print_help(self):
        """Print help information."""
        console.print()
        help_text = Table(show_header=False, box=None, padding=(0, 2))
        help_text.add_column(style="bold cyan", no_wrap=True)
        help_text.add_column(style="dim")

        help_text.add_row("/help", "Show this help")
        help_text.add_row("/clear", "Clear screen")
        help_text.add_row("/status", "Show MCP server status")
        help_text.add_row("/sessions", "List saved sessions")
        help_text.add_row("/quit", "Exit (shows session id)")
        help_text.add_row("", "")
        help_text.add_row("Ctrl+C", "Interrupt current operation")
        help_text.add_row("↑/↓", "Navigate command history")

        console.print(Panel(help_text, title="[bold green]Help[/bold green]", border_style="green", width=50))
        console.print()

    def _print_status(self):
        """Print current status."""
        console.print()
        model_name = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250514")
        sub_model = os.environ.get("LLM_SUBAGENT_MODEL", "anthropic/claude-haiku-4-5-20251001")

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="dim", no_wrap=True)
        table.add_column()

        table.add_row("Model", f"[bold]{model_name}[/bold]")
        table.add_row("Subagent", f"[bold]{sub_model}[/bold]")
        table.add_row("Servers", f"[bold green]{len(MCP_SERVERS)}[/bold green] MCP servers")
        table.add_row("Session", f"[dim]{self.session_id}[/dim]")
        table.add_row("Workdir", f"{self.work_dir}")
        table.add_row("Limit", f"[bold]{RECURSION_LIMIT}[/bold] recursion steps")

        console.print(Panel(table, title="[bold green]Status[/bold green]", border_style="green", width=60))
        console.print()


# ─── Entry Point ──────────────────────────────────────────────────────────

def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="tui_app.py",
        description="Code Hack AI Expert — Claude Code-style TUI",
        epilog="Examples:\n"
               "  python tui_app.py                    # new session, CWD as workdir\n"
               "  python tui_app.py /path/to/project   # choose the working directory\n"
               "  python tui_app.py --continue         # resume the most recent session\n"
               "  python tui_app.py --resume <uuid>     # resume a specific session\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "workdir",
        nargs="?",
        default=None,
        help="working directory (default: current directory)",
    )
    parser.add_argument(
        "--continue", "-c",
        dest="continue_session",
        action="store_true",
        help="resume the most recent session",
    )
    parser.add_argument(
        "--resume", "-r",
        dest="resume_session",
        metavar="SESSION_ID",
        default=None,
        help="resume a specific session by id",
    )
    parser.add_argument(
        "--no-session",
        dest="no_session",
        action="store_true",
        help="disable session persistence (no checkpoint db)",
    )
    return parser.parse_args(argv)


async def _run_tui(args) -> int:
    global WORK_DIR

    # ── Resolve the working directory BEFORE anything else ──
    if args.workdir:
        wd = Path(args.workdir).expanduser().resolve()
        if not wd.is_dir():
            console.print(f"[bold red]Error:[/bold red] Not a directory: {wd}")
            return 1
    else:
        wd = Path.cwd().resolve()
    WORK_DIR = wd
    # chdir so MCP servers spawned later (if any) and relative paths in
    # subprocesses inherit the same workdir; FilesystemBackend also uses it.
    os.chdir(wd)

    # ── Resolve the session id ──
    checkpointer = None
    resume = False

    if args.resume_session:
        session_id = args.resume_session.strip()
        resume = True
    elif args.continue_session:
        session_id = _find_most_recent_session()
        if not session_id:
            console.print("[bold red]Error:[/bold red] No saved session found to continue.")
            return 1
        resume = True
    else:
        session_id = str(uuid_mod.uuid4())

    session_conn = None
    if not args.no_session:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        db_path = _session_db_path()
        session_conn = await aiosqlite.connect(str(db_path))
        checkpointer = AsyncSqliteSaver(session_conn)

    tui = CodeHackTUI(
        work_dir=wd,
        session_id=session_id,
        checkpointer=checkpointer,
        resume=resume,
    )

    exit_code = 0
    try:
        await tui.run()
    except KeyboardInterrupt:
        console.print("\n  [dim]Shutting down...[/dim]")
    finally:
        await tui.cleanup()
        if session_conn is not None:
            await session_conn.close()

    return exit_code


def main():
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        console.print("[bold red]Error:[/bold red] Please set OPENROUTER_API_KEY or OPENAI_API_KEY")
        sys.exit(1)

    args = _parse_args(sys.argv[1:])
    sys.exit(asyncio.run(_run_tui(args)))


if __name__ == "__main__":
    main()
