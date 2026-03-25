#!/usr/bin/env python3
"""
Code Hack AI Expert — Claude Code-style TUI Interface

A terminal UI that connects to 7 MCP servers and provides an interactive
code editing experience similar to Claude Code.

Usage:
    # 1. Start all MCP servers:
    bash start_servers.sh

    # 2. Start the TUI:
    uv run python tui_app.py
"""

import asyncio
import json
import os
import sys
import signal
import warnings
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

# ─── Constants ────────────────────────────────────────────────────────────

EXPERT_DIR = Path(__file__).parent

MCP_SERVERS = {
    "filesystem-command": "http://localhost:8001/mcp",
    "git-tools":          "http://localhost:8002/mcp",
    "code-intel":         "http://localhost:8003/mcp",
    "memory-store":       "http://localhost:8004/mcp",
    "code-review":        "http://localhost:8005/mcp",
    "code-refactor":      "http://localhost:8006/mcp",
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

### 4. Persistent Memory (memory-store)
- `memory_save` / `memory_get` / `memory_search` / `memory_list` / `memory_delete`
- `scratchpad_write` / `scratchpad_read` / `scratchpad_append`
- `qa_experience_save` / `qa_experience_search` / `qa_experience_get`

### 5. Code Review (code-review)
- `review_project` / `review_file` / `review_function` / `health_score`
- `find_long_functions` / `find_complex_functions` / `suggest_reorg` / `review_diff_text`

### 6. Code Refactoring (code-refactor)
- `auto_refactor` / `ydiff_files` / `ydiff_commit` / `ydiff_git_changes`

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
- Use memory to persist important context
- Safety first: confirm before destructive operations

### Two-Phase Commit (Reviewer-Friendly AI Changes)
When changes involve both structural reorganization and logic modifications, **split into two commits**:
1. **Mechanical commit** → add `#not-need-review` (moves, renames, reformats — identity transformation)
2. **Logic commit** → normal commit (behavior changes, bug fixes, new features — reviewer must read)

Reviewers use `git log --grep="#not-need-review" --invert-grep` to skip mechanical changes.
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
    "memory_save": ("Memory", "blue"),
    "memory_get": ("Memory", "blue"),
    "memory_search": ("Memory", "blue"),
    "memory_list": ("Memory", "blue"),
    "memory_delete": ("Memory", "red"),
    "scratchpad_write": ("Scratch", "blue"),
    "scratchpad_read": ("Scratch", "blue"),
    "scratchpad_append": ("Scratch", "blue"),
    "qa_experience_save": ("QA", "blue"),
    "qa_experience_search": ("QA", "blue"),
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
    elif name in ("memory_save", "memory_get"):
        detail = args.get("key", "")
    elif name == "memory_search":
        detail = args.get("query", "")
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


# ─── TUI Application ─────────────────────────────────────────────────────

class CodeHackTUI:
    def __init__(self):
        self.agent = None
        self._exit_stack = None
        self.thread_id = "tui-session"
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
            try:
                transport = await self._exit_stack.enter_async_context(
                    streamablehttp_client(url)
                )
                read_stream, write_stream, _ = transport
                session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()
                tools = await load_mcp_tools(session, server_name=server_name)
                all_tools.extend(tools)
                self.print_server_status(server_name, len(tools), True)
            except Exception as e:
                self.print_server_status(server_name, 0, False)

        console.print()
        console.print(f"  [bold green]{len(all_tools)}[/bold green] [dim]tools loaded from[/dim] [bold]{len(MCP_SERVERS)}[/bold] [dim]servers[/dim]")

        # Load subagents
        subagents = load_subagents(EXPERT_DIR / "subagents.yaml", all_tools)
        if subagents:
            names = ", ".join(s["name"] for s in subagents)
            console.print(f"  [bold cyan]{len(subagents)}[/bold cyan] [dim]subagents:[/dim] {names}")

        console.print()

        self.agent = create_deep_agent(
            model=model,
            tools=all_tools,
            subagents=subagents,
            memory=["./AGENTS.md"],
            backend=FilesystemBackend(root_dir=EXPERT_DIR),
            system_prompt=SYSTEM_PROMPT,
        )

    async def cleanup(self):
        if self._exit_stack:
            await self._exit_stack.aclose()

    async def process_message(self, user_message: str):
        """Stream agent response for a user message."""
        printed_count = 0
        pending_tools = {}  # tool_call_id -> name
        accumulated_text = ""

        try:
            async for chunk in self.agent.astream(
                {"messages": [("user", user_message)]},
                config={"configurable": {"thread_id": self.thread_id}},
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
        except Exception as e:
            console.print(f"\n  [bold red]Error:[/bold red] {e}")

        console.print()

    async def run(self):
        """Main TUI loop."""
        self.print_header()

        # Show config info
        model_name = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250514")
        console.print(f"  [dim]Model:[/dim]    {model_name}")
        console.print(f"  [dim]CWD:[/dim]      {Path.cwd()}")

        await self.init_agent()

        # Print help
        console.print(Rule(style="dim green"))
        console.print("  [dim]Type your message and press Enter. Commands:[/dim]")
        console.print("  [cyan]/help[/cyan]    [dim]— Show help[/dim]")
        console.print("  [cyan]/clear[/cyan]   [dim]— Clear screen[/dim]")
        console.print("  [cyan]/status[/cyan]  [dim]— Show connection status[/dim]")
        console.print("  [cyan]/quit[/cyan]    [dim]— Exit[/dim]")
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
                        console.print("  [dim]Goodbye![/dim]")
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

    def _print_help(self):
        """Print help information."""
        console.print()
        help_text = Table(show_header=False, box=None, padding=(0, 2))
        help_text.add_column(style="bold cyan", no_wrap=True)
        help_text.add_column(style="dim")

        help_text.add_row("/help", "Show this help")
        help_text.add_row("/clear", "Clear screen")
        help_text.add_row("/status", "Show MCP server status")
        help_text.add_row("/quit", "Exit")
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
        table.add_row("Thread", f"[dim]{self.thread_id}[/dim]")
        table.add_row("CWD", f"{Path.cwd()}")

        console.print(Panel(table, title="[bold green]Status[/bold green]", border_style="green", width=60))
        console.print()


# ─── Entry Point ──────────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        console.print("[bold red]Error:[/bold red] Please set OPENROUTER_API_KEY or OPENAI_API_KEY")
        sys.exit(1)

    tui = CodeHackTUI()

    try:
        asyncio.run(tui.run())
    except KeyboardInterrupt:
        console.print("\n  [dim]Shutting down...[/dim]")


if __name__ == "__main__":
    main()
