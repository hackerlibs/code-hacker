#!/usr/bin/env python3
"""
Multi-Project Workspace MCP Server — 多项目连调工作区。

解决跨项目编辑和搜索的痛点，典型场景：
- Jenkinsfile + 依赖库联合修改
- 前后端分离项目协同开发
- 微服务多仓联调

提供：
- 工作区管理：注册/移除/列出多个项目
- 跨项目搜索：内容搜索、文件搜索
- 跨项目编辑：读写任意项目的文件
- 跨项目 Git：多仓状态总览、协同提交
- 依赖分析：项目间引用关系追踪
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(name="multi-project", host="localhost", port=8007)

# Workspace config persisted in .agent-memory
WORKSPACE_FILE = Path.cwd() / ".agent-memory" / "workspace.json"

ALLOWED_EXTENSIONS = {
    '.txt', '.py', '.java', '.js', '.ts', '.jsx', '.tsx', '.json', '.md',
    '.csv', '.log', '.yaml', '.yml', '.xml', '.html', '.css', '.sh', '.bat',
    '.clj', '.edn', '.cljs', '.cljc', '.go', '.rs', '.toml', '.cfg', '.ini',
    '.sql', '.graphql', '.proto', '.gradle', '.properties', '.env',
    '.Jenkinsfile', '.Dockerfile', '.groovy', '.kt', '.swift', '.rb',
    '.php', '.vue', '.svelte',
}


def _load_workspace() -> dict:
    """Load workspace config."""
    if WORKSPACE_FILE.exists():
        try:
            return json.loads(WORKSPACE_FILE.read_text())
        except Exception:
            pass
    return {"projects": {}}


def _save_workspace(ws: dict):
    """Save workspace config."""
    WORKSPACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    WORKSPACE_FILE.write_text(json.dumps(ws, ensure_ascii=False, indent=2))


def _resolve_project_path(ws: dict, name_or_path: str) -> Optional[str]:
    """Resolve a project name alias to its path, or return path directly."""
    # Check alias first
    if name_or_path in ws["projects"]:
        return ws["projects"][name_or_path]["path"]
    # Check if it's a direct path
    if Path(name_or_path).is_dir():
        return str(Path(name_or_path).resolve())
    return None


def _is_allowed_file(path: str) -> bool:
    """Check if file extension is allowed, or if it's a known config file."""
    p = Path(path)
    if p.suffix.lower() in ALLOWED_EXTENSIONS:
        return True
    # Allow known extensionless config files
    known_names = {'Jenkinsfile', 'Dockerfile', 'Makefile', 'Vagrantfile', 'Gemfile', 'Rakefile'}
    return p.name in known_names


def _run_git(args: list[str], cwd: str, timeout: int = 30) -> dict:
    """Run a git command and return result."""
    try:
        result = subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"Timed out after {timeout}s"}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e)}


async def _read_file_content(file_path: str) -> Optional[str]:
    """Read file content with multiple encoding attempts."""
    for enc in ('utf-8', 'gbk', 'gb2312', 'latin-1'):
        try:
            with open(file_path, 'r', encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
        except Exception:
            return None
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  工作区管理
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def workspace_add(project_path: str, alias: str = "", description: str = "", role: str = "") -> str:
    """Register a project into the multi-project workspace.

    Args:
        project_path: Absolute path to the project directory
        alias: Short name alias for this project (default: directory name)
        description: What this project is (e.g., 'backend API', 'shared library', 'Jenkins pipeline')
        role: Role in the workspace: 'frontend', 'backend', 'library', 'infra', 'config', 'service' (optional)
    """
    path = Path(project_path).resolve()
    if not path.is_dir():
        return f"Error: Directory does not exist: {project_path}"

    alias = alias or path.name
    ws = _load_workspace()

    # Detect git info
    git_res = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], str(path))
    branch = git_res["stdout"].strip() if git_res["success"] else "(not a git repo)"

    ws["projects"][alias] = {
        "path": str(path),
        "description": description,
        "role": role,
        "branch": branch,
        "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    _save_workspace(ws)
    return (
        f"Project registered: '{alias}'\n"
        f"  Path: {path}\n"
        f"  Branch: {branch}\n"
        f"  Role: {role or '(not set)'}\n"
        f"  Description: {description or '(not set)'}\n"
        f"\nWorkspace now has {len(ws['projects'])} project(s)."
    )


@mcp.tool()
async def workspace_remove(alias: str) -> str:
    """Remove a project from the workspace.

    Args:
        alias: Project alias to remove
    """
    ws = _load_workspace()
    if alias not in ws["projects"]:
        available = ", ".join(ws["projects"].keys()) or "(empty)"
        return f"Project '{alias}' not found. Available: {available}"

    del ws["projects"][alias]
    _save_workspace(ws)
    return f"Removed '{alias}' from workspace. Remaining: {len(ws['projects'])} project(s)."


@mcp.tool()
async def workspace_list() -> str:
    """List all projects in the current workspace with their status."""
    ws = _load_workspace()
    if not ws["projects"]:
        return "Workspace is empty. Use workspace_add to register projects."

    lines = [f"=== Multi-Project Workspace ({len(ws['projects'])} projects) ===\n"]

    for alias, info in ws["projects"].items():
        path = info["path"]
        exists = Path(path).is_dir()

        # Get current git status
        if exists:
            branch_res = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], path)
            branch = branch_res["stdout"].strip() if branch_res["success"] else "?"
            status_res = _run_git(["status", "--porcelain"], path)
            changed = len(status_res["stdout"].strip().splitlines()) if status_res["success"] and status_res["stdout"].strip() else 0
            status_str = f"branch: {branch}, {changed} changed file(s)" if changed else f"branch: {branch}, clean"
        else:
            status_str = "PATH NOT FOUND"

        role_str = f" [{info.get('role', '')}]" if info.get('role') else ""
        desc_str = f" — {info.get('description', '')}" if info.get('description') else ""

        lines.append(f"  {alias}{role_str}{desc_str}")
        lines.append(f"    {path}")
        lines.append(f"    {status_str}")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
#  跨项目搜索
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def workspace_search(
    pattern: str,
    projects: str = "",
    file_type: str = "",
    case_sensitive: bool = False,
    max_results_per_project: int = 50,
    context_lines: int = 0,
) -> str:
    """Search for text patterns across all workspace projects. Like grep/ag across multiple repos.

    Args:
        pattern: Text pattern to search for (supports regex)
        projects: Comma-separated project aliases to search (default: all)
        file_type: File type filter (e.g., 'py', 'js', 'groovy') (default: all)
        case_sensitive: Whether to do case-sensitive search (default: False)
        max_results_per_project: Max results per project (default: 50)
        context_lines: Lines of context around matches (default: 0)
    """
    ws = _load_workspace()
    if not ws["projects"]:
        return "Workspace is empty. Use workspace_add to register projects."

    target_aliases = [a.strip() for a in projects.split(",") if a.strip()] if projects else list(ws["projects"].keys())

    # Find search tool
    ag_bin = os.environ.get('AG_PATH', 'ag')
    use_ag = True
    try:
        subprocess.run([ag_bin, '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        use_ag = False

    all_results = []

    for alias in target_aliases:
        if alias not in ws["projects"]:
            all_results.append(f"\n[{alias}] — NOT FOUND in workspace")
            continue

        project_path = ws["projects"][alias]["path"]
        if not Path(project_path).is_dir():
            all_results.append(f"\n[{alias}] — PATH NOT FOUND: {project_path}")
            continue

        if use_ag:
            cmd = [ag_bin, "--nocolor", "--numbers"]
            if not case_sensitive:
                cmd.append("-i")
            if context_lines > 0:
                cmd.extend(["-C", str(context_lines)])
            if file_type:
                cmd.append(f"--{file_type.lstrip('.')}")
            cmd.extend(["-m", str(max_results_per_project), pattern, project_path])
        else:
            cmd = ["grep", "-rn"]
            if not case_sensitive:
                cmd.append("-i")
            if context_lines > 0:
                cmd.extend(["-C", str(context_lines)])
            if file_type:
                cmd.extend(["--include", f"*.{file_type.lstrip('.')}"])
            cmd.extend(["-m", str(max_results_per_project), pattern, project_path])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
            )

            if result.stdout.strip():
                match_count = result.stdout.strip().count("\n") + 1
                # Make paths relative to project root for readability
                output = result.stdout.replace(project_path + "/", "")
                all_results.append(f"\n[{alias}] ({match_count} matches) — {project_path}")
                all_results.append(output.rstrip())
            else:
                all_results.append(f"\n[{alias}] (0 matches)")
        except subprocess.TimeoutExpired:
            all_results.append(f"\n[{alias}] — search timed out")
        except Exception as e:
            all_results.append(f"\n[{alias}] — error: {e}")

    header = f"=== Workspace Search: '{pattern}' across {len(target_aliases)} project(s) ==="
    return header + "\n" + "\n".join(all_results)


@mcp.tool()
async def workspace_find_files(
    pattern: str = "*",
    projects: str = "",
    max_depth: int = 5,
) -> str:
    """Find files matching a glob pattern across workspace projects.

    Args:
        pattern: Glob pattern to match (e.g., '*.py', 'Jenkinsfile', '**/*config*')
        projects: Comma-separated project aliases (default: all)
        max_depth: Max directory depth (default: 5)
    """
    ws = _load_workspace()
    if not ws["projects"]:
        return "Workspace is empty. Use workspace_add to register projects."

    target_aliases = [a.strip() for a in projects.split(",") if a.strip()] if projects else list(ws["projects"].keys())

    all_results = []

    for alias in target_aliases:
        if alias not in ws["projects"]:
            continue
        project_path = Path(ws["projects"][alias]["path"])
        if not project_path.is_dir():
            continue

        matches = []
        skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", "target"}

        for match in sorted(project_path.rglob(pattern)):
            # Check depth
            try:
                rel = match.relative_to(project_path)
            except ValueError:
                continue
            if len(rel.parts) > max_depth:
                continue
            # Skip hidden/build dirs
            if any(part in skip_dirs for part in rel.parts):
                continue

            item_type = "FILE" if match.is_file() else "DIR "
            size = match.stat().st_size if match.is_file() else 0
            matches.append(f"  {item_type} {str(rel):<55} {size:>10,} bytes")
            if len(matches) >= 200:
                matches.append("  ... (truncated)")
                break

        if matches:
            all_results.append(f"\n[{alias}] ({len(matches)} found) — {project_path}")
            all_results.extend(matches)
        else:
            all_results.append(f"\n[{alias}] (0 matches)")

    header = f"=== Workspace Find: '{pattern}' across {len(target_aliases)} project(s) ==="
    return header + "\n" + "\n".join(all_results)


# ═══════════════════════════════════════════════════════════════════════════
#  跨项目文件操作
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def workspace_read_file(project: str, file_path: str, start_line: int = 1, end_line: int = 0) -> str:
    """Read a file from any project in the workspace.

    Args:
        project: Project alias or absolute path
        file_path: Relative file path within the project
        start_line: Start line (1-based, default: 1)
        end_line: End line (0 = read to end, default: 0)
    """
    ws = _load_workspace()
    project_root = _resolve_project_path(ws, project)
    if not project_root:
        available = ", ".join(ws["projects"].keys()) or "(empty)"
        return f"Error: Project '{project}' not found. Available: {available}"

    full_path = Path(project_root) / file_path
    if not full_path.is_file():
        return f"Error: File not found: {full_path}"

    content = await _read_file_content(str(full_path))
    if content is None:
        return f"Error: Unable to read file: {full_path}"

    lines = content.splitlines(keepends=True)
    total = len(lines)
    start = max(1, start_line) - 1
    end = total if end_line <= 0 else min(end_line, total)

    selected = lines[start:end]
    numbered = [f"{i + start + 1:>6}\t{line}" for i, line in enumerate(selected)]
    header = f"[{project}] {file_path} (lines {start + 1}-{end} of {total})"
    return header + "\n" + "".join(numbered)


@mcp.tool()
async def workspace_edit_file(project: str, file_path: str, old_string: str, new_string: str) -> str:
    """Edit a file in any workspace project using precise string replacement.

    Args:
        project: Project alias or absolute path
        file_path: Relative file path within the project
        old_string: The exact text to find and replace
        new_string: The replacement text
    """
    ws = _load_workspace()
    project_root = _resolve_project_path(ws, project)
    if not project_root:
        available = ", ".join(ws["projects"].keys()) or "(empty)"
        return f"Error: Project '{project}' not found. Available: {available}"

    full_path = Path(project_root) / file_path
    if not full_path.is_file():
        return f"Error: File not found: {full_path}"
    if not _is_allowed_file(str(full_path)):
        return f"Error: File type not allowed: {full_path.suffix}"

    content = await _read_file_content(str(full_path))
    if content is None:
        return f"Error: Unable to read file: {full_path}"

    count = content.count(old_string)
    if count == 0:
        return f"Error: old_string not found in [{project}] {file_path}"
    if count > 1:
        return f"Error: old_string found {count} times. Provide more context to make it unique."

    new_content = content.replace(old_string, new_string, 1)
    try:
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return f"Successfully edited [{project}] {file_path}: replaced 1 occurrence."
    except Exception as e:
        return f"Error writing file: {e}"


@mcp.tool()
async def workspace_write_file(project: str, file_path: str, content: str) -> str:
    """Write/create a file in any workspace project.

    Args:
        project: Project alias or absolute path
        file_path: Relative file path within the project
        content: Content to write
    """
    ws = _load_workspace()
    project_root = _resolve_project_path(ws, project)
    if not project_root:
        available = ", ".join(ws["projects"].keys()) or "(empty)"
        return f"Error: Project '{project}' not found. Available: {available}"

    full_path = Path(project_root) / file_path
    if not _is_allowed_file(str(full_path)):
        return f"Error: File type not allowed: {full_path.suffix}"

    try:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote [{project}] {file_path} ({len(content)} chars)"
    except Exception as e:
        return f"Error writing file: {e}"


# ═══════════════════════════════════════════════════════════════════════════
#  跨项目 Git 操作
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def workspace_git_status(projects: str = "") -> str:
    """Show git status across all workspace projects — a bird's-eye view of what's changed where.

    Args:
        projects: Comma-separated project aliases (default: all)
    """
    ws = _load_workspace()
    if not ws["projects"]:
        return "Workspace is empty."

    target_aliases = [a.strip() for a in projects.split(",") if a.strip()] if projects else list(ws["projects"].keys())

    lines = [f"=== Workspace Git Status ({len(target_aliases)} projects) ===\n"]

    total_changed = 0
    for alias in target_aliases:
        if alias not in ws["projects"]:
            continue
        project_path = ws["projects"][alias]["path"]
        if not Path(project_path).is_dir():
            lines.append(f"[{alias}] PATH NOT FOUND: {project_path}\n")
            continue

        branch_res = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], project_path)
        branch = branch_res["stdout"].strip() if branch_res["success"] else "?"

        status_res = _run_git(["status", "--porcelain"], project_path)
        if status_res["success"] and status_res["stdout"].strip():
            changes = status_res["stdout"].strip().splitlines()
            total_changed += len(changes)
            lines.append(f"[{alias}] branch: {branch} — {len(changes)} change(s)")
            for change in changes[:20]:
                lines.append(f"  {change}")
            if len(changes) > 20:
                lines.append(f"  ... and {len(changes) - 20} more")
        else:
            lines.append(f"[{alias}] branch: {branch} — clean")
        lines.append("")

    lines.append(f"Total changed files across workspace: {total_changed}")
    return "\n".join(lines)


@mcp.tool()
async def workspace_git_diff(projects: str = "", staged: bool = False) -> str:
    """Show git diff across workspace projects.

    Args:
        projects: Comma-separated project aliases (default: all)
        staged: Show staged changes only (default: False)
    """
    ws = _load_workspace()
    if not ws["projects"]:
        return "Workspace is empty."

    target_aliases = [a.strip() for a in projects.split(",") if a.strip()] if projects else list(ws["projects"].keys())

    lines = [f"=== Workspace Git Diff ({'staged' if staged else 'unstaged'}) ===\n"]

    for alias in target_aliases:
        if alias not in ws["projects"]:
            continue
        project_path = ws["projects"][alias]["path"]
        if not Path(project_path).is_dir():
            continue

        args = ["diff", "--stat"]
        if staged:
            args.append("--cached")

        diff_res = _run_git(args, project_path)
        if diff_res["success"] and diff_res["stdout"].strip():
            lines.append(f"[{alias}] — {project_path}")
            lines.append(diff_res["stdout"].rstrip())
            lines.append("")

    if len(lines) == 1:
        lines.append("No changes found.")

    return "\n".join(lines)


@mcp.tool()
async def workspace_git_log(projects: str = "", max_count: int = 5) -> str:
    """Show recent git log across workspace projects. Useful for understanding recent activity.

    Args:
        projects: Comma-separated project aliases (default: all)
        max_count: Number of recent commits per project (default: 5)
    """
    ws = _load_workspace()
    if not ws["projects"]:
        return "Workspace is empty."

    target_aliases = [a.strip() for a in projects.split(",") if a.strip()] if projects else list(ws["projects"].keys())

    lines = [f"=== Workspace Recent Commits ===\n"]

    for alias in target_aliases:
        if alias not in ws["projects"]:
            continue
        project_path = ws["projects"][alias]["path"]
        if not Path(project_path).is_dir():
            continue

        log_res = _run_git(
            ["log", f"-{max_count}", "--oneline", "--decorate"],
            project_path,
        )
        if log_res["success"] and log_res["stdout"].strip():
            lines.append(f"[{alias}] — {project_path}")
            lines.append(log_res["stdout"].rstrip())
            lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def workspace_commit(
    projects: str,
    message: str,
    add_all: bool = False,
) -> str:
    """Commit changes in one or more workspace projects with the same commit message.
    Useful for coordinated changes across repos (e.g., library + consumer).

    Args:
        projects: Comma-separated project aliases to commit in
        message: Commit message (shared across all projects)
        add_all: Stage all changes before committing (default: False, only commits staged files)
    """
    ws = _load_workspace()
    target_aliases = [a.strip() for a in projects.split(",") if a.strip()]
    if not target_aliases:
        return "Error: Specify at least one project alias."

    results = []
    for alias in target_aliases:
        if alias not in ws["projects"]:
            results.append(f"[{alias}] SKIPPED — not found in workspace")
            continue

        project_path = ws["projects"][alias]["path"]
        if not Path(project_path).is_dir():
            results.append(f"[{alias}] SKIPPED — path not found")
            continue

        if add_all:
            add_res = _run_git(["add", "-A"], project_path)
            if not add_res["success"]:
                results.append(f"[{alias}] FAILED to stage: {add_res['stderr']}")
                continue

        # Check if there's anything to commit
        status_res = _run_git(["diff", "--cached", "--quiet"], project_path)
        if status_res["success"]:
            results.append(f"[{alias}] SKIPPED — nothing staged to commit")
            continue

        commit_res = _run_git(["commit", "-m", message], project_path)
        if commit_res["success"]:
            # Extract short hash
            hash_res = _run_git(["rev-parse", "--short", "HEAD"], project_path)
            short_hash = hash_res["stdout"].strip() if hash_res["success"] else "?"
            results.append(f"[{alias}] COMMITTED ({short_hash}) — {message}")
        else:
            results.append(f"[{alias}] FAILED — {commit_res['stderr'].strip()}")

    return "=== Workspace Coordinated Commit ===\n\n" + "\n".join(results)


# ═══════════════════════════════════════════════════════════════════════════
#  跨项目依赖分析
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def workspace_find_dependencies(
    symbol: str,
    projects: str = "",
    file_type: str = "",
) -> str:
    """Find where a symbol (function, class, package, config key) is referenced across all workspace projects.
    Useful for impact analysis: "if I change this function in library A, what breaks in project B?"

    Args:
        symbol: Symbol, function name, class name, or any string to trace
        projects: Comma-separated project aliases (default: all)
        file_type: File type filter, e.g., 'py', 'groovy', 'yaml' (default: all)
    """
    ws = _load_workspace()
    if not ws["projects"]:
        return "Workspace is empty."

    target_aliases = [a.strip() for a in projects.split(",") if a.strip()] if projects else list(ws["projects"].keys())

    ag_bin = os.environ.get('AG_PATH', 'ag')
    use_ag = True
    try:
        subprocess.run([ag_bin, '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        use_ag = False

    lines = [f"=== Cross-Project Dependency Trace: '{symbol}' ===\n"]
    total_refs = 0

    for alias in target_aliases:
        if alias not in ws["projects"]:
            continue
        project_path = ws["projects"][alias]["path"]
        if not Path(project_path).is_dir():
            continue

        if use_ag:
            cmd = [ag_bin, "--nocolor", "--numbers", "-m", "30"]
            if file_type:
                cmd.append(f"--{file_type.lstrip('.')}")
            cmd.extend([symbol, project_path])
        else:
            cmd = ["grep", "-rn", "-m", "30"]
            if file_type:
                cmd.extend(["--include", f"*.{file_type.lstrip('.')}"])
            cmd.extend([symbol, project_path])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            if result.stdout.strip():
                matches = result.stdout.strip().splitlines()
                total_refs += len(matches)
                output = result.stdout.replace(project_path + "/", "")
                lines.append(f"[{alias}] ({len(matches)} references)")
                lines.append(output.rstrip())
                lines.append("")
        except Exception as e:
            lines.append(f"[{alias}] error: {e}")

    lines.append(f"\nTotal references across workspace: {total_refs}")
    if total_refs > 0:
        lines.append(
            "\nTip: Review all references before modifying the symbol. "
            "Use workspace_edit_file to make coordinated changes."
        )

    return "\n".join(lines)


@mcp.tool()
async def workspace_overview(projects: str = "") -> str:
    """Get a high-level overview of all workspace projects: languages, structure, config files, entry points.

    Args:
        projects: Comma-separated project aliases (default: all)
    """
    ws = _load_workspace()
    if not ws["projects"]:
        return "Workspace is empty. Use workspace_add to register projects."

    target_aliases = [a.strip() for a in projects.split(",") if a.strip()] if projects else list(ws["projects"].keys())

    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", "target", ".idea"}

    config_names = {
        "package.json", "pyproject.toml", "setup.py", "setup.cfg",
        "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
        "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
        "Jenkinsfile", "requirements.txt", "Pipfile", "tsconfig.json",
        ".env", ".env.example", "settings.gradle",
    }

    lines = [f"=== Workspace Overview ({len(target_aliases)} projects) ===\n"]

    for alias in target_aliases:
        if alias not in ws["projects"]:
            continue
        info = ws["projects"][alias]
        root = Path(info["path"])
        if not root.is_dir():
            lines.append(f"[{alias}] PATH NOT FOUND: {info['path']}\n")
            continue

        ext_counts: dict[str, int] = {}
        total_files = 0
        configs_found = []

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith('.')]
            for fname in filenames:
                total_files += 1
                ext = Path(fname).suffix.lower() or "(no ext)"
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
                if fname in config_names:
                    rel = os.path.relpath(os.path.join(dirpath, fname), root)
                    configs_found.append(rel)

        top_langs = sorted(ext_counts.items(), key=lambda x: -x[1])[:8]
        lang_str = ", ".join(f"{ext}({cnt})" for ext, cnt in top_langs)

        role_str = f" [{info.get('role', '')}]" if info.get('role') else ""
        desc_str = f" — {info.get('description', '')}" if info.get('description') else ""

        lines.append(f"[{alias}]{role_str}{desc_str}")
        lines.append(f"  Path: {root}")
        lines.append(f"  Files: {total_files} | Languages: {lang_str}")
        if configs_found:
            lines.append(f"  Config: {', '.join(configs_found[:10])}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def workspace_exec(project: str, command: str, timeout: int = 30) -> str:
    """Execute a shell command in the context of a specific workspace project.
    Dangerous commands (rm, format, etc.) are blocked.

    Args:
        project: Project alias or absolute path
        command: Command to execute
        timeout: Timeout in seconds (default: 30)
    """
    ws = _load_workspace()
    project_root = _resolve_project_path(ws, project)
    if not project_root:
        available = ", ".join(ws["projects"].keys()) or "(empty)"
        return f"Error: Project '{project}' not found. Available: {available}"

    blocked = {'rm', 'del', 'format', 'mkfs', 'dd', 'shutdown', 'reboot', 'halt', 'poweroff'}
    cmd_parts = command.strip().split()
    if cmd_parts and cmd_parts[0].lower() in blocked:
        return f"Error: Command blocked for safety: {cmd_parts[0]}"

    try:
        result = subprocess.run(
            command, shell=True, cwd=project_root,
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        out = []
        out.append(f"[{project}] $ {command}")
        out.append(f"Return code: {result.returncode}")
        if result.stdout:
            out.append(result.stdout.rstrip())
        if result.stderr:
            out.append(f"[stderr] {result.stderr.rstrip()}")
        return "\n".join(out)
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


# ─── 入口 ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="streamable-http")
