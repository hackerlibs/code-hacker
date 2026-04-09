#!/usr/bin/env python3
"""
Code Hacker — Memory Browser

A small web app for viewing and managing the reusable-experience memory store
that lives at ~/.code-hacker/memory.db (CozoDB / pycozo backed).

Why a separate app:
    The memory database accumulates AI-knowledge, pipeline recipes, email
    templates, JIRA templates, bug-fix patterns, and devops snippets that
    code-hacker has saved over time. The MCP server is great for the agent,
    but a human wants to browse, search, edit, and prune this knowledge by
    hand. This UI is that "filing cabinet view".

    It opens its own pycozo Client. SQLite's file locking lets it coexist
    with the running memory-store MCP server — both can read AND write the
    same DB at once. Schema creation is idempotent, so it's safe to start
    this app on a fresh machine before the MCP server has ever run.

Run:
    python memory_web.py            # serves on http://localhost:8009
    CODE_HACKER_MEMORY_DB=/path/to/other.db python memory_web.py
"""

import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pycozo.client import Client, QueryException

EXPERT_DIR = Path(__file__).parent
DB_PATH = os.environ.get(
    "CODE_HACKER_MEMORY_DB",
    str(Path.home() / ".code-hacker" / "memory.db"),
)

# Same canonical buckets as memory_store.py — kept in lock-step so the
# sidebar in the UI matches what the agent actually saves.
CATEGORIES = (
    "ai_knowledge",
    "pipeline",
    "email_customer",
    "email_internal",
    "jira_template",
    "bug_fix",
    "devops_lib",
    "qa_experience",
    "general",
)

# ---------------------------------------------------------------------------
# CozoDB client + schema (idempotent — safe to call when memory_store has
# already created everything)
# ---------------------------------------------------------------------------
_client: Optional[Client] = None
_has_fts = False


def get_client() -> Client:
    global _client, _has_fts
    if _client is not None:
        return _client

    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    _client = Client("sqlite", DB_PATH, dataframe=False)

    for ddl in (
        """
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
        """,
        """
        :create memory_tag { tag: String, id: String => }
        """,
        """
        :create scratchpad {
            name: String =>
            content: String,
            updated_at: Float
        }
        """,
    ):
        try:
            _client.run(ddl)
        except QueryException:
            pass

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
        _has_fts = "exists" in str(e).lower()

    return _client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", text.strip().lower())
    return s.strip("_")[:80] or "untitled"


def _make_id(category: str, title: str) -> str:
    return f"{category}:{_slug(title)}"


def _row_to_dict(row: list[Any]) -> dict:
    return {
        "id": row[0],
        "title": row[1],
        "category": row[2],
        "problem": row[3],
        "context": row[4],
        "solution": row[5],
        "pattern": row[6],
        "tags": list(row[7] or []),
        "created_at": row[8],
        "updated_at": row[9],
        "usage_count": row[10],
    }


_ALL_FIELDS = (
    "id, title, category, problem, context, solution, pattern, "
    "tags, created_at, updated_at, usage_count"
)


def _get_row(mid: str) -> Optional[dict]:
    res = get_client().run(
        f"?[{_ALL_FIELDS}] := *memory{{{_ALL_FIELDS}}}, id = $id",
        {"id": mid},
    )
    rows = res["rows"]
    return _row_to_dict(rows[0]) if rows else None


def _replace_tags(mid: str, tags: list[str]) -> None:
    client = get_client()
    client.run(
        "?[tag, id] := *memory_tag{tag, id}, id = $id :rm memory_tag {tag, id}",
        {"id": mid},
    )
    if tags:
        client.put("memory_tag", [{"tag": t, "id": mid} for t in tags])


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------
class MemoryIn(BaseModel):
    title: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    solution: str = ""
    problem: str = ""
    context: str = ""
    pattern: str = ""
    tags: list[str] = Field(default_factory=list)


class MemoryUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    solution: Optional[str] = None
    problem: Optional[str] = None
    context: Optional[str] = None
    pattern: Optional[str] = None
    tags: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Code Hacker Memory Browser")


@app.on_event("startup")
def _startup():
    get_client()  # eager init so errors surface immediately
    print(f"  DB:   {DB_PATH}")
    print(f"  FTS:  {'on' if _has_fts else 'off (substring fallback)'}")
    print(f"  UI:   http://localhost:8009")


@app.on_event("shutdown")
def _shutdown():
    if _client:
        try:
            _client.close()
        except Exception:
            pass


@app.get("/")
def index():
    response = FileResponse(EXPERT_DIR / "static" / "memory.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/api/stats")
def stats():
    """Top-line dashboard numbers: total, top-used, recent edits."""
    client = get_client()

    total = client.run("?[count(id)] := *memory{id}")["rows"]
    total_n = total[0][0] if total else 0

    top = client.run(
        f"?[{_ALL_FIELDS}] := *memory{{{_ALL_FIELDS}}}, usage_count > 0"
    )["rows"]
    top.sort(key=lambda r: -r[10])
    top = [_row_to_dict(r) for r in top[:5]]

    recent = client.run(f"?[{_ALL_FIELDS}] := *memory{{{_ALL_FIELDS}}}")["rows"]
    recent.sort(key=lambda r: -r[9])
    recent = [_row_to_dict(r) for r in recent[:5]]

    return {
        "total": total_n,
        "categories_known": list(CATEGORIES),
        "top_used": top,
        "recent": recent,
        "fts": _has_fts,
        "db_path": DB_PATH,
    }


@app.get("/api/categories")
def categories():
    """Counts per category. Includes empty buckets so the sidebar is stable."""
    res = get_client().run("?[category, count(id)] := *memory{id, category}")
    counts = {row[0]: row[1] for row in res["rows"]}
    return [
        {"category": c, "count": counts.get(c, 0)}
        for c in CATEGORIES
    ] + [
        {"category": c, "count": n}
        for c, n in counts.items()
        if c not in CATEGORIES
    ]


@app.get("/api/tags")
def tags():
    """All tags with usage counts."""
    res = get_client().run("?[tag, count(id)] := *memory_tag{tag, id}")
    return sorted(
        [{"tag": r[0], "count": r[1]} for r in res["rows"]],
        key=lambda x: (-x["count"], x["tag"]),
    )


@app.get("/api/memories")
def list_memories(
    q: str = "",
    category: str = "",
    tag: str = "",
    sort: str = "updated",  # updated | created | used | title
    limit: int = 200,
):
    """
    List/search memories. Combinable filters:
      - q:        full-text query (FTS), or substring fallback
      - category: exact match
      - tag:      exact tag match (joins memory_tag)
      - sort:     updated | created | used | title
      - limit:    cap on results (default 200)
    """
    client = get_client()
    candidates: list[tuple[str, float]] = []

    if q.strip() and _has_fts:
        try:
            res = client.run(
                "?[id, score] := ~memory:search{id | query: $q, k: $k, bind_score: score}",
                {"q": q, "k": max(limit * 3, 60)},
            )
            candidates = [(r[0], r[1]) for r in res["rows"]]
        except QueryException:
            candidates = []

    if not candidates:
        res = client.run("?[id] := *memory{id}")
        candidates = [(r[0], 0.0) for r in res["rows"]]

    # Bulk-fetch all matched rows in one query so we don't N+1.
    ids = [c[0] for c in candidates]
    if not ids:
        return {"items": [], "total": 0}

    res = client.run(
        f"?[{_ALL_FIELDS}] := *memory{{{_ALL_FIELDS}}}, is_in(id, $ids)",
        {"ids": ids},
    )
    rows_by_id = {row[0]: _row_to_dict(row) for row in res["rows"]}
    score_by_id = dict(candidates)

    items = []
    for mid in ids:
        rec = rows_by_id.get(mid)
        if rec is None:
            continue
        rec["_score"] = score_by_id.get(mid, 0.0)
        items.append(rec)

    # Substring fallback when FTS missed
    if q.strip() and not any(it["_score"] > 0 for it in items):
        ql = q.lower()
        items = [
            it for it in items
            if ql in " ".join(
                str(it.get(k) or "")
                for k in ("title", "problem", "context", "solution", "pattern", "category")
            ).lower()
        ]

    if category:
        items = [it for it in items if it["category"] == category]

    if tag:
        tagged_res = client.run(
            "?[id] := *memory_tag{tag, id}, tag = $tag",
            {"tag": tag},
        )
        tagged = {r[0] for r in tagged_res["rows"]}
        items = [it for it in items if it["id"] in tagged]

    sort_key = {
        "updated": lambda it: (-it["_score"], -it["updated_at"]),
        "created": lambda it: (-it["_score"], -it["created_at"]),
        "used":    lambda it: (-it["_score"], -it["usage_count"], -it["updated_at"]),
        "title":   lambda it: (-it["_score"], it["title"].lower()),
    }.get(sort, lambda it: (-it["_score"], -it["updated_at"]))
    items.sort(key=sort_key)

    return {"items": items[:limit], "total": len(items)}


@app.get("/api/memories/{mid}")
def get_memory(mid: str):
    rec = _get_row(mid)
    if not rec:
        raise HTTPException(404, f"memory not found: {mid}")
    return rec


@app.post("/api/memories")
def create_memory(payload: MemoryIn):
    client = get_client()
    mid = _make_id(payload.category, payload.title)
    if _get_row(mid):
        raise HTTPException(409, f"memory already exists: {mid}")
    now = time.time()
    client.put(
        "memory",
        {
            "id": mid,
            "title": payload.title,
            "category": payload.category,
            "problem": payload.problem,
            "context": payload.context,
            "solution": payload.solution,
            "pattern": payload.pattern,
            "tags": payload.tags,
            "created_at": now,
            "updated_at": now,
            "usage_count": 0,
        },
    )
    _replace_tags(mid, payload.tags)
    return _get_row(mid)


@app.put("/api/memories/{mid}")
def update_memory(mid: str, payload: MemoryUpdate):
    """
    Update fields of an existing memory. If `title` or `category` change, the
    memory's id changes too (since `id = category:slug(title)`), so we delete
    the old row and write a new one. Usage_count and created_at carry over.
    """
    existing = _get_row(mid)
    if not existing:
        raise HTTPException(404, f"memory not found: {mid}")

    merged = {**existing}
    for k, v in payload.model_dump(exclude_unset=True).items():
        merged[k] = v

    new_id = _make_id(merged["category"], merged["title"])
    client = get_client()
    now = time.time()

    if new_id != mid:
        # id-renaming path: delete old row + tags, then insert new
        if _get_row(new_id):
            raise HTTPException(409, f"target id already exists: {new_id}")
        _replace_tags(mid, [])
        client.run("?[id] <- [[$id]] :rm memory {id}", {"id": mid})

    client.put(
        "memory",
        {
            "id": new_id,
            "title": merged["title"],
            "category": merged["category"],
            "problem": merged.get("problem", "") or "",
            "context": merged.get("context", "") or "",
            "solution": merged.get("solution", "") or "",
            "pattern": merged.get("pattern", "") or "",
            "tags": list(merged.get("tags") or []),
            "created_at": existing["created_at"],
            "updated_at": now,
            "usage_count": existing["usage_count"],
        },
    )
    _replace_tags(new_id, list(merged.get("tags") or []))
    return _get_row(new_id)


@app.delete("/api/memories/{mid}")
def delete_memory(mid: str):
    if not _get_row(mid):
        raise HTTPException(404, f"memory not found: {mid}")
    _replace_tags(mid, [])
    get_client().run("?[id] <- [[$id]] :rm memory {id}", {"id": mid})
    return {"deleted": mid}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    print()
    print("=== Code Hacker Memory Browser ===")
    print()
    uvicorn.run(app, host="0.0.0.0", port=8009)
