#!/usr/bin/env python3
"""
Code Review MCP Server — 代码质量审查工具。

自包含，无外部依赖。提供：
- 项目代码质量扫描与健康评分
- 函数/文件级深度分析
- 超长函数和高复杂度函数排行
- 文件重组建议
- 新旧代码对比审查
"""

import os
import sys
import ast
import json
import subprocess
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# 将 lib/ 加入搜索路径（ydiff 引擎）
_LIB_DIR = str(Path(__file__).parent / "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

# ─── 初始化 MCP Server ─────────────────────────────────────────────────────
mcp = FastMCP(name="code-review", host='localhost', port=8005)

# ─── 默认阈值 ──────────────────────────────────────────────────────────────
DEFAULT_THRESHOLDS = {
    "max_func_lines": 30,
    "max_func_params": 5,
    "max_local_vars": 8,
    "max_complexity": 10,
    "max_file_lines": 400,
    "max_classes_per_file": 4,
    "max_funcs_per_file": 10,
    "min_similar_prefix": 2,
}

EXCLUDE_DIRS = {
    ".venv", "venv", "__pycache__", ".git", "node_modules",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".eggs", "env",
}


# ─── 数据结构 ──────────────────────────────────────────────────────────────
@dataclass
class Issue:
    file: str
    line: int
    category: str
    severity: str
    title: str
    detail: str
    suggestion: str


@dataclass
class ReorgSuggestion:
    source_file: str
    items: list
    suggested_file: str
    reason: str


@dataclass
class FuncInfo:
    name: str
    file: str
    line: int
    end_line: int
    num_lines: int
    num_params: int
    local_vars: list
    complexity: int
    decorators: list
    is_method: bool
    class_name: Optional[str]


@dataclass
class FileInfo:
    path: str
    total_lines: int
    classes: list
    top_functions: list
    imports: list


@dataclass
class AnalysisResult:
    files_analyzed: int = 0
    total_lines: int = 0
    issues: list = field(default_factory=list)
    reorg_suggestions: list = field(default_factory=list)
    file_infos: list = field(default_factory=list)
    func_infos: list = field(default_factory=list)


# ─── AST 分析器 ────────────────────────────────────────────────────────────
class CodeAnalyzer(ast.NodeVisitor):

    def __init__(self, filepath: str, source: str):
        self.filepath = filepath
        self.source = source
        self.functions: list[FuncInfo] = []
        self.classes: list[str] = []
        self.top_functions: list[str] = []
        self.imports: list[str] = []
        self._class_stack: list[str] = []

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        module = node.module or ""
        for alias in node.names:
            self.imports.append(f"{module}.{alias.name}")
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.classes.append(node.name)
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node):
        self._analyze_function(node)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def _analyze_function(self, node):
        is_method = len(self._class_stack) > 0
        class_name = self._class_stack[-1] if is_method else None
        if not is_method:
            self.top_functions.append(node.name)

        end_line = node.end_lineno or node.lineno
        num_lines = end_line - node.lineno + 1

        args = node.args
        all_args = args.args + args.posonlyargs + args.kwonlyargs
        param_names = [a.arg for a in all_args]
        if is_method and param_names and param_names[0] in ("self", "cls"):
            param_names = param_names[1:]
        num_params = len(param_names)
        if args.vararg:
            num_params += 1
        if args.kwarg:
            num_params += 1

        local_vars = self._collect_local_vars(node)
        complexity = self._calc_complexity(node)

        decorators = []
        for d in node.decorator_list:
            if isinstance(d, ast.Name):
                decorators.append(d.id)
            elif isinstance(d, ast.Attribute):
                decorators.append(d.attr)

        self.functions.append(FuncInfo(
            name=node.name, file=self.filepath, line=node.lineno,
            end_line=end_line, num_lines=num_lines, num_params=num_params,
            local_vars=local_vars, complexity=complexity, decorators=decorators,
            is_method=is_method, class_name=class_name,
        ))

    def _collect_local_vars(self, func_node) -> list[str]:
        vars_found = set()
        for node in ast.walk(func_node):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        vars_found.add(target.id)
                    elif isinstance(target, ast.Tuple):
                        for elt in target.elts:
                            if isinstance(elt, ast.Name):
                                vars_found.add(elt.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                vars_found.add(node.target.id)
        return sorted(vars_found)

    def _calc_complexity(self, node) -> int:
        complexity = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor, ast.ExceptHandler)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
            elif isinstance(child, ast.comprehension):
                complexity += 1 + len(child.ifs)
        return complexity


# ─── 核心分析引擎 ──────────────────────────────────────────────────────────
def _scan_project(root_dir: str, thresholds: dict) -> AnalysisResult:
    result = AnalysisResult()
    root = Path(root_dir).resolve()

    if not root.is_dir():
        return result

    py_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for f in filenames:
            if f.endswith(".py"):
                py_files.append(os.path.join(dirpath, f))

    for filepath in sorted(py_files):
        try:
            source = Path(filepath).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        rel_path = os.path.relpath(filepath, root)
        lines = source.splitlines()
        result.total_lines += len(lines)
        result.files_analyzed += 1

        try:
            tree = ast.parse(source, filename=filepath)
        except SyntaxError:
            continue

        analyzer = CodeAnalyzer(rel_path, source)
        analyzer.visit(tree)

        fi = FileInfo(
            path=rel_path, total_lines=len(lines),
            classes=analyzer.classes, top_functions=analyzer.top_functions,
            imports=analyzer.imports,
        )
        result.file_infos.append(fi)
        result.func_infos.extend(analyzer.functions)

        _check_file_issues(fi, result, thresholds)
        for func in analyzer.functions:
            _check_func_issues(func, result, thresholds)

    _generate_reorg_suggestions(result, thresholds)
    return result


def _check_file_issues(fi: FileInfo, result: AnalysisResult, t: dict):
    if fi.total_lines > t["max_file_lines"]:
        result.issues.append(Issue(
            file=fi.path, line=1, category="long_file", severity="medium",
            title=f"文件过长 ({fi.total_lines} 行)",
            detail=f"超过阈值 {t['max_file_lines']} 行。",
            suggestion="建议按功能拆分为多个模块文件。",
        ))
    if len(fi.classes) > t["max_classes_per_file"]:
        result.issues.append(Issue(
            file=fi.path, line=1, category="too_many_classes", severity="medium",
            title=f"单文件类过多 ({len(fi.classes)} 个)",
            detail=f"类: {', '.join(fi.classes)}",
            suggestion="建议每个类放入独立文件中。",
        ))
    if len(fi.top_functions) > t["max_funcs_per_file"]:
        result.issues.append(Issue(
            file=fi.path, line=1, category="too_many_funcs", severity="low",
            title=f"单文件函数过多 ({len(fi.top_functions)} 个)",
            detail=f"包含 {len(fi.top_functions)} 个顶层函数。",
            suggestion="建议按职责将函数分组到不同模块。",
        ))


def _check_func_issues(func: FuncInfo, result: AnalysisResult, t: dict):
    name = f"{func.class_name}.{func.name}" if func.class_name else func.name

    if func.num_lines > t["max_func_lines"]:
        result.issues.append(Issue(
            file=func.file, line=func.line, category="long_func",
            severity="high" if func.num_lines > t["max_func_lines"] * 2 else "medium",
            title=f"函数过长: {name} ({func.num_lines} 行)",
            detail=f"第{func.line}-{func.end_line}行，阈值 {t['max_func_lines']} 行。",
            suggestion="建议拆分为多个小函数，每个只做一件事。",
        ))
    if func.num_params > t["max_func_params"]:
        result.issues.append(Issue(
            file=func.file, line=func.line, category="too_many_params", severity="medium",
            title=f"参数过多: {name} ({func.num_params} 个)",
            detail=f"阈值 {t['max_func_params']}。",
            suggestion="建议用 dataclass 封装参数，或拆分函数职责。",
        ))
    if len(func.local_vars) > t["max_local_vars"]:
        result.issues.append(Issue(
            file=func.file, line=func.line, category="extract_vars", severity="medium",
            title=f"局部变量过多: {name} ({len(func.local_vars)} 个)",
            detail=f"变量: {', '.join(func.local_vars[:15])}",
            suggestion="建议将相关变量和逻辑提取为独立函数或数据类。",
        ))
    if func.complexity > t["max_complexity"]:
        result.issues.append(Issue(
            file=func.file, line=func.line, category="high_complexity",
            severity="high" if func.complexity > t["max_complexity"] * 2 else "medium",
            title=f"圈复杂度过高: {name} (复杂度 {func.complexity})",
            detail=f"阈值 {t['max_complexity']}。",
            suggestion="使用早返回、策略模式或将条件分支提取为子函数。",
        ))


def _generate_reorg_suggestions(result: AnalysisResult, t: dict):
    file_funcs: dict[str, list[FuncInfo]] = defaultdict(list)
    for f in result.func_infos:
        if not f.is_method:
            file_funcs[f.file].append(f)

    for filepath, funcs in file_funcs.items():
        if len(funcs) < t["min_similar_prefix"] * 2:
            continue
        prefix_groups: dict[str, list[str]] = defaultdict(list)
        for f in funcs:
            parts = f.name.split("_")
            if len(parts) >= 2 and not f.name.startswith("_"):
                prefix_groups[parts[0]].append(f.name)
        for prefix, names in prefix_groups.items():
            if len(names) >= t["min_similar_prefix"]:
                result.reorg_suggestions.append(ReorgSuggestion(
                    source_file=filepath, items=names,
                    suggested_file=f"{prefix}_utils.py",
                    reason=f"这 {len(names)} 个函数共享前缀 '{prefix}_'，建议提取到独立模块。",
                ))

    for fi in result.file_infos:
        if len(fi.classes) > t["max_classes_per_file"]:
            for cls_name in fi.classes:
                snake = "".join(f"_{c.lower()}" if c.isupper() and i > 0 else c.lower()
                                for i, c in enumerate(cls_name))
                result.reorg_suggestions.append(ReorgSuggestion(
                    source_file=fi.path, items=[cls_name],
                    suggested_file=f"{snake}.py",
                    reason=f"类 {cls_name} 可独立为单独模块。",
                ))


def _calc_health_score(result: AnalysisResult) -> int:
    total = len(result.func_infos) or 1
    high = sum(1 for i in result.issues if i.severity == "high")
    other = len(result.issues) - high
    return max(0, round(100 - (high * 8 + other * 3) * 100 / (total * 10)))


def _merge_thresholds(**overrides) -> dict:
    t = DEFAULT_THRESHOLDS.copy()
    for k, v in overrides.items():
        if v is not None and k in t:
            t[k] = v
    return t


# ═══════════════════════════════════════════════════════════════════════════
#  MCP Tools — 代码审查与分析
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def review_project(
    project_dir: str,
    max_func_lines: int = 30,
    max_func_params: int = 5,
    max_complexity: int = 10,
    max_file_lines: int = 400,
) -> str:
    """Scan a Python project for code quality issues and refactoring opportunities.
    Returns health score, issue counts by severity, detailed issue list, and reorg suggestions.

    Args:
        project_dir: Absolute path to the Python project directory
        max_func_lines: Max lines per function before flagging (default: 30)
        max_func_params: Max parameters per function (default: 5)
        max_complexity: Max McCabe cyclomatic complexity (default: 10)
        max_file_lines: Max lines per file (default: 400)
    """
    path = Path(project_dir)
    if not path.is_dir():
        return f"Error: Directory does not exist: {project_dir}"

    t = _merge_thresholds(
        max_func_lines=max_func_lines, max_func_params=max_func_params,
        max_complexity=max_complexity, max_file_lines=max_file_lines,
    )
    result = _scan_project(project_dir, t)
    score = _calc_health_score(result)

    sev_counts = {"high": 0, "medium": 0, "low": 0}
    cat_counts = defaultdict(int)
    for iss in result.issues:
        sev_counts[iss.severity] += 1
        cat_counts[iss.category] += 1

    lines = [
        f"=== Python 代码审查报告 ===",
        f"项目: {path.resolve()}",
        f"健康评分: {score}/100",
        f"",
        f"--- 统计 ---",
        f"文件: {result.files_analyzed} | 代码行: {result.total_lines:,} | 函数: {len(result.func_infos)}",
        f"问题: {len(result.issues)} (高:{sev_counts['high']} 中:{sev_counts['medium']} 低:{sev_counts['low']})",
    ]

    if result.issues:
        lines.append(f"\n--- 问题详情 ---")
        for iss in sorted(result.issues, key=lambda i: ({"high": 0, "medium": 1, "low": 2}[i.severity],)):
            sev_mark = {"high": "[!!!]", "medium": "[!!]", "low": "[!]"}[iss.severity]
            lines.append(f"\n{sev_mark} {iss.title}")
            lines.append(f"  {iss.file}:{iss.line} — {iss.detail}")
            lines.append(f"  建议: {iss.suggestion}")

    if result.reorg_suggestions:
        lines.append(f"\n--- 重组建议 ({len(result.reorg_suggestions)} 条) ---")
        for s in result.reorg_suggestions:
            lines.append(f"  {s.source_file} → {s.suggested_file}: {', '.join(s.items)}")

    return "\n".join(lines)


@mcp.tool()
async def review_file(file_path: str) -> str:
    """Analyze a single Python file: line count, classes, functions ranked by complexity.

    Args:
        file_path: Absolute path to the Python file
    """
    path = Path(file_path)
    if not path.is_file():
        return f"Error: File does not exist: {file_path}"
    if not file_path.endswith(".py"):
        return f"Error: Not a Python file: {file_path}"

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as e:
        return f"Syntax error: {e}"

    analyzer = CodeAnalyzer(str(path), source)
    analyzer.visit(tree)
    total_lines = len(source.splitlines())
    t = DEFAULT_THRESHOLDS

    lines = [
        f"=== 文件分析: {path.name} ===",
        f"路径: {path.resolve()}",
        f"行数: {total_lines}  (阈值: {t['max_file_lines']}){' ⚠' if total_lines > t['max_file_lines'] else ' ✓'}",
        f"类: {len(analyzer.classes)}{' ⚠' if len(analyzer.classes) > t['max_classes_per_file'] else ' ✓'}",
        f"顶层函数: {len(analyzer.top_functions)}{' ⚠' if len(analyzer.top_functions) > t['max_funcs_per_file'] else ' ✓'}",
        f"导入: {len(analyzer.imports)} | 函数/方法: {len(analyzer.functions)}",
    ]

    if analyzer.classes:
        lines.append(f"\n类: {', '.join(analyzer.classes)}")

    if analyzer.functions:
        lines.append(f"\n{'函数名':<35} {'行数':>5} {'参数':>5} {'变量':>5} {'复杂度':>6}")
        lines.append("-" * 62)
        for f in sorted(analyzer.functions, key=lambda f: f.complexity, reverse=True):
            name = f"{f.class_name}.{f.name}" if f.class_name else f.name
            if len(name) > 34:
                name = name[:31] + "..."
            flags = ""
            if f.num_lines > t["max_func_lines"]: flags += "L"
            if f.num_params > t["max_func_params"]: flags += "P"
            if len(f.local_vars) > t["max_local_vars"]: flags += "V"
            if f.complexity > t["max_complexity"]: flags += "C"
            flag_str = f" [{flags}]" if flags else ""
            lines.append(f"{name:<35} {f.num_lines:>5} {f.num_params:>5} {len(f.local_vars):>5} {f.complexity:>6}{flag_str}")
        lines.append(f"\n标记: L=行数超标 P=参数过多 V=变量过多 C=复杂度高")

    return "\n".join(lines)


@mcp.tool()
async def review_function(file_path: str, function_name: str) -> str:
    """Deep analysis of a specific function with refactoring suggestions.

    Args:
        file_path: Absolute path to the Python file
        function_name: Function name (use ClassName.method for methods)
    """
    path = Path(file_path)
    if not path.is_file():
        return f"Error: File not found: {file_path}"
    if not file_path.endswith(".py"):
        return f"Error: Not a Python file"

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as e:
        return f"Syntax error: {e}"

    analyzer = CodeAnalyzer(str(path), source)
    analyzer.visit(tree)

    parts = function_name.split(".")
    target_class = parts[0] if len(parts) > 1 else None
    target_func = parts[-1]

    found = None
    for f in analyzer.functions:
        if f.name == target_func:
            if target_class is None or f.class_name == target_class:
                found = f
                break

    if not found:
        available = [f"{f.class_name}.{f.name}" if f.class_name else f.name
                     for f in analyzer.functions]
        return (f"Function '{function_name}' not found.\n"
                f"Available: {', '.join(available[:30])}")

    f = found
    display = f"{f.class_name}.{f.name}" if f.class_name else f.name
    t = DEFAULT_THRESHOLDS

    lines = [
        f"=== 函数分析: {display} ===",
        f"文件: {file_path}:{f.line}-{f.end_line}",
        f"类型: {'方法' if f.is_method else '函数'}",
        f"",
        f"行数: {f.num_lines} (阈值: {t['max_func_lines']}){' ⚠' if f.num_lines > t['max_func_lines'] else ' ✓'}",
        f"参数: {f.num_params} (阈值: {t['max_func_params']}){' ⚠' if f.num_params > t['max_func_params'] else ' ✓'}",
        f"局部变量: {len(f.local_vars)} (阈值: {t['max_local_vars']}){' ⚠' if len(f.local_vars) > t['max_local_vars'] else ' ✓'}",
        f"复杂度: {f.complexity} (阈值: {t['max_complexity']}){' ⚠' if f.complexity > t['max_complexity'] else ' ✓'}",
    ]

    if f.local_vars:
        lines.append(f"\n局部变量: {', '.join(f.local_vars)}")
    if f.decorators:
        lines.append(f"装饰器: {', '.join(f.decorators)}")

    suggestions = []
    if f.num_lines > t["max_func_lines"]:
        suggestions.append(f"• 函数 {f.num_lines} 行，建议拆分。寻找独立逻辑块提取为子函数。")
    if f.num_params > t["max_func_params"]:
        suggestions.append(f"• 参数过多 ({f.num_params})，建议用 @dataclass 封装。")
    if len(f.local_vars) > t["max_local_vars"]:
        suggestions.append(f"• 变量过多 ({len(f.local_vars)})，建议分组提取为独立函数。")
    if f.complexity > t["max_complexity"]:
        suggestions.append(
            f"• 复杂度 {f.complexity}，建议：\n"
            f"  - 早返回减少嵌套\n"
            f"  - 字典映射替代 if-elif\n"
            f"  - 条件提取为辅助函数"
        )

    if suggestions:
        lines.append(f"\n--- 重构建议 ---")
        lines.extend(suggestions)
    else:
        lines.append(f"\n✓ 各项指标均正常。")

    return "\n".join(lines)


@mcp.tool()
async def health_score(project_dir: str) -> str:
    """Quick 0-100 health score for a Python project with grade and summary.

    Args:
        project_dir: Absolute path to the Python project directory
    """
    path = Path(project_dir)
    if not path.is_dir():
        return f"Error: Directory does not exist: {project_dir}"

    t = _merge_thresholds()
    result = _scan_project(project_dir, t)
    score = _calc_health_score(result)

    sev = {"high": 0, "medium": 0, "low": 0}
    for iss in result.issues:
        sev[iss.severity] += 1

    if score >= 80:
        grade, comment = "A", "优秀"
    elif score >= 60:
        grade, comment = "B", "一般，建议关注高优问题"
    elif score >= 40:
        grade, comment = "C", "需改进"
    else:
        grade, comment = "D", "较差，强烈建议重构"

    return (f"健康评分: {score}/100 ({grade})\n"
            f"评价: {comment}\n"
            f"文件: {result.files_analyzed} | 行: {result.total_lines:,} | 函数: {len(result.func_infos)}\n"
            f"问题: 高 {sev['high']} | 中 {sev['medium']} | 低 {sev['low']}")


@mcp.tool()
async def find_long_functions(project_dir: str, min_lines: int = 30, top_n: int = 20) -> str:
    """Find the longest functions in a Python project, ranked by line count.

    Args:
        project_dir: Absolute path to the Python project directory
        min_lines: Minimum lines to include (default: 30)
        top_n: Max results (default: 20)
    """
    path = Path(project_dir)
    if not path.is_dir():
        return f"Error: Directory does not exist: {project_dir}"

    t = _merge_thresholds(max_func_lines=min_lines)
    result = _scan_project(project_dir, t)

    long = [f for f in sorted(result.func_infos, key=lambda f: f.num_lines, reverse=True)
            if f.num_lines >= min_lines][:top_n]

    if not long:
        return f"未发现超过 {min_lines} 行的函数。共 {len(result.func_infos)} 个函数。"

    lines = [f"=== 超长函数 TOP {len(long)} (≥{min_lines} 行) ===", ""]
    for i, f in enumerate(long, 1):
        name = f"{f.class_name}.{f.name}" if f.class_name else f.name
        lines.append(f"{i:>3}. {name:<40} {f.num_lines:>5} 行  {f.file}:{f.line}")

    return "\n".join(lines)


@mcp.tool()
async def find_complex_functions(project_dir: str, min_complexity: int = 10, top_n: int = 20) -> str:
    """Find functions with highest cyclomatic complexity, ranked.

    Args:
        project_dir: Absolute path to the Python project directory
        min_complexity: Minimum McCabe complexity (default: 10)
        top_n: Max results (default: 20)
    """
    path = Path(project_dir)
    if not path.is_dir():
        return f"Error: Directory does not exist: {project_dir}"

    t = _merge_thresholds(max_complexity=min_complexity)
    result = _scan_project(project_dir, t)

    cx = [f for f in sorted(result.func_infos, key=lambda f: f.complexity, reverse=True)
          if f.complexity >= min_complexity][:top_n]

    if not cx:
        return f"未发现复杂度超过 {min_complexity} 的函数。"

    lines = [f"=== 高复杂度函数 TOP {len(cx)} (≥{min_complexity}) ===", ""]
    for i, f in enumerate(cx, 1):
        name = f"{f.class_name}.{f.name}" if f.class_name else f.name
        lines.append(f"{i:>3}. {name:<40} 复杂度:{f.complexity:>3}  {f.num_lines:>4}行  {f.file}:{f.line}")

    return "\n".join(lines)


@mcp.tool()
async def suggest_reorg(project_dir: str) -> str:
    """Suggest file reorganization for a Python project based on naming patterns and class distribution.

    Args:
        project_dir: Absolute path to the Python project directory
    """
    path = Path(project_dir)
    if not path.is_dir():
        return f"Error: Directory does not exist: {project_dir}"

    result = _scan_project(project_dir, _merge_thresholds())

    if not result.reorg_suggestions:
        return f"文件结构合理，暂无重组建议。({result.files_analyzed} 文件, {len(result.func_infos)} 函数)"

    lines = [f"=== 重组建议 ({len(result.reorg_suggestions)} 条) ===", ""]
    for i, s in enumerate(result.reorg_suggestions, 1):
        lines.append(f"[{i}] {s.source_file} → {s.suggested_file}")
        lines.append(f"    移动: {', '.join(s.items)}")
        lines.append(f"    原因: {s.reason}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def review_diff_text(old_code: str, new_code: str) -> str:
    """Review code changes by comparing old and new Python code strings.
    Returns a structural analysis of what changed: added/removed/modified functions,
    complexity changes, and potential issues in the new code.

    Args:
        old_code: The original Python code string
        new_code: The modified Python code string
    """
    # Analyze both versions
    def analyze(source: str, label: str):
        try:
            tree = ast.parse(source)
            analyzer = CodeAnalyzer(label, source)
            analyzer.visit(tree)
            return analyzer
        except SyntaxError as e:
            return None

    old_a = analyze(old_code, "old")
    new_a = analyze(new_code, "new")

    if old_a is None:
        return "Error: Old code has syntax errors"
    if new_a is None:
        return "Error: New code has syntax errors"

    old_funcs = {f"{f.class_name}.{f.name}" if f.class_name else f.name: f for f in old_a.functions}
    new_funcs = {f"{f.class_name}.{f.name}" if f.class_name else f.name: f for f in new_a.functions}

    added = set(new_funcs) - set(old_funcs)
    removed = set(old_funcs) - set(new_funcs)
    common = set(old_funcs) & set(new_funcs)

    lines = [
        f"=== 代码变更审查 ===",
        f"旧代码: {len(old_code.splitlines())} 行, {len(old_a.functions)} 函数",
        f"新代码: {len(new_code.splitlines())} 行, {len(new_a.functions)} 函数",
        "",
    ]

    if added:
        lines.append(f"--- 新增函数 ({len(added)}) ---")
        for name in sorted(added):
            f = new_funcs[name]
            lines.append(f"  + {name} ({f.num_lines}行, 复杂度:{f.complexity})")

    if removed:
        lines.append(f"\n--- 删除函数 ({len(removed)}) ---")
        for name in sorted(removed):
            lines.append(f"  - {name}")

    modified = []
    for name in sorted(common):
        old_f, new_f = old_funcs[name], new_funcs[name]
        changes = []
        if new_f.num_lines != old_f.num_lines:
            changes.append(f"行数 {old_f.num_lines}→{new_f.num_lines}")
        if new_f.complexity != old_f.complexity:
            direction = "↑" if new_f.complexity > old_f.complexity else "↓"
            changes.append(f"复杂度 {old_f.complexity}→{new_f.complexity}{direction}")
        if new_f.num_params != old_f.num_params:
            changes.append(f"参数 {old_f.num_params}→{new_f.num_params}")
        if changes:
            modified.append((name, changes, new_f))

    if modified:
        lines.append(f"\n--- 修改函数 ({len(modified)}) ---")
        t = DEFAULT_THRESHOLDS
        for name, changes, f in modified:
            flags = []
            if f.num_lines > t["max_func_lines"]: flags.append("行数超标")
            if f.complexity > t["max_complexity"]: flags.append("复杂度高")
            if f.num_params > t["max_func_params"]: flags.append("参数多")
            warn = f" ⚠ {', '.join(flags)}" if flags else ""
            lines.append(f"  ~ {name}: {', '.join(changes)}{warn}")

    if not added and not removed and not modified:
        lines.append("结构无变化（可能仅修改了函数内部实现）。")

    # Check new issues
    t = DEFAULT_THRESHOLDS
    new_issues = []
    for f in new_a.functions:
        name = f"{f.class_name}.{f.name}" if f.class_name else f.name
        if f.num_lines > t["max_func_lines"]:
            new_issues.append(f"  ⚠ {name}: 过长 ({f.num_lines}行)")
        if f.complexity > t["max_complexity"]:
            new_issues.append(f"  ⚠ {name}: 高复杂度 ({f.complexity})")

    if new_issues:
        lines.append(f"\n--- 新代码中的质量问题 ---")
        lines.extend(new_issues)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
#  MCP Tools — ydiff 结构化 Diff（人类审核 AI 改动的关键工具）
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
