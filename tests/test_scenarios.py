"""
LLM-powered integration tests for Code Hack AI Expert.

Real scenarios that exercise the DeepAgent + 7 MCP servers end-to-end.
Each test sends a natural language request and verifies:
  1. The agent invoked the expected MCP tools
  2. The response contains meaningful content
  3. No errors in tool results

Requirements:
    - All 7 MCP servers running (bash start_servers.sh)
    - OPENROUTER_API_KEY set
    - Run: NO_PROXY=localhost,127.0.0.1 uv run pytest tests/test_scenarios.py -v -s
"""

import os
import uuid
import pytest
import pytest_asyncio

from conftest import run_agent_query

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Real commit IDs from this repo's git history
COMMIT_INIT = "a4e3244"       # init Add web_app.py
COMMIT_MULTI = "0a3b145"      # Add multi_project.py
COMMIT_QA = "e95c070"         # WIP: QA Experience Recording
COMMIT_YDIFF = "ac1aa9d"      # Mac test ydiff_commit successful


# ─── Scenario 1: Code Review with ydiff ─────────────────────────────────

@pytest.mark.asyncio
async def test_ydiff_commit_review(deep_agent):
    """
    Scenario: 帮我审核一下commit的代码生成ydiff报告
    Expected: Agent uses ydiff_commit and/or git_show to analyze the commit.
    """
    result = await run_agent_query(
        deep_agent,
        f"帮我审核一下 commit {COMMIT_INIT} 的代码，"
        f"项目路径是 {PROJECT_DIR}，生成结构化diff报告。",
        thread_id=f"test-ydiff-{uuid.uuid4().hex[:8]}",
    )

    # Should invoke structural diff or git tools
    diff_tools = {"ydiff_commit", "ydiff_git_changes", "git_show", "git_diff", "git_log"}
    used = set(result["tool_calls"])
    assert used & diff_tools, f"Expected diff/git tools, got: {result['tool_calls']}"

    # Response should mention the commit or code changes
    text = result["text"].lower()
    assert any(kw in text for kw in ["commit", "diff", "变更", "修改", "web_app", "添加"]), \
        f"Response doesn't discuss the commit: {result['text'][:200]}"


# ─── Scenario 2: Project Health Score ────────────────────────────────────

@pytest.mark.asyncio
async def test_project_health_score(deep_agent):
    """
    Scenario: 帮我评估一下这个项目的代码质量
    Expected: Agent uses health_score and/or review_project.
    """
    result = await run_agent_query(
        deep_agent,
        f"帮我评估一下 {PROJECT_DIR} 项目的代码健康度，给出评分和主要问题。",
        thread_id=f"test-health-{uuid.uuid4().hex[:8]}",
    )

    review_tools = {"health_score", "review_project", "review_file", "find_long_functions", "find_complex_functions"}
    used = set(result["tool_calls"])
    assert used & review_tools, f"Expected review tools, got: {result['tool_calls']}"

    # Response should contain a score or quality assessment
    text = result["text"].lower()
    assert any(kw in text for kw in ["score", "health", "评分", "质量", "问题", "建议"]), \
        f"Response doesn't discuss quality: {result['text'][:200]}"


# ─── Scenario 3: Git History Investigation ───────────────────────────────

@pytest.mark.asyncio
async def test_git_history_investigation(deep_agent):
    """
    Scenario: 帮我查一下这个项目最近的提交历史
    Expected: Agent uses git_log, potentially git_show.
    """
    result = await run_agent_query(
        deep_agent,
        f"帮我查一下 {PROJECT_DIR} 最近5次提交的历史，"
        "包括每次提交修改了哪些文件。",
        thread_id=f"test-gitlog-{uuid.uuid4().hex[:8]}",
    )

    git_tools = {"git_log", "git_show", "git_diff"}
    used = set(result["tool_calls"])
    assert used & git_tools, f"Expected git tools, got: {result['tool_calls']}"

    # Should mention real commit messages
    text = result["text"]
    assert any(kw in text for kw in ["web_app", "multi_project", "MIT", "init", "commit"]), \
        f"Response doesn't mention real commits: {result['text'][:200]}"


# ─── Scenario 4: Code Intelligence — Analyze Python File ────────────────

@pytest.mark.asyncio
async def test_analyze_python_file(deep_agent):
    """
    Scenario: 帮我分析一下web_app.py的代码结构
    Expected: Agent uses analyze_python_file or extract_symbols.
    """
    result = await run_agent_query(
        deep_agent,
        f"帮我分析一下 {PROJECT_DIR}/web_app.py 的代码结构，"
        "包含哪些函数和类，列出主要入口点。",
        thread_id=f"test-analyze-{uuid.uuid4().hex[:8]}",
    )

    intel_tools = {"analyze_python_file", "extract_symbols", "read_file", "read_file_lines"}
    used = set(result["tool_calls"])
    assert used & intel_tools, f"Expected code intel tools, got: {result['tool_calls']}"

    # Should mention key functions in web_app.py
    text = result["text"]
    assert any(fn in text for fn in ["init_agent", "get_llm_model", "websocket_endpoint", "lifespan"]), \
        f"Response doesn't mention key functions: {result['text'][:200]}"


# ─── Scenario 5: Cross-File Search ──────────────────────────────────────

@pytest.mark.asyncio
async def test_search_code_pattern(deep_agent):
    """
    Scenario: 帮我搜索所有MCP服务器里用到FastMCP的地方
    Expected: Agent uses search_files_ag to find patterns across files.
    """
    result = await run_agent_query(
        deep_agent,
        f"帮我在 {PROJECT_DIR} 搜索所有Python文件中用到 FastMCP 的地方，"
        "列出文件名和行号。",
        thread_id=f"test-search-{uuid.uuid4().hex[:8]}",
    )

    search_tools = {"search_files_ag", "find_files", "workspace_search"}
    used = set(result["tool_calls"])
    assert used & search_tools, f"Expected search tools, got: {result['tool_calls']}"

    # Should find FastMCP in multiple server files
    text = result["text"]
    assert any(f in text for f in ["filesystem.py", "git_tools.py", "code_intel.py", "FastMCP"]), \
        f"Response doesn't mention server files: {result['text'][:200]}"


# ─── Scenario 6: Dependency Graph ───────────────────────────────────────

@pytest.mark.asyncio
async def test_dependency_graph(deep_agent):
    """
    Scenario: 帮我看一下web_app.py的依赖关系图
    Expected: Agent uses dependency_graph or project_overview.
    """
    result = await run_agent_query(
        deep_agent,
        f"帮我分析 {PROJECT_DIR}/web_app.py 的依赖关系，"
        "它import了什么，被谁引用。",
        thread_id=f"test-deps-{uuid.uuid4().hex[:8]}",
    )

    dep_tools = {"dependency_graph", "find_references", "project_overview", "analyze_python_file", "read_file"}
    used = set(result["tool_calls"])
    assert used & dep_tools, f"Expected dep/intel tools, got: {result['tool_calls']}"

    # Should mention key imports
    text = result["text"]
    assert any(kw in text for kw in ["fastapi", "deepagents", "langchain", "mcp", "import"]), \
        f"Response doesn't discuss dependencies: {result['text'][:200]}"


# ─── Scenario 7: Multi-Project Workspace Registration ───────────────────

@pytest.mark.asyncio
async def test_workspace_register_and_search(deep_agent):
    """
    Scenario: 注册项目到工作区然后做跨项目搜索
    Expected: Agent uses workspace_add and workspace_search.
    """
    result = await run_agent_query(
        deep_agent,
        f"把 {PROJECT_DIR} 注册到工作区，别名叫 code-hacker，"
        "然后在工作区里搜索所有包含 'create_deep_agent' 的文件。",
        thread_id=f"test-workspace-{uuid.uuid4().hex[:8]}",
    )

    ws_tools = {"workspace_add", "workspace_search", "workspace_list", "workspace_find_files"}
    used = set(result["tool_calls"])
    assert used & ws_tools, f"Expected workspace tools, got: {result['tool_calls']}"


# ─── Scenario 8: Memory Store — Save and Retrieve ───────────────────────

@pytest.mark.asyncio
async def test_memory_save_and_recall(deep_agent):
    """
    Scenario: 记住一个项目信息然后回忆
    Expected: Agent uses memory_save and memory_get/memory_search.
    """
    test_key = f"test-info-{uuid.uuid4().hex[:6]}"
    result = await run_agent_query(
        deep_agent,
        f"帮我记住一个信息：key是'{test_key}'，"
        f"内容是'Code Hack项目有7个MCP服务器，66个工具'。"
        f"然后立即查询key '{test_key}' 确认保存成功。",
        thread_id=f"test-memory-{uuid.uuid4().hex[:8]}",
    )

    mem_tools = {"memory_save", "memory_get", "memory_search", "memory_list"}
    used = set(result["tool_calls"])
    assert used & mem_tools, f"Expected memory tools, got: {result['tool_calls']}"

    # Should confirm save
    text = result["text"]
    assert any(kw in text for kw in ["保存", "记住", "save", "success", "成功", "7", "MCP"]), \
        f"Response doesn't confirm save: {result['text'][:200]}"


# ─── Scenario 9: Jenkinsfile Pipeline Generation ────────────────────────

@pytest.mark.asyncio
async def test_jenkinsfile_pipeline_generation(deep_agent):
    """
    Scenario: 帮我完成一个Jenkinsfile的管道发布
    Expected: Agent uses filesystem tools to create/edit a Jenkinsfile.
    """
    result = await run_agent_query(
        deep_agent,
        f"帮我在 /tmp/test-pipeline-{uuid.uuid4().hex[:8]} 目录下创建一个Jenkinsfile，"
        "实现一个标准的CI/CD管道：包含Build、Test、Deploy三个stage，"
        "使用pipeline lib，agent使用 'any'。只需要创建文件即可。",
        thread_id=f"test-jenkins-{uuid.uuid4().hex[:8]}",
    )

    fs_tools = {"write_file", "create_directory", "execute_command", "edit_file"}
    used = set(result["tool_calls"])
    assert used & fs_tools, f"Expected filesystem tools, got: {result['tool_calls']}"

    # Response should mention pipeline stages
    text = result["text"]
    assert any(kw in text for kw in ["Jenkinsfile", "pipeline", "Build", "Test", "Deploy", "stage"]), \
        f"Response doesn't discuss pipeline: {result['text'][:200]}"


# ─── Scenario 10: Find Long/Complex Functions ───────────────────────────

@pytest.mark.asyncio
async def test_find_complex_functions(deep_agent):
    """
    Scenario: 帮我找出项目中最复杂的函数
    Expected: Agent uses find_complex_functions or find_long_functions.
    """
    result = await run_agent_query(
        deep_agent,
        f"帮我找出 {PROJECT_DIR} 项目中最长和最复杂的函数，"
        "列出前5个，包含文件名和行数。",
        thread_id=f"test-complex-{uuid.uuid4().hex[:8]}",
    )

    review_tools = {"find_long_functions", "find_complex_functions", "review_project", "health_score"}
    used = set(result["tool_calls"])
    assert used & review_tools, f"Expected review tools, got: {result['tool_calls']}"


# ─── Scenario 11: Git Blame Investigation ────────────────────────────────

@pytest.mark.asyncio
async def test_git_blame_investigation(deep_agent):
    """
    Scenario: 帮我查看web_app.py的init_agent函数是谁写的
    Expected: Agent uses git_blame on the specific file/lines.
    """
    result = await run_agent_query(
        deep_agent,
        f"帮我用git blame查看 {PROJECT_DIR}/web_app.py 中 init_agent 函数是谁在什么时候写的。",
        thread_id=f"test-blame-{uuid.uuid4().hex[:8]}",
    )

    git_tools = {"git_blame", "git_log", "git_show", "read_file", "read_file_lines", "search_files_ag"}
    used = set(result["tool_calls"])
    assert used & git_tools, f"Expected git/read tools, got: {result['tool_calls']}"

    # Should mention init_agent
    text = result["text"]
    assert "init_agent" in text, f"Response doesn't mention init_agent: {result['text'][:200]}"


# ─── Scenario 12: QA Experience Recording ────────────────────────────────

@pytest.mark.asyncio
async def test_qa_experience_workflow(deep_agent):
    """
    Scenario: 记录一次QA经验
    Expected: Agent uses qa_experience_save.
    """
    result = await run_agent_query(
        deep_agent,
        "帮我记录一个QA经验：标题是'MCP服务器连接测试'，"
        "问题是'本地代理拦截了localhost连接导致502'，"
        "解决方案是'设置NO_PROXY=localhost,127.0.0.1'，"
        "标签是 ['mcp', 'proxy', 'debugging']。",
        thread_id=f"test-qa-{uuid.uuid4().hex[:8]}",
    )

    qa_tools = {"qa_experience_save", "qa_experience_search", "qa_experience_get"}
    used = set(result["tool_calls"])
    assert used & qa_tools, f"Expected QA tools, got: {result['tool_calls']}"


# ─── Scenario 13: Project Overview ──────────────────────────────────────

@pytest.mark.asyncio
async def test_project_overview(deep_agent):
    """
    Scenario: 帮我看一下项目整体结构
    Expected: Agent uses project_overview or list_directory.
    """
    result = await run_agent_query(
        deep_agent,
        f"帮我看一下 {PROJECT_DIR} 的项目整体结构，"
        "包括目录树、文件分布和语言统计。",
        thread_id=f"test-overview-{uuid.uuid4().hex[:8]}",
    )

    overview_tools = {"project_overview", "list_directory", "find_files"}
    used = set(result["tool_calls"])
    assert used & overview_tools, f"Expected overview tools, got: {result['tool_calls']}"

    # Should mention key files
    text = result["text"]
    assert any(f in text for f in ["web_app.py", ".py", "Python", "python"]), \
        f"Response doesn't mention project files: {result['text'][:200]}"
