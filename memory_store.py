"""Reusable-experience memory store for code-hacker, backed by CozoDB (Datalog).

Why CozoDB:
    Code-hacker accumulates a lot of "this worked, do it again next time" knowledge:
    AI prompts that worked, pipeline configs, customer email templates, JIRA templates,
    bug-fix recipes, devops library usage notes. We want to save them once and find
    the right one fast — by keyword, by category, by tag. CozoDB gives us:
      * persistent SQLite-backed storage (single file, no server)
      * a full-text search index for fuzzy keyword lookup
      * Datalog joins so we can combine FTS + category + tag filters in one query
      * usage_count tracking so frequently-reused items rank higher

Schema:
    memory{id => title, category, problem, context, solution, pattern, tags,
            created_at, updated_at, usage_count}
        Primary table. `id` is `<category>:<slug(title)>` for human readability.

    memory_tag{tag, id =>}
        Inverted index for tag filtering — joined against `memory` for tag queries.

    memory:search   (FTS index)
        Full-text index over concat(title, problem, context, solution, pattern,
        category). Tokenized Simple+Lowercase so it handles Chinese and English.

    scratchpad{name => content, updated_at}
        Free-form working memory for the current task.

Search strategy (memory_search):
    1. If query is given:   FTS lookup -> ids+score
    2. If category is given: filter rows by category
    3. If tag is given:      join with memory_tag
    4. Sort by score desc, then usage_count desc, then updated_at desc

Storage location:
    By default ~/.code-hacker/memory.db so knowledge is shared across projects
    (email templates, devops recipes etc. are usually project-agnostic).
    Override with the CODE_HACKER_MEMORY_DB environment variable.
"""

import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from pycozo.client import Client, QueryException

mcp = FastMCP(name="memory-store", host="localhost", port=8004)

# ---------------------------------------------------------------------------
# Categories — predefined to keep the knowledge base navigable. Free-text is
# allowed too; these are just the well-known buckets the user asked for.
# ---------------------------------------------------------------------------
CATEGORIES = (
    "ai_knowledge",     # AI/ML patterns, prompts, model usage notes
    "pipeline",         # CI/CD, data pipelines, build systems
    "email_customer",   # customer-facing email templates
    "email_internal",   # internal team / stakeholder email templates
    "jira_template",    # JIRA / ticket templates
    "bug_fix",          # bug-fix recipes
    "devops_lib",       # devops / infra library usage notes
    "qa_experience",    # successful QA dialogue patterns
    "general",
)

DB_PATH = os.environ.get(
    "CODE_HACKER_MEMORY_DB",
    str(Path.home() / ".code-hacker" / "memory.db"),
)

_client: Optional[Client] = None
_has_fts: bool = False


def _get_client() -> Client:
    """Lazy singleton — open the DB and ensure schema on first access."""
    global _client, _has_fts
    if _client is not None:
        return _client

    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    _client = Client("sqlite", DB_PATH, dataframe=False)

    # Create main relation if not yet present.
    try:
        _client.run("""
        :create memory {
            id: String =>
            title: String,
            category: String,
            problem: String,
            context: String,
            solution: String,
            pattern: String,
            tags: [String],
            created_at: Float,
            updated_at: Float,
            usage_count: Int default 0
        }
        """)
    except QueryException:
        pass  # already exists

    try:
        _client.run("""
        :create memory_tag {
            tag: String,
            id: String =>
        }
        """)
    except QueryException:
        pass

    try:
        _client.run("""
        :create scratchpad {
            name: String =>
            content: String,
            updated_at: Float
        }
        """)
    except QueryException:
        pass

    # FTS index — try to create, treat "already exists" as success.
    try:
        _client.run("""
        ::fts create memory:search {
            extractor: concat(title, ' ', problem, ' ', context, ' ', solution, ' ', pattern, ' ', category),
            tokenizer: Simple,
            filters: [Lowercase]
        }
        """)
        _has_fts = True
    except QueryException as e:
        if "exists" in str(e).lower():
            _has_fts = True
        else:
            _has_fts = False

    return _client


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", text.strip().lower())
    return s.strip("_")[:80] or "untitled"


def _make_id(category: str, title: str) -> str:
    return f"{category}:{_slug(title)}"


def _split_tags(tags: str) -> list[str]:
    return [t.strip() for t in tags.split(",") if t.strip()]


def _replace_tags(client: Client, mid: str, tags: list[str]) -> None:
    """Atomically swap the tag rows for a memory id."""
    # Remove existing tag rows
    client.run(
        """
        ?[tag, id] := *memory_tag{tag, id}, id = $id
        :rm memory_tag {tag, id}
        """,
        {"id": mid},
    )
    if tags:
        client.put("memory_tag", [{"tag": t, "id": mid} for t in tags])


def _format_row(row: list[Any]) -> str:
    (mid, title, category, problem, context, solution, pattern,
     tags, created_at, updated_at, usage_count) = row
    return (
        f"# [{category}] {title}\n"
        f"id: {mid}\n"
        f"tags: {', '.join(tags) if tags else '-'}\n"
        f"created: {time.strftime('%Y-%m-%d %H:%M', time.localtime(created_at))}  "
        f"updated: {time.strftime('%Y-%m-%d %H:%M', time.localtime(updated_at))}  "
        f"used: {usage_count}\n\n"
        f"## Problem\n{problem or '-'}\n\n"
        f"## Context\n{context or '-'}\n\n"
        f"## Solution\n{solution or '-'}\n\n"
        f"## Pattern\n{pattern or '-'}"
    )


def _get_row(client: Client, mid: str) -> Optional[list[Any]]:
    res = client.run(
        """
        ?[id, title, category, problem, context, solution, pattern, tags, created_at, updated_at, usage_count] :=
            *memory{id, title, category, problem, context, solution, pattern, tags, created_at, updated_at, usage_count},
            id = $id
        """,
        {"id": mid},
    )
    return res["rows"][0] if res["rows"] else None


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------
@mcp.tool()
async def memory_save(
    title: str,
    category: str,
    solution: str,
    problem: str = "",
    context: str = "",
    pattern: str = "",
    tags: str = "",
) -> str:
    """Save a reusable engineering experience to long-term memory.

    Use this to remember anything worth doing the same way next time:
    AI prompt that worked, pipeline recipe, customer email wording, JIRA template,
    bug fix steps, devops library snippet, etc. Idempotent on (category, title) —
    saving the same title in the same category updates the existing record.

    Args:
        title: Short, descriptive title — used as part of the id.
        category: One of: ai_knowledge, pipeline, email_customer, email_internal,
                  jira_template, bug_fix, devops_lib, qa_experience, general.
                  Free-text is allowed but stick to the list when possible.
        solution: The actual content — the prompt, the email body, the fix, the
                  command, etc. This is the part you'll paste back next time.
        problem: What situation does this address? (optional but recommended for
                 fast retrieval)
        context: When/where it applies — stack, environment, audience, etc.
        pattern: The reusable strategy distilled out of this experience.
        tags: Comma-separated tags for filtering (e.g., 'python,django,outage').
    """
    client = _get_client()
    mid = _make_id(category, title)
    now = time.time()
    tag_list = _split_tags(tags)

    existing = _get_row(client, mid)
    created_at = existing[8] if existing else now

    client.put(
        "memory",
        {
            "id": mid,
            "title": title,
            "category": category,
            "problem": problem,
            "context": context,
            "solution": solution,
            "pattern": pattern,
            "tags": tag_list,
            "created_at": created_at,
            "updated_at": now,
            "usage_count": existing[10] if existing else 0,
        },
    )
    _replace_tags(client, mid, tag_list)

    verb = "updated" if existing else "saved"
    return f"Memory {verb}: {mid}"


@mcp.tool()
async def memory_get(id: str) -> str:
    """Retrieve a memory by id and increment its usage counter.

    Args:
        id: The memory id (format: '<category>:<slug>').
    """
    client = _get_client()
    row = _get_row(client, id)
    if not row:
        return f"Memory not found: {id}"

    # Bump usage_count so frequently-used items float to the top.
    client.run(
        """
        ?[id, usage_count] <- [[$id, $n]]
        :update memory {id => usage_count}
        """,
        {"id": id, "n": row[10] + 1},
    )
    return _format_row(row)


@mcp.tool()
async def memory_search(
    query: str = "",
    category: str = "",
    tag: str = "",
    limit: int = 20,
) -> str:
    """Find memories by keyword, category, and/or tag. Combinable filters.

    Strategy: full-text search via the FTS index when `query` is given, then
    filter by category and tag if specified, then sort by score and recency.

    Args:
        query: Natural-language keywords (e.g., 'circular import django').
        category: Restrict to this category.
        tag: Restrict to memories with this tag.
        limit: Maximum number of results (default 20).
    """
    client = _get_client()

    # Build the result set in stages so we can combine FTS + filters cleanly.
    candidates: list[tuple[str, float]] = []  # (id, score)

    if query.strip() and _has_fts:
        try:
            res = client.run(
                """
                ?[id, score] :=
                    ~memory:search{id | query: $q, k: $k, bind_score: score}
                """,
                {"q": query, "k": max(limit * 3, 30)},
            )
            candidates = [(r[0], r[1]) for r in res["rows"]]
        except QueryException:
            candidates = []

    if not candidates:
        # No query, or FTS unavailable / empty — pull all ids.
        res = client.run("?[id] := *memory{id}")
        candidates = [(r[0], 0.0) for r in res["rows"]]

        if query.strip():
            # Substring fallback when FTS missed.
            q = query.lower()
            kept = []
            for mid, _ in candidates:
                row = _get_row(client, mid)
                if not row:
                    continue
                hay = " ".join(str(x) for x in row[1:7]).lower()
                if q in hay:
                    kept.append((mid, 0.0))
            candidates = kept

    # Apply category filter
    if category:
        kept = []
        for mid, sc in candidates:
            row = _get_row(client, mid)
            if row and row[2] == category:
                kept.append((mid, sc))
        candidates = kept

    # Apply tag filter
    if tag:
        res = client.run(
            "?[id] := *memory_tag{tag, id}, tag = $tag",
            {"tag": tag},
        )
        tagged = {r[0] for r in res["rows"]}
        candidates = [(mid, sc) for mid, sc in candidates if mid in tagged]

    if not candidates:
        return "No memories matched."

    # Score-sort, then by usage_count and updated_at as tiebreakers.
    enriched = []
    for mid, sc in candidates:
        row = _get_row(client, mid)
        if row:
            enriched.append((sc, row[10], row[9], row))
    enriched.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    enriched = enriched[:limit]

    lines = [f"Found {len(enriched)} memor{'y' if len(enriched) == 1 else 'ies'}:\n"]
    for sc, uc, _, row in enriched:
        mid, title, cat = row[0], row[1], row[2]
        problem = (row[3] or "").replace("\n", " ")[:120]
        lines.append(
            f"  [{cat}] {title}\n"
            f"    id: {mid}  (used {uc}x" + (f", score {sc:.2f}" if sc else "") + ")\n"
            f"    {problem}"
        )
    lines.append("\nUse memory_get(<id>) to read the full record.")
    return "\n".join(lines)


@mcp.tool()
async def memory_list(category: str = "", limit: int = 50) -> str:
    """List recent memories, optionally filtered by category.

    Args:
        category: Filter by category (default: all categories).
        limit: Maximum number to return (default 50).
    """
    client = _get_client()
    if category:
        res = client.run(
            """
            ?[id, title, category, updated_at, usage_count] :=
                *memory{id, title, category, updated_at, usage_count},
                category = $cat
            """,
            {"cat": category},
        )
    else:
        res = client.run("""
            ?[id, title, category, updated_at, usage_count] :=
                *memory{id, title, category, updated_at, usage_count}
        """)

    rows = res["rows"]
    if not rows:
        return "No memories stored." + (f" (category={category})" if category else "")

    rows.sort(key=lambda r: (-r[3], -r[4]))
    rows = rows[:limit]

    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r[2], []).append(r)

    lines = [f"Total: {len(rows)} memor{'y' if len(rows) == 1 else 'ies'}\n"]
    for cat, items in sorted(by_cat.items()):
        lines.append(f"--- {cat} ({len(items)}) ---")
        for r in items:
            ts = time.strftime("%Y-%m-%d", time.localtime(r[3]))
            lines.append(f"  {r[1]}  [used {r[4]}x, {ts}]\n    id: {r[0]}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
async def memory_delete(id: str) -> str:
    """Delete a memory and its tag rows.

    Args:
        id: The memory id to remove.
    """
    client = _get_client()
    if not _get_row(client, id):
        return f"Memory not found: {id}"

    _replace_tags(client, id, [])
    client.run("?[id] <- [[$id]] :rm memory {id}", {"id": id})
    return f"Deleted: {id}"


@mcp.tool()
async def memory_categories() -> str:
    """Show all categories with how many memories each contains."""
    client = _get_client()
    res = client.run("""
        ?[category, count(id)] := *memory{id, category}
    """)
    rows = res["rows"]
    if not rows:
        return "No memories stored yet.\nKnown categories: " + ", ".join(CATEGORIES)

    rows.sort(key=lambda r: -r[1])
    lines = ["Memory by category:\n"]
    for cat, n in rows:
        lines.append(f"  {cat}: {n}")
    lines.append("\nKnown categories: " + ", ".join(CATEGORIES))
    return "\n".join(lines)


@mcp.tool()
async def memory_top_used(limit: int = 10) -> str:
    """Show the most-frequently-retrieved memories — your real workhorses."""
    client = _get_client()
    res = client.run("""
        ?[id, title, category, usage_count] :=
            *memory{id, title, category, usage_count},
            usage_count > 0
    """)
    rows = sorted(res["rows"], key=lambda r: -r[3])[:limit]
    if not rows:
        return "No memories have been retrieved yet."
    lines = [f"Top {len(rows)} most-used memories:\n"]
    for r in rows:
        lines.append(f"  [{r[2]}] {r[1]}  (used {r[3]}x)\n    id: {r[0]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-category convenience finders — same as memory_search with category preset
# ---------------------------------------------------------------------------
async def _find(category: str, query: str, limit: int) -> str:
    return await memory_search(query=query, category=category, limit=limit)


@mcp.tool()
async def find_email_template(query: str = "", to_customer: bool = True, limit: int = 10) -> str:
    """Find a saved email template by keyword.

    Args:
        query: Keywords (e.g., 'apology outage', 'feature launch').
        to_customer: True for customer-facing, False for internal.
        limit: Max results.
    """
    return await _find("email_customer" if to_customer else "email_internal", query, limit)


@mcp.tool()
async def find_jira_template(query: str = "", limit: int = 10) -> str:
    """Find a saved JIRA / ticket template by keyword."""
    return await _find("jira_template", query, limit)


@mcp.tool()
async def find_bugfix(query: str = "", limit: int = 10) -> str:
    """Find a saved bug-fix recipe by keyword (error message, stack, etc.)."""
    return await _find("bug_fix", query, limit)


@mcp.tool()
async def find_pipeline(query: str = "", limit: int = 10) -> str:
    """Find a saved CI/CD or data pipeline recipe."""
    return await _find("pipeline", query, limit)


@mcp.tool()
async def find_devops_lib(query: str = "", limit: int = 10) -> str:
    """Find saved devops/infra library usage notes."""
    return await _find("devops_lib", query, limit)


@mcp.tool()
async def find_ai_knowledge(query: str = "", limit: int = 10) -> str:
    """Find saved AI/ML knowledge — prompts, model usage, patterns."""
    return await _find("ai_knowledge", query, limit)


# ---------------------------------------------------------------------------
# QA experience — kept as a thin wrapper for backwards compatibility with the
# previous file-based API. Stored in the same `memory` table under category
# 'qa_experience' so it's searchable alongside everything else.
# ---------------------------------------------------------------------------
@mcp.tool()
async def qa_experience_save(
    title: str,
    problem: str,
    key_turns: str,
    resolution: str,
    pattern: str,
    tags: str = "",
) -> str:
    """Save a successful problem-solving dialogue as a reusable QA pattern.

    Args:
        title: Short title (e.g., 'fix-circular-import').
        problem: The initial symptom / question.
        key_turns: The pivotal dialogue turns and reasoning steps.
        resolution: What ultimately fixed it.
        pattern: The reusable strategy.
        tags: Comma-separated tags.
    """
    return await memory_save(
        title=title,
        category="qa_experience",
        problem=problem,
        context=key_turns,
        solution=resolution,
        pattern=pattern,
        tags=tags,
    )


@mcp.tool()
async def qa_experience_search(query: str = "", tag: str = "", limit: int = 20) -> str:
    """Search saved QA experience patterns."""
    return await memory_search(query=query, category="qa_experience", tag=tag, limit=limit)


@mcp.tool()
async def qa_experience_get(title: str) -> str:
    """Retrieve a QA experience by title."""
    return await memory_get(_make_id("qa_experience", title))


# ---------------------------------------------------------------------------
# Scratchpad — short-lived working memory, also stored in CozoDB.
# ---------------------------------------------------------------------------
@mcp.tool()
async def scratchpad_write(content: str, name: str = "default") -> str:
    """Overwrite a named scratchpad. Use for step-by-step planning notes.

    Args:
        content: New content (replaces previous).
        name: Scratchpad name (default 'default').
    """
    client = _get_client()
    client.put("scratchpad", {"name": name, "content": content, "updated_at": time.time()})
    return f"Scratchpad '{name}' updated ({len(content)} chars)"


@mcp.tool()
async def scratchpad_read(name: str = "default") -> str:
    """Read a named scratchpad.

    Args:
        name: Scratchpad name (default 'default').
    """
    client = _get_client()
    res = client.run(
        "?[content] := *scratchpad{name, content}, name = $name",
        {"name": name},
    )
    if not res["rows"]:
        return f"(scratchpad '{name}' is empty)"
    return res["rows"][0][0]


@mcp.tool()
async def scratchpad_append(content: str, name: str = "default") -> str:
    """Append to a named scratchpad without overwriting.

    Args:
        content: Content to append.
        name: Scratchpad name (default 'default').
    """
    client = _get_client()
    res = client.run(
        "?[content] := *scratchpad{name, content}, name = $name",
        {"name": name},
    )
    existing = res["rows"][0][0] if res["rows"] else ""
    new_content = existing + ("\n" if existing else "") + content
    client.put("scratchpad", {"name": name, "content": new_content, "updated_at": time.time()})
    return f"Scratchpad '{name}' appended ({len(content)} chars added)"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
