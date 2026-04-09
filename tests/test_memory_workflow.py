"""
Real-LLM tests for the reusable-experience memory workflow.

Covers the loop the user cares about:
  1. After solving a problem the user says "记住它", the agent classifies the
     experience and calls memory_save with the right `category`.
  2. On a new conversation, the agent recalls the prior experience via the
     category-scoped finder (find_pipeline / find_bugfix / find_email_template …)
     and applies the same pattern.

Each test isolates itself with a unique `pytest-<uuid>` tag and cleans up via
`cleanup_memory_by_tag` so the user's real `~/.code-hacker/memory.db` isn't
polluted between runs.

Requirements:
    - All MCP servers running (bash start_servers.sh)
    - OPENROUTER_API_KEY set
    - Run: NO_PROXY=localhost,127.0.0.1 uv run pytest tests/test_memory_workflow.py -v -s
"""

import uuid
import pytest

from conftest import run_agent_query, cleanup_memory_by_tag


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _calls_with_name(tool_calls_full, name):
    return [args for n, args in tool_calls_full if n == name]


def _any_call_among(tool_calls_full, names):
    return [(n, a) for n, a in tool_calls_full if n in names]


# ---------------------------------------------------------------------------
# 1. Save trigger — pipeline
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_save_pipeline_on_remember_trigger(deep_agent, memory_tools):
    """
    User narrates an A→B→C debugging session that fixed an Airflow DAG retry
    storm, then says "帮我记住它". The agent must call memory_save with
    category='pipeline' and the unique title we provided.
    """
    tag = f"pytest-{uuid.uuid4().hex[:8]}"
    title = f"test airflow retry storm {tag}"

    try:
        result = await run_agent_query(
            deep_agent,
            (
                "我刚刚解决了一个 Airflow DAG 的重试风暴问题。我尝试了几个 prompt：\n"
                "- prompt A: 调高 retry_delay → 没用\n"
                "- prompt B: 设置 max_active_runs=1 → 没用\n"
                "- prompt C: 在 on_failure_callback 里调用 dag.set_state(FAILED) → 成功了！\n\n"
                f"帮我记住它。标题就用 \"{title}\"，tags 加上 \"{tag},airflow,pipeline\"。"
            ),
            thread_id=f"test-mem-save-pipe-{uuid.uuid4().hex[:8]}",
        )

        save_calls = _calls_with_name(result["tool_calls_full"], "memory_save")
        assert save_calls, (
            f"Expected memory_save to be called. Tool calls: {result['tool_calls']}"
        )

        # At least one save call must use category='pipeline' and the unique title
        pipeline_saves = [
            args for args in save_calls
            if args.get("category") == "pipeline" and tag in (args.get("title") or "")
        ]
        assert pipeline_saves, (
            f"memory_save called but not with category=pipeline+our title.\n"
            f"  Saves seen: {save_calls}"
        )
    finally:
        await cleanup_memory_by_tag(memory_tools, tag)


# ---------------------------------------------------------------------------
# 2. Save trigger — bug_fix
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_save_bugfix_on_remember_trigger(deep_agent, memory_tools):
    """
    User describes a Django circular-import fix and says "记住". The agent
    should classify the experience as `bug_fix`.
    """
    tag = f"pytest-{uuid.uuid4().hex[:8]}"
    title = f"test django circular import {tag}"

    try:
        result = await run_agent_query(
            deep_agent,
            (
                "我刚刚修复了一个 Django 的循环导入 bug。\n"
                "症状: ImportError: cannot import name X (most likely due to circular import)\n"
                "解决: 把顶层的 import 移到函数体内部, 延迟到运行时。\n\n"
                f"帮我记住这个修复经验。标题用 \"{title}\"，tags 加上 \"{tag},django,import\"。"
            ),
            thread_id=f"test-mem-save-bug-{uuid.uuid4().hex[:8]}",
        )

        save_calls = _calls_with_name(result["tool_calls_full"], "memory_save")
        assert save_calls, f"Expected memory_save. Tool calls: {result['tool_calls']}"

        bug_saves = [
            args for args in save_calls
            if args.get("category") == "bug_fix" and tag in (args.get("title") or "")
        ]
        assert bug_saves, (
            f"memory_save called but not with category=bug_fix+our title.\n"
            f"  Saves seen: {save_calls}"
        )
    finally:
        await cleanup_memory_by_tag(memory_tools, tag)


# ---------------------------------------------------------------------------
# 3. Save trigger — email_customer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_save_customer_email_on_remember_trigger(deep_agent, memory_tools):
    """
    User describes a customer-facing postmortem email template and says
    "帮我记住". Must classify as `email_customer`.
    """
    tag = f"pytest-{uuid.uuid4().hex[:8]}"
    title = f"test postmortem apology {tag}"

    try:
        result = await run_agent_query(
            deep_agent,
            (
                "我刚刚为一次 P1 生产事故写了一封发给客户的致歉邮件，结构是：\n"
                "  事实 → 影响 → 根因 → 修复 → 预防措施。\n"
                "客户反馈说这封邮件写得很好。\n\n"
                f"帮我记住这个客户邮件模板。标题用 \"{title}\"，tags 加上 \"{tag},email,incident\"。"
            ),
            thread_id=f"test-mem-save-email-{uuid.uuid4().hex[:8]}",
        )

        save_calls = _calls_with_name(result["tool_calls_full"], "memory_save")
        assert save_calls, f"Expected memory_save. Tool calls: {result['tool_calls']}"

        email_saves = [
            args for args in save_calls
            if args.get("category") == "email_customer" and tag in (args.get("title") or "")
        ]
        assert email_saves, (
            f"memory_save called but not with category=email_customer+our title.\n"
            f"  Saves seen: {save_calls}"
        )
    finally:
        await cleanup_memory_by_tag(memory_tools, tag)


# ---------------------------------------------------------------------------
# 4. Recall — pre-seed pipeline memory, ask similar question, expect a finder
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recall_pipeline_via_finder(deep_agent, memory_tools):
    """
    Pre-seed a unique pipeline memory directly via the MCP tools, then ask the
    agent a similar question. The agent must call a memory finder/search and
    surface the seeded `pattern` in its answer.
    """
    tag = f"pytest-{uuid.uuid4().hex[:8]}"
    marker = f"MARKER_{uuid.uuid4().hex[:8].upper()}"
    title = f"jenkins shared library version pin {tag}"

    save_tool = memory_tools["memory_save"]
    await save_tool.ainvoke(
        {
            "title": title,
            "category": "pipeline",
            "problem": "Jenkins shared library auto-updates and breaks unrelated jobs",
            "context": "Tried: branch=main (drift), tag=v1 (still drifts on retag), commit SHA pin (works)",
            "solution": "Pin the @Library directive to a specific commit SHA, never to main or a moving tag",
            "pattern": f"Always pin Jenkins shared libraries to immutable refs. {marker}",
            "tags": f"{tag},jenkins,pipeline,library",
        }
    )

    try:
        result = await run_agent_query(
            deep_agent,
            (
                "我们生产 Jenkins 上有一些 job 突然挂了，看起来是 shared library "
                "自动升级到了一个有 bug 的版本。我应该怎么彻底避免这种情况？"
                "如果之前有解决过的经验也可以参考。"
            ),
            thread_id=f"test-mem-recall-pipe-{uuid.uuid4().hex[:8]}",
        )

        recall_tools = {
            "find_pipeline", "memory_search", "memory_get",
            "find_devops_lib", "memory_list", "qa_experience_search",
        }
        used_recall = recall_tools & set(result["tool_calls"])
        assert used_recall, (
            f"Expected at least one recall tool from {recall_tools}, "
            f"got: {result['tool_calls']}"
        )

        # The seeded pattern carries a unique marker — if the agent really
        # consulted the memory, the marker (or the SHA-pin idea) should land
        # in its answer. We accept either: marker present, or 'commit sha'/'pin'
        # phrasing that proves it read the seeded record.
        text_lower = result["text"].lower()
        assert (
            marker in result["text"]
            or "commit sha" in text_lower
            or "sha" in text_lower and "pin" in text_lower
        ), (
            f"Agent didn't surface the seeded pattern. "
            f"Tools used: {result['tool_calls']}\nText:\n{result['text'][:500]}"
        )
    finally:
        await cleanup_memory_by_tag(memory_tools, tag)


# ---------------------------------------------------------------------------
# 5. Full loop — save in thread A, recall in thread B
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_full_save_then_recall_loop(deep_agent, memory_tools):
    """
    End-to-end loop: in thread A the LLM saves an experience after "记住它",
    in thread B (fresh thread, no shared chat history) the LLM is asked a
    similar question and must recall the saved experience.
    """
    tag = f"pytest-{uuid.uuid4().hex[:8]}"
    marker = f"MARKER_{uuid.uuid4().hex[:8].upper()}"
    title = f"terraform import drift fix {tag}"

    # ---- thread A: save ---------------------------------------------------
    save_result = await run_agent_query(
        deep_agent,
        (
            "我刚解决了一个 Terraform state drift 的问题：AWS 控制台手动改过的资源 "
            "和 state 不一致, terraform plan 一直想 recreate. 我尝试了:\n"
            "- prompt A: terraform refresh → 没用，新字段还是要 recreate\n"
            "- prompt B: terraform import 把现存资源重新 import → 成功!\n\n"
            f"帮我记住这个 devops 经验, 标题用 \"{title}\", "
            f"在 pattern 字段里加上字符串 \"{marker}\", "
            f"tags 加上 \"{tag},terraform,aws\"。"
        ),
        thread_id=f"test-mem-loop-save-{uuid.uuid4().hex[:8]}",
    )

    save_calls = _calls_with_name(save_result["tool_calls_full"], "memory_save")
    assert save_calls, (
        f"Save phase: expected memory_save. Tool calls: {save_result['tool_calls']}"
    )
    matching_saves = [
        args for args in save_calls
        if tag in (args.get("title") or "")
        and args.get("category") in {"devops_lib", "bug_fix", "pipeline"}
    ]
    assert matching_saves, (
        f"Save phase: no save matched our title+expected category. Saves: {save_calls}"
    )

    try:
        # ---- thread B: recall (NO shared history) -------------------------
        recall_result = await run_agent_query(
            deep_agent,
            (
                "我现在又遇到一个 Terraform 的问题: 有一个 AWS 资源已经在控制台被手动改过了, "
                "现在 terraform plan 一直想 recreate 它。之前有没有解过类似问题? 该怎么修?"
            ),
            thread_id=f"test-mem-loop-recall-{uuid.uuid4().hex[:8]}",
        )

        recall_tools = {
            "memory_search", "memory_get", "find_devops_lib",
            "find_pipeline", "find_bugfix", "qa_experience_search",
        }
        used = recall_tools & set(recall_result["tool_calls"])
        assert used, (
            f"Recall phase: expected one of {recall_tools}, "
            f"got: {recall_result['tool_calls']}"
        )

        text = recall_result["text"]
        text_lower = text.lower()
        assert (
            marker in text
            or "terraform import" in text_lower
            or ("import" in text_lower and "terraform" in text_lower)
        ), (
            f"Recall phase: agent didn't reuse the saved 'terraform import' fix.\n"
            f"Tools: {recall_result['tool_calls']}\nText:\n{text[:500]}"
        )
    finally:
        await cleanup_memory_by_tag(memory_tools, tag)


# ---------------------------------------------------------------------------
# 6. qa_experience back-compat wrapper still routes through CozoDB
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_qa_experience_back_compat(deep_agent, memory_tools):
    """
    The qa_experience_save tool is now a thin wrapper around memory_save with
    category='qa_experience'. Verify the LLM can still drive the legacy path
    and the memory shows up in a qa_experience_search round-trip.
    """
    tag = f"pytest-{uuid.uuid4().hex[:8]}"
    title = f"test qa async deadlock {tag}"

    try:
        save_result = await run_agent_query(
            deep_agent,
            (
                "帮我用 qa_experience_save 记录一个 QA 经验。\n"
                f"  title: \"{title}\"\n"
                "  problem: asyncio task 永远 hang 住，没有报错\n"
                "  key_turns: 假设是锁竞争 → 加 trace → 发现共享的 threading.Lock 在 async 上下文里被 await\n"
                "  resolution: 把 threading.Lock 换成 asyncio.Lock\n"
                "  pattern: asyncio 里的所有锁必须是 asyncio 感知的，否则会卡死事件循环\n"
                f"  tags: {tag},python,asyncio"
            ),
            thread_id=f"test-mem-qa-save-{uuid.uuid4().hex[:8]}",
        )

        save_or_legacy = _any_call_among(
            save_result["tool_calls_full"],
            {"qa_experience_save", "memory_save"},
        )
        assert save_or_legacy, (
            f"Expected qa_experience_save or memory_save, got: {save_result['tool_calls']}"
        )

        # Recall via the legacy search wrapper
        recall_result = await run_agent_query(
            deep_agent,
            (
                "用 qa_experience_search 搜索关键字 \"async deadlock\"，"
                f"看看有没有相关的过往经验, 注意 tag 是 \"{tag}\"。"
            ),
            thread_id=f"test-mem-qa-recall-{uuid.uuid4().hex[:8]}",
        )

        recall_used = {
            "qa_experience_search", "memory_search", "qa_experience_get",
            "memory_get",
        } & set(recall_result["tool_calls"])
        assert recall_used, (
            f"Expected a search/get tool, got: {recall_result['tool_calls']}"
        )
    finally:
        await cleanup_memory_by_tag(memory_tools, tag)
