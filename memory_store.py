import json
import time
from pathlib import Path
from typing import Optional
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("memory-store")

# Memory is stored as JSON files in a .agent-memory directory relative to the working directory
MEMORY_DIR = Path.cwd() / ".agent-memory"


def _ensure_dir():
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _memory_path(key: str) -> Path:
    safe_key = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return MEMORY_DIR / f"{safe_key}.json"


@mcp.tool()
async def memory_save(key: str, content: str, category: str = "general", tags: str = "") -> str:
    """Save a piece of information to persistent memory. Survives across chat sessions.
    Use this to remember decisions, context, user preferences, project notes, etc.

    Args:
        key: Unique identifier for this memory (e.g., 'auth-architecture', 'user-preference-style')
        content: The information to remember
        category: Category: 'project', 'user', 'decision', 'context', 'todo' (default: general)
        tags: Comma-separated tags for searching (e.g., 'auth,backend,important')
    """
    _ensure_dir()
    path = _memory_path(key)

    entry = {
        "key": key,
        "content": content,
        "category": category,
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Preserve created_at if updating
    if path.exists():
        try:
            old = json.loads(path.read_text())
            entry["created_at"] = old.get("created_at", entry["created_at"])
        except Exception:
            pass

    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2))
    return f"Memory saved: '{key}' [{category}]"


@mcp.tool()
async def memory_get(key: str) -> str:
    """Retrieve a specific memory by key.

    Args:
        key: The key of the memory to retrieve
    """
    path = _memory_path(key)
    if not path.exists():
        return f"Memory not found: '{key}'"
    try:
        entry = json.loads(path.read_text())
        return (
            f"Key: {entry['key']}\n"
            f"Category: {entry['category']}\n"
            f"Tags: {', '.join(entry.get('tags', []))}\n"
            f"Created: {entry['created_at']}\n"
            f"Updated: {entry['updated_at']}\n"
            f"\n{entry['content']}"
        )
    except Exception as e:
        return f"Error reading memory: {e}"


@mcp.tool()
async def memory_search(query: str = "", category: str = "", tag: str = "") -> str:
    """Search memories by keyword, category, or tag.

    Args:
        query: Text to search for in memory content and keys (default: '' for all)
        category: Filter by category (default: '' for all)
        tag: Filter by tag (default: '' for all)
    """
    _ensure_dir()
    results = []
    query_lower = query.lower()

    for f in sorted(MEMORY_DIR.glob("*.json")):
        try:
            entry = json.loads(f.read_text())
        except Exception:
            continue

        if category and entry.get("category") != category:
            continue
        if tag and tag not in entry.get("tags", []):
            continue
        if query_lower:
            text = f"{entry.get('key', '')} {entry.get('content', '')}".lower()
            if query_lower not in text:
                continue

        results.append(entry)

    if not results:
        return "No memories found matching the criteria."

    lines = [f"Found {len(results)} memories:\n"]
    for entry in results:
        lines.append(
            f"  [{entry['category']}] {entry['key']}"
            f"  (tags: {', '.join(entry.get('tags', []))})"
            f"  updated: {entry['updated_at']}"
        )
        preview = entry["content"][:120].replace("\n", " ")
        lines.append(f"    {preview}...")

    return "\n".join(lines)


@mcp.tool()
async def memory_delete(key: str) -> str:
    """Delete a memory by key.

    Args:
        key: The key of the memory to delete
    """
    path = _memory_path(key)
    if not path.exists():
        return f"Memory not found: '{key}'"
    path.unlink()
    return f"Memory deleted: '{key}'"


@mcp.tool()
async def memory_list(category: str = "") -> str:
    """List all saved memories, optionally filtered by category.

    Args:
        category: Filter by category (default: '' for all)
    """
    _ensure_dir()
    entries = []

    for f in sorted(MEMORY_DIR.glob("*.json")):
        try:
            entry = json.loads(f.read_text())
        except Exception:
            continue
        if category and entry.get("category") != category:
            continue
        entries.append(entry)

    if not entries:
        return "No memories stored." + (f" (category filter: {category})" if category else "")

    lines = [f"Total memories: {len(entries)}\n"]

    # Group by category
    by_cat: dict[str, list] = {}
    for e in entries:
        cat = e.get("category", "general")
        by_cat.setdefault(cat, []).append(e)

    for cat, items in sorted(by_cat.items()):
        lines.append(f"--- {cat} ({len(items)}) ---")
        for e in items:
            lines.append(f"  {e['key']}  (updated: {e['updated_at']})")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def scratchpad_write(content: str) -> str:
    """Write to a temporary scratchpad for complex reasoning. Use this to think step-by-step,
    plan multi-file changes, or track progress on a complex task.

    Args:
        content: Content to write to the scratchpad (overwrites previous)
    """
    _ensure_dir()
    path = MEMORY_DIR / "_scratchpad.md"
    path.write_text(content)
    return f"Scratchpad updated ({len(content)} chars)"


@mcp.tool()
async def scratchpad_read() -> str:
    """Read the current scratchpad content."""
    path = MEMORY_DIR / "_scratchpad.md"
    if not path.exists():
        return "(scratchpad is empty)"
    return path.read_text()


@mcp.tool()
async def scratchpad_append(content: str) -> str:
    """Append to the scratchpad without overwriting.

    Args:
        content: Content to append
    """
    _ensure_dir()
    path = MEMORY_DIR / "_scratchpad.md"
    existing = path.read_text() if path.exists() else ""
    path.write_text(existing + "\n" + content)
    return f"Appended to scratchpad ({len(content)} chars added)"


if __name__ == "__main__":
    mcp.run(transport="stdio")
