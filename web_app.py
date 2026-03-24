#!/usr/bin/env python3
"""
Code Hack AI Expert - Multi-Project Web Interface

Connects to 7 MCP servers (filesystem, git, code-intel, memory, code-review,
code-refactor, multi-project) and provides a unified AI code expert web chat.

Usage:
    # 1. Start all MCP servers:
    bash start_servers.sh

    # 2. Start the web interface:
    uv run python web_app.py

    # 3. Open http://localhost:8000
"""

import asyncio
import json
import os
import sys
import warnings
from pathlib import Path
from contextlib import asynccontextmanager, AsyncExitStack

# Bypass proxy for localhost MCP server connections
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1")

warnings.filterwarnings("ignore", message="Core Pydantic V1 functionality")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from langchain_mcp_adapters.tools import load_mcp_tools

EXPERT_DIR = Path(__file__).parent

# ─── Global State ──────────────────────────────────────────────────────────
agent = None
_exit_stack = None

# ─── MCP Server URLs ──────────────────────────────────────────────────────
MCP_SERVERS = {
    "filesystem-command": "http://localhost:8001/mcp",
    "git-tools":          "http://localhost:8002/mcp",
    "code-intel":         "http://localhost:8003/mcp",
    "memory-store":       "http://localhost:8004/mcp",
    "code-review":        "http://localhost:8005/mcp",
    "code-refactor":      "http://localhost:8006/mcp",
    "multi-project":      "http://localhost:8007/mcp",
}


def get_llm_model() -> ChatOpenAI:
    """Get the LLM model configured via environment variables."""
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    model_name = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250514")

    if not api_key:
        raise ValueError(
            "Please set OPENROUTER_API_KEY or OPENAI_API_KEY environment variable"
        )

    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        max_tokens=16000,
    )


SYSTEM_PROMPT = """\
You are **Code Hack AI Expert**, a full-featured multi-project programming agent.

## Your Toolset (7 MCP Servers)

### 1. Filesystem (filesystem-command)
- `read_file` / `read_file_lines` / `write_file` / `append_file` / `edit_file`
- `find_files` / `search_files_ag` / `list_directory` / `get_file_info`
- `execute_command` / `create_directory` / `get_current_directory`

### 2. Git Operations (git-tools)
- `git_status` / `git_diff` / `git_log` / `git_show`
- `git_add` / `git_commit` / `git_branch` / `git_create_branch` / `git_checkout`
- `git_stash` / `git_blame`

### 3. Code Intelligence (code-intel)
- `analyze_python_file` — Deep Python file analysis (AST-level)
- `extract_symbols` — Symbol extraction for Python/JS/TS/Java/Go/Rust
- `project_overview` — Project panorama: directory tree, language distribution
- `find_references` — Cross-file symbol reference search
- `dependency_graph` — File import/imported-by relationships

### 4. Persistent Memory (memory-store)
- `memory_save` / `memory_get` / `memory_search` / `memory_list` / `memory_delete`
- `scratchpad_write` / `scratchpad_read` / `scratchpad_append`
- `qa_experience_save` / `qa_experience_search` / `qa_experience_get`

### 5. Code Review (code-review)
- `review_project` / `review_file` / `review_function` / `health_score`
- `find_long_functions` / `find_complex_functions` / `suggest_reorg`
- `review_diff_text`

### 6. Code Refactoring & Structural Diff (code-refactor)
- `auto_refactor` — Auto refactoring: split long functions and large files
- `ydiff_files` / `ydiff_commit` / `ydiff_git_changes` — Structural AST-level diff

### 7. Multi-Project Workspace (multi-project)
- `workspace_add` / `workspace_remove` / `workspace_list` / `workspace_overview`
- `workspace_search` / `workspace_find_files` / `workspace_find_dependencies`
- `workspace_read_file` / `workspace_edit_file` / `workspace_write_file`
- `workspace_git_status` / `workspace_git_diff` / `workspace_git_log` / `workspace_commit`
- `workspace_exec`

## Core Working Principles

### Understand First, Act Second
1. Use `project_overview` to understand project structure
2. Use `find_files` and `search_files_ag` to locate relevant files
3. Use `read_file_lines` to read key code sections
4. Use `analyze_python_file` or `extract_symbols` to understand code structure
5. Only start making changes after confirming understanding

### Precise Editing
- Prefer `edit_file` for precise replacements instead of rewriting entire files
- Read the file before modifying to ensure old_string is accurate

### Git Workflow
- Before modifying code, use `git_status` and `git_diff` to understand current state
- After completing related changes, proactively suggest committing

### Memory & Context
- Use `memory_save` to persist important project info and decisions
- At session start, use `memory_list` to check previous context
- Use `scratchpad` for complex task tracking

### Multi-Project Workflow
- Use `workspace_list` to see registered projects
- Use `workspace_add` to register new projects
- Use `workspace_search` for cross-project impact analysis
- Use `workspace_commit` for synchronized commits

### QA Experience
- After solving a problem, offer to record it with `qa_experience_save`
- Before tackling new problems, check `qa_experience_search` for prior patterns

### Safety First
- Never execute dangerous commands
- Confirm intent before modifying files
- Check current state before Git operations
- Never modify files you haven't read

## Style
- Concise and direct
- Search code before making suggestions
- Think like an experienced senior engineer
- Proactively identify potential issues without over-engineering
"""


async def init_agent():
    """Initialize the agent by connecting to all MCP servers."""
    global agent, _exit_stack

    _exit_stack = AsyncExitStack()
    await _exit_stack.__aenter__()

    model = get_llm_model()
    all_tools = []

    print("  Connecting to MCP servers...")
    for server_name, url in MCP_SERVERS.items():
        try:
            transport = await _exit_stack.enter_async_context(
                streamablehttp_client(url)
            )
            read_stream, write_stream, _ = transport

            session = await _exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

            tools = await load_mcp_tools(session, server_name=server_name)
            all_tools.extend(tools)

            tool_names = [t.name for t in tools]
            print(f"  OK {server_name}: {len(tools)} tools — {', '.join(tool_names[:5])}{'...' if len(tool_names) > 5 else ''}")
        except Exception as e:
            print(f"  FAIL {server_name}: {e}")

    print(f"\n  Total: {len(all_tools)} tools loaded from {len(MCP_SERVERS)} servers")

    memory = MemorySaver()
    agent = create_react_agent(
        model=model,
        tools=all_tools,
        prompt=SYSTEM_PROMPT,
        checkpointer=memory,
    )

    return agent


async def cleanup():
    """Close all MCP connections."""
    global _exit_stack
    if _exit_stack:
        await _exit_stack.aclose()


# ─── Helpers ──────────────────────────────────────────────────────────────

def get_content_as_string(content) -> str:
    """Convert message content to string."""
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


def get_tool_display(name: str, args: dict) -> tuple:
    """Get icon and status text for a tool call."""
    # Filesystem
    if name.endswith(":read_file") or name.endswith(":read_file_lines"):
        return "📖", f"读取: {args.get('file_path', 'file')}"
    if name.endswith(":write_file"):
        return "✏️", f"写入: {args.get('file_path', 'file')}"
    if name.endswith(":edit_file"):
        return "✏️", f"编辑: {args.get('file_path', 'file')}"
    if name.endswith(":append_file"):
        return "✏️", f"追加: {args.get('file_path', 'file')}"
    if name.endswith(":list_directory"):
        return "📁", f"列出: {args.get('directory_path', '.')}"
    if name.endswith(":execute_command"):
        return "⚡", f"执行: {args.get('command', '')[:50]}"
    if name.endswith(":search_files_ag"):
        return "🔍", f"搜索: {args.get('pattern', '')[:40]}"
    if name.endswith(":find_files"):
        return "🔍", f"查找: {args.get('pattern', '*')}"

    # Git
    if "git-tools:" in name:
        git_name = name.split(":")[-1]
        return "📜", f"Git {git_name}"

    # Code Intel
    if name.endswith(":analyze_python_file"):
        return "🧬", f"分析: {args.get('file_path', '')}"
    if name.endswith(":project_overview"):
        return "🗺️", f"项目概览: {args.get('directory', '.')}"
    if name.endswith(":find_references"):
        return "🔗", f"引用: {args.get('symbol', '')}"
    if name.endswith(":dependency_graph"):
        return "🕸️", f"依赖: {args.get('file_path', '')}"
    if name.endswith(":extract_symbols"):
        return "🧬", f"符号: {args.get('file_path', '')}"

    # Memory
    if name.endswith(":memory_save"):
        return "💾", f"记忆: {args.get('key', '')}"
    if name.endswith(":memory_get") or name.endswith(":memory_search"):
        return "🧠", f"回忆: {args.get('key', args.get('query', ''))}"
    if name.endswith(":memory_list"):
        return "🧠", "列出记忆"
    if name.endswith(":scratchpad_write") or name.endswith(":scratchpad_append"):
        return "📝", "写草稿"
    if name.endswith(":scratchpad_read"):
        return "📝", "读草稿"
    if name.endswith(":qa_experience_save"):
        return "🎓", f"记录经验: {args.get('title', '')}"
    if name.endswith(":qa_experience_search"):
        return "🎓", f"搜索经验: {args.get('query', '')}"

    # Code Review
    if name.endswith(":review_project"):
        return "🔬", f"审查项目: {args.get('project_dir', '')}"
    if name.endswith(":review_file"):
        return "🔬", f"审查文件: {args.get('file_path', '')}"
    if name.endswith(":review_function"):
        return "🔬", f"审查函数: {args.get('function_name', '')}"
    if name.endswith(":health_score"):
        return "💯", "健康评分"
    if name.endswith(":find_long_functions"):
        return "📏", "超长函数"
    if name.endswith(":find_complex_functions"):
        return "🌀", "高复杂度函数"
    if name.endswith(":suggest_reorg"):
        return "📦", "重组建议"
    if name.endswith(":review_diff_text"):
        return "🔬", "代码对比审查"

    # Code Refactor
    if name.endswith(":auto_refactor"):
        return "🔧", f"自动重构: {args.get('project_dir', '')}"
    if name.endswith(":ydiff_files"):
        return "📊", "结构化 Diff"
    if name.endswith(":ydiff_commit"):
        return "📊", f"Commit Diff: {args.get('commit_id', '')}"
    if name.endswith(":ydiff_git_changes"):
        return "📊", "Git 变更 Diff"

    # Multi-Project
    if name.endswith(":workspace_add"):
        return "➕", f"注册项目: {args.get('alias', args.get('project_path', ''))}"
    if name.endswith(":workspace_list"):
        return "📋", "工作区列表"
    if name.endswith(":workspace_search"):
        return "🔍", f"跨项目搜索: {args.get('pattern', '')[:40]}"
    if name.endswith(":workspace_find_files"):
        return "🔍", f"跨项目查找: {args.get('pattern', '')}"
    if name.endswith(":workspace_find_dependencies"):
        return "🕸️", f"跨项目依赖: {args.get('symbol', '')}"
    if name.endswith(":workspace_read_file"):
        return "📖", f"[{args.get('project', '')}] {args.get('file_path', '')}"
    if name.endswith(":workspace_edit_file"):
        return "✏️", f"[{args.get('project', '')}] {args.get('file_path', '')}"
    if name.endswith(":workspace_git_status"):
        return "📜", "跨项目 Git 状态"
    if name.endswith(":workspace_commit"):
        return "📜", f"协同提交: {args.get('message', '')[:40]}"
    if name.endswith(":workspace_overview"):
        return "🗺️", "工作区概览"
    if name.endswith(":workspace_exec"):
        return "⚡", f"[{args.get('project', '')}] {args.get('command', '')[:40]}"

    return "🔧", name


# ─── FastAPI App ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print()
    print("=== Code Hack AI Expert ===")
    print()
    await init_agent()
    print()
    print("  Web UI: http://localhost:8000")
    print()
    yield
    await cleanup()
    print("Shutting down...")


app = FastAPI(title="Code Hack AI Expert", lifespan=lifespan)


@app.get("/")
async def get_chat():
    response = FileResponse(EXPERT_DIR / "static" / "index.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for chat."""
    await websocket.accept()

    thread_id = f"web-{id(websocket)}"
    printed_count = 0

    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            user_message = message_data.get("message", "")

            if not user_message:
                continue

            printed_count = 0

            try:
                async for chunk in agent.astream(
                    {"messages": [("user", user_message)]},
                    config={"configurable": {"thread_id": thread_id}},
                    stream_mode="values",
                ):
                    if "messages" in chunk:
                        messages = chunk["messages"]

                        for msg in messages[printed_count:]:
                            if isinstance(msg, AIMessage):
                                content = get_content_as_string(msg.content)

                                if content and content.strip():
                                    await websocket.send_json({
                                        "type": "assistant",
                                        "content": content,
                                    })

                                if msg.tool_calls:
                                    for tc in msg.tool_calls:
                                        name = tc.get("name", "unknown")
                                        args = tc.get("args", {})
                                        icon, status = get_tool_display(name, args)
                                        await websocket.send_json({
                                            "type": "tool_call",
                                            "name": name,
                                            "icon": icon,
                                            "status": status,
                                        })

                            elif isinstance(msg, ToolMessage):
                                content = get_content_as_string(msg.content)
                                success = not (
                                    content and "error" in content.lower()[:100]
                                )
                                await websocket.send_json({
                                    "type": "tool_result",
                                    "name": getattr(msg, "name", ""),
                                    "success": success,
                                })

                        printed_count = len(messages)

                await websocket.send_json({"type": "done"})

            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": str(e),
                })

    except WebSocketDisconnect:
        print(f"Client disconnected: {thread_id}")


# ─── Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: Please set OPENROUTER_API_KEY or OPENAI_API_KEY")
        sys.exit(1)

    model_name = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250514")
    base_url = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")

    print()
    print("=== Code Hack AI Expert - Web Interface ===")
    print(f"  Model:    {model_name}")
    print(f"  Base URL: {base_url}")
    print(f"  Servers:  {len(MCP_SERVERS)} MCP servers")
    print()

    uvicorn.run(app, host="0.0.0.0", port=8000)
