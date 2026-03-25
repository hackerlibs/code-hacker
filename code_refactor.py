#!/usr/bin/env python3
"""
Code Refactor MCP Server — 自动重构与结构化 Diff 工具。

自包含，所有核心逻辑在 lib/ 目录中。提供：
- 自动重构：拆分长函数、拆分大文件
- ydiff 结构化 AST 级别 diff（文件对比、commit 对比、git ref 对比）
"""

import os
import sys
import subprocess
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# 将 lib/ 加入搜索路径
_LIB_DIR = str(Path(__file__).parent / "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

# ─── 初始化 MCP Server ─────────────────────────────────────────────────────
mcp = FastMCP(name="code-refactor", host='localhost', port=8006)


# ═══════════════════════════════════════════════════════════════════════════
#  MCP Tools — 自动重构
# ═══════════════════════════════════════════════════════════════════════════

def _git_commit(project_dir: str, message: str) -> tuple[bool, str]:
    """在项目目录执行 git add -A && git commit。返回 (成功, 输出信息)。"""
    try:
        subprocess.run(
            ["git", "add", "-A"], cwd=project_dir,
            capture_output=True, text=True, timeout=30, check=True,
        )
        result = subprocess.run(
            ["git", "commit", "-m", message], cwd=project_dir,
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        # nothing to commit is not an error
        if "nothing to commit" in result.stdout:
            return True, "nothing to commit"
        return False, result.stderr.strip()
    except Exception as e:
        return False, str(e)


@mcp.tool()
async def auto_refactor(
    project_dir: str,
    apply: bool = False,
    backup: bool = True,
    auto_commit: bool = True,
    max_func_lines: int = 30,
    max_file_lines: int = 400,
) -> str:
    """Automatically refactor a Python project: split long functions and large files.
    Preview mode by default (apply=False). Set apply=True to execute.

    When auto_commit=True (default), creates separate git commits for reviewer-friendly history:
      1. Mechanical changes (move functions/classes between files) → commit tagged #not-need-review
      2. Logic changes (split/restructure functions) → commit that needs human review

    Args:
        project_dir: Absolute path to the Python project directory
        apply: Execute refactoring (default: False, preview only)
        backup: Backup originals as .bak (default: True)
        auto_commit: Auto-create separated git commits (default: True)
        max_func_lines: Split functions longer than this (default: 30)
        max_file_lines: Split files longer than this (default: 400)
    """
    path = Path(project_dir)
    if not path.is_dir():
        return f"Error: Directory does not exist: {project_dir}"

    try:
        import refactor_auto
        refactor_auto.MAX_FUNC_LINES = max_func_lines
        refactor_auto.MAX_FILE_LINES = max_file_lines
    except ImportError:
        return "Error: refactor_auto module not found in lib/"

    actions = refactor_auto.analyze_project(project_dir)
    if not actions:
        return "代码结构良好，无需重构。"

    func_splits = [a for a in actions if a.kind == "split_func"]
    file_splits = [a for a in actions if a.kind == "split_file"]

    lines = [
        f"=== 自动重构{'执行' if apply else '预览'} ===",
        f"项目: {path.resolve()}",
        f"函数拆分: {len(func_splits)} | 文件拆分: {len(file_splits)}",
    ]

    for a in func_splits:
        lines.append(f"\n{a.description}")
        for d in a.details:
            lines.append(f"  {d}")

    for a in file_splits:
        lines.append(f"\n{a.description}")
        for d in a.details:
            lines.append(f"  {d}")

    if apply:
        lines.append(f"\n--- 执行结果 ---")

        # ── Phase 1: 文件拆分 (机械性移动，不改逻辑) ──
        file_ok_count = 0
        if file_splits:
            lines.append(f"\n[Phase 1] 文件拆分 — 移动函数/类到新文件 (机械性变更)")
            for a in file_splits:
                try:
                    refactor_auto.apply_file_split(a, project_dir, backup=backup)
                    lines.append(f"  [OK] {a.description}")
                    file_ok_count += 1
                except Exception as e:
                    lines.append(f"  [FAIL] {a.description}: {e}")

            if auto_commit and file_ok_count > 0:
                moved_items = []
                for a in file_splits:
                    if a.file_plan:
                        for mod in a.file_plan.new_modules:
                            moved_items.append(mod.filename)
                summary = ", ".join(moved_items[:5])
                if len(moved_items) > 5:
                    summary += f" 等 {len(moved_items)} 个模块"
                commit_msg = (
                    f"refactor: move code to separate modules #not-need-review\n\n"
                    f"机械性移动，无逻辑变更，可跳过 review。\n"
                    f"拆分文件: {summary}"
                )
                ok, out = _git_commit(project_dir, commit_msg)
                if ok:
                    lines.append(f"  [GIT] ✓ commit #not-need-review: {out[:80]}")
                else:
                    lines.append(f"  [GIT] ✗ commit 失败: {out[:120]}")

        # ── Phase 2: 函数拆分 (逻辑变更，需要 review) ──
        func_ok_count = 0
        if func_splits:
            lines.append(f"\n[Phase 2] 函数拆分 — 拆分长函数为子函数 (需要 review)")
            for a in func_splits:
                try:
                    refactor_auto.apply_func_split(a, project_dir, backup=backup)
                    lines.append(f"  [OK] {a.description}")
                    func_ok_count += 1
                except Exception as e:
                    lines.append(f"  [FAIL] {a.description}: {e}")

            if auto_commit and func_ok_count > 0:
                split_names = []
                for a in func_splits:
                    if a.func_plan:
                        name = a.func_plan.func_name
                        if a.func_plan.class_name:
                            name = f"{a.func_plan.class_name}.{name}"
                        split_names.append(name)
                summary = ", ".join(split_names[:5])
                if len(split_names) > 5:
                    summary += f" 等 {len(split_names)} 个函数"
                commit_msg = (
                    f"refactor: split long functions #need-review\n\n"
                    f"逻辑性拆分，请 review 提取的子函数是否正确。\n"
                    f"拆分函数: {summary}"
                )
                ok, out = _git_commit(project_dir, commit_msg)
                if ok:
                    lines.append(f"  [GIT] ✓ commit #need-review: {out[:80]}")
                else:
                    lines.append(f"  [GIT] ✗ commit 失败: {out[:120]}")

        # ── 汇总 ──
        if auto_commit:
            lines.append(f"\n--- Git 提交策略 ---")
            lines.append(f"  #not-need-review: 文件拆分 (纯移动，reviewer 可跳过)")
            lines.append(f"  #need-review: 函数拆分 (逻辑变更，需要人工审核)")
    else:
        lines.append(f"\n提示: 设置 apply=True 执行重构")
        if file_splits and func_splits:
            lines.append(f"  执行时将自动分两个 commit:")
            lines.append(f"    1. 文件拆分 → #not-need-review (纯移动)")
            lines.append(f"    2. 函数拆分 → #need-review (逻辑变更)")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
#  MCP Tools — ydiff 结构化 Diff
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def ydiff_files(file_path1: str, file_path2: str, output_path: str = "") -> str:
    """Structural AST-level diff of two Python files. Unlike line-based diff,
    this understands code structure — detects moved functions, semantic changes.
    Generates interactive side-by-side HTML with click-to-navigate highlighting.

    Args:
        file_path1: Path to the old Python file
        file_path2: Path to the new Python file
        output_path: Output HTML path (default: auto-generated)
    """
    for fp in (file_path1, file_path2):
        if not Path(fp).is_file():
            return f"Error: File not found: {fp}"

    try:
        import ydiff_python
    except ImportError:
        return "Error: ydiff_python module not found in lib/"

    try:
        text1 = Path(file_path1).read_text(encoding="utf-8", errors="replace")
        text2 = Path(file_path2).read_text(encoding="utf-8", errors="replace")

        node1 = ydiff_python.parse_python(text1)
        node2 = ydiff_python.parse_python(text2)
        changes = ydiff_python.diff(node1, node2)
        out = ydiff_python.htmlize(changes, file_path1, file_path2, text1, text2)

        if output_path and output_path != out:
            Path(out).rename(output_path)
            out = output_path

        return (f"Structural diff 报告已生成: {out}\n"
                f"在浏览器中打开查看交互式对比。\n"
                f"  红色: 删除的代码\n"
                f"  绿色: 新增的代码\n"
                f"  灰色链接: 匹配/移动的代码（点击跳转）")
    except SyntaxError as e:
        return f"Error: Python 语法错误: {e}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def ydiff_commit(project_dir: str, commit_id: str, output_path: str = "") -> str:
    """Structural diff report for a git commit. Analyzes all changed Python files
    using AST-level comparison. Produces multi-file HTML with file navigator sidebar.

    Args:
        project_dir: Path to the git repository
        commit_id: Git commit hash (full or short)
        output_path: Output HTML path (default: commit-<hash>.html)
    """
    path = Path(project_dir)
    if not path.is_dir():
        return f"Error: Directory does not exist: {project_dir}"

    try:
        import ydiff_python
    except ImportError:
        return "Error: ydiff_python module not found in lib/"

    try:
        out = ydiff_python.diff_commit(project_dir, commit_id, output_path or None)
        return (f"Commit diff 报告: {out}\n"
                f"功能:\n"
                f"  文件导航侧栏 (M/A/D/R 状态)\n"
                f"  左红右绿对比面板\n"
                f"  点击匹配代码跳转对应位置")
    except RuntimeError as e:
        return f"Git error: {e}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def ydiff_git_changes(project_dir: str, base: str = "HEAD~1", output_path: str = "") -> str:
    """Structural diff of all Python files changed between two git refs.
    Useful for reviewing a branch's changes or recent commits.

    Args:
        project_dir: Path to the git repository
        base: Base git ref to compare against (default: HEAD~1, can be branch name or commit)
        output_path: Output HTML path (default: auto-generated)
    """
    path = Path(project_dir)
    if not path.is_dir():
        return f"Error: Directory does not exist: {project_dir}"

    try:
        import ydiff_python
    except ImportError:
        return "Error: ydiff_python module not found in lib/"

    # Get list of changed Python files
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", base, "--", "*.py"],
            cwd=project_dir, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return f"Git error: {result.stderr}"

        changed_files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
        if not changed_files:
            return f"No Python files changed between {base} and HEAD."

    except Exception as e:
        return f"Error running git: {e}"

    reports = []
    for rel_file in changed_files:
        full_path = os.path.join(project_dir, rel_file)
        if not Path(full_path).is_file():
            continue

        # Get old version
        try:
            old_result = subprocess.run(
                ["git", "show", f"{base}:{rel_file}"],
                cwd=project_dir, capture_output=True, text=True, timeout=10,
            )
            old_text = old_result.stdout if old_result.returncode == 0 else ""
        except Exception:
            old_text = ""

        new_text = Path(full_path).read_text(encoding="utf-8", errors="replace")

        if not old_text and not new_text:
            continue

        try:
            node_old = ydiff_python.parse_python(old_text) if old_text else ydiff_python.Node("Module", 0, 0, [])
            node_new = ydiff_python.parse_python(new_text) if new_text else ydiff_python.Node("Module", 0, 0, [])
            changes = ydiff_python.diff(node_old, node_new)

            ins = sum(1 for c in changes if c.old is None)
            dels = sum(1 for c in changes if c.new is None)
            moves = sum(1 for c in changes if c.type == 'mov' and c.cost > 0)

            reports.append(f"  {rel_file}: +{ins} -{dels}" + (f" ~{moves} moved" if moves else ""))
        except Exception as e:
            reports.append(f"  {rel_file}: parse error ({e})")

    lines = [
        f"=== Structural Diff: {base}..HEAD ===",
        f"项目: {path.resolve()}",
        f"变更文件: {len(changed_files)}",
        "",
    ] + reports

    # Generate combined HTML report
    try:
        out = ydiff_python.diff_commit(project_dir, "HEAD", output_path or None)
        lines.append(f"\nHTML 报告: {out}")
    except Exception:
        lines.append(f"\n(HTML 报告生成跳过)")

    return "\n".join(lines)


# ─── 入口 ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="streamable-http")
