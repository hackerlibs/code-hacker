"""
Shared fixtures for LLM integration tests.

These tests require:
1. All 7 MCP servers running (bash start_servers.sh)
2. OPENROUTER_API_KEY or OPENAI_API_KEY set
3. NO_PROXY=localhost,127.0.0.1 (auto-set by web_app)

Run:
    NO_PROXY=localhost,127.0.0.1 uv run pytest tests/ -v -s
"""

import os
import sys
import asyncio
import pytest

# Ensure proxy bypass for MCP localhost connections
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1")

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the whole test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def deep_agent():
    """
    Initialize the DeepAgent with all 7 MCP servers.
    Shared across the whole test session to avoid repeated startup.
    """
    from web_app import init_agent, cleanup

    agent = await init_agent()
    yield agent

    # Cleanup MCP connections (suppress benign teardown errors)
    try:
        await cleanup()
    except Exception:
        pass


@pytest.fixture(scope="session")
async def memory_tools():
    """
    Fresh MCP client connected straight to the running memory-store server.

    Tests use this fixture to seed and clean up memories without going through
    the LLM. We deliberately do NOT import memory_store directly: pycozo forbids
    two processes opening the same embedded SQLite DB, and the MCP server is
    already holding it open.
    """
    from contextlib import AsyncExitStack
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from langchain_mcp_adapters.tools import load_mcp_tools

    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        transport = await stack.enter_async_context(
            streamablehttp_client("http://localhost:8004/mcp")
        )
        read_stream, write_stream, _ = transport
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        tools = await load_mcp_tools(session, server_name="memory-store")
        by_name = {t.name: t for t in tools}
        yield by_name
    finally:
        try:
            await stack.aclose()
        except Exception:
            pass


async def run_agent_query(agent, query: str, thread_id: str = "test") -> dict:
    """
    Run a query through the DeepAgent and collect results.

    Returns dict with:
        - messages: list of all messages
        - text: final assistant text response
        - tool_calls: list of tool names invoked
        - tool_calls_full: list of (name, args) tuples for finer assertions
        - tool_results: list of tool result contents
    """
    from langchain_core.messages import AIMessage, ToolMessage

    text_parts = []
    tool_calls = []
    tool_calls_full = []
    tool_results = []
    all_messages = []

    async for chunk in agent.astream(
        {"messages": [("user", query)]},
        config={"configurable": {"thread_id": thread_id}},
        stream_mode="values",
    ):
        if "messages" not in chunk:
            continue
        all_messages = chunk["messages"]

    # Process final message list
    for msg in all_messages:
        if isinstance(msg, AIMessage):
            if msg.content:
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if content.strip():
                    text_parts.append(content)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name", "unknown")
                    tool_calls.append(name)
                    tool_calls_full.append((name, tc.get("args", {}) or {}))
        elif isinstance(msg, ToolMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            tool_results.append(content)

    return {
        "messages": all_messages,
        "text": "\n".join(text_parts),
        "tool_calls": tool_calls,
        "tool_calls_full": tool_calls_full,
        "tool_results": tool_results,
    }


async def cleanup_memory_by_tag(memory_tools: dict, tag: str) -> None:
    """
    Delete every memory carrying the given tag. Used by tests to leave the
    user's real memory store clean. Tolerant of missing tools / no matches.
    """
    import re

    search = memory_tools.get("memory_search")
    delete = memory_tools.get("memory_delete")
    if not search or not delete:
        return

    try:
        result = await search.ainvoke({"query": "", "tag": tag, "limit": 100})
    except Exception:
        return

    text = result if isinstance(result, str) else str(result)
    # memory_search formats each row as "    id: <id>  (used ...)" — pull every id.
    ids = re.findall(r"id:\s*(\S+)", text)
    for mid in set(ids):
        try:
            await delete.ainvoke({"id": mid})
        except Exception:
            pass
