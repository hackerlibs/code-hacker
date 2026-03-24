import ast
import json
import os
import subprocess
from pathlib import Path
from typing import Optional
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("code-intel")

# Language-specific comment/def patterns for basic parsing
LANG_PATTERNS = {
    ".py": {"def": r"^\s*(class |def |async def )", "import": r"^\s*(import |from .+ import )"},
    ".js": {"def": r"^\s*(function |class |const \w+ = |export )", "import": r"^\s*(import |require\()"},
    ".ts": {"def": r"^\s*(function |class |const \w+ = |export |interface |type )", "import": r"^\s*(import )"},
    ".java": {"def": r"^\s*(public |private |protected |class |interface )", "import": r"^\s*(import )"},
    ".go": {"def": r"^\s*(func |type )", "import": r"^\s*(import )"},
    ".rs": {"def": r"^\s*(fn |struct |enum |impl |trait |pub )", "import": r"^\s*(use )"},
}


@mcp.tool()
async def analyze_python_file(file_path: str) -> str:
    """Deep analysis of a Python file: classes, functions, imports, dependencies, docstrings.
    Returns a structured overview without reading the entire file content.

    Args:
        file_path: Path to the Python file
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: File not found: {file_path}"
    if path.suffix != ".py":
        return f"Error: Not a Python file: {file_path}"

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as e:
        return f"Syntax error in {file_path}: {e}"

    imports = []
    classes = []
    functions = []
    globals_vars = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")
        elif isinstance(node, ast.ClassDef):
            bases = [_name(b) for b in node.bases]
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = [a.arg for a in item.args.args if a.arg != "self"]
                    methods.append(f"  {'async ' if isinstance(item, ast.AsyncFunctionDef) else ''}def {item.name}({', '.join(args)}) -> line {item.lineno}")
            doc = ast.get_docstring(node) or ""
            doc_short = doc.split("\n")[0][:100] if doc else ""
            classes.append(
                f"class {node.name}({', '.join(bases)}) -> line {node.lineno}"
                + (f'  """{doc_short}"""' if doc_short else "")
                + ("\n" + "\n".join(methods) if methods else "")
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            doc = ast.get_docstring(node) or ""
            doc_short = doc.split("\n")[0][:100] if doc else ""
            prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            functions.append(
                f"{prefix}def {node.name}({', '.join(args)}) -> line {node.lineno}"
                + (f'  """{doc_short}"""' if doc_short else "")
            )
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    globals_vars.append(f"{target.id} -> line {node.lineno}")

    lines = source.count("\n") + 1
    sections = [f"=== {file_path} ({lines} lines) ==="]

    if imports:
        sections.append(f"\n--- Imports ({len(imports)}) ---")
        sections.append("\n".join(imports))
    if globals_vars:
        sections.append(f"\n--- Global Variables ({len(globals_vars)}) ---")
        sections.append("\n".join(globals_vars))
    if classes:
        sections.append(f"\n--- Classes ({len(classes)}) ---")
        sections.append("\n".join(classes))
    if functions:
        sections.append(f"\n--- Functions ({len(functions)}) ---")
        sections.append("\n".join(functions))

    return "\n".join(sections)


def _name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    return "?"


@mcp.tool()
async def extract_symbols(file_path: str) -> str:
    """Extract all symbol definitions (functions, classes, variables) from a source file.
    Works with Python (AST), and uses regex patterns for JS/TS/Java/Go/Rust.

    Args:
        file_path: Path to the source file
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: File not found: {file_path}"

    suffix = path.suffix.lower()

    # For Python, use AST
    if suffix == ".py":
        return await analyze_python_file(file_path)

    # For other languages, use grep-like approach
    import re

    patterns = LANG_PATTERNS.get(suffix)
    if not patterns:
        return f"Unsupported file type: {suffix}. Supported: {', '.join(LANG_PATTERNS.keys())}"

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Error reading file: {e}"

    lines = content.splitlines()
    defs = []
    imps = []

    for i, line in enumerate(lines, 1):
        if re.match(patterns["def"], line):
            defs.append(f"  {i}: {line.strip()}")
        if re.match(patterns["import"], line):
            imps.append(f"  {i}: {line.strip()}")

    sections = [f"=== {file_path} ({len(lines)} lines) ==="]
    if imps:
        sections.append(f"\n--- Imports ({len(imps)}) ---")
        sections.extend(imps)
    if defs:
        sections.append(f"\n--- Definitions ({len(defs)}) ---")
        sections.extend(defs)

    return "\n".join(sections)


@mcp.tool()
async def project_overview(directory: str = ".", max_depth: int = 3) -> str:
    """Generate a project overview: directory tree, file counts by language, entry points, config files.
    Helps quickly understand a new codebase.

    Args:
        directory: Project root directory (default: current directory)
        max_depth: Max depth for directory tree (default: 3)
    """
    root = Path(directory)
    if not root.exists() or not root.is_dir():
        return f"Error: Directory not found: {directory}"

    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
                 "dist", "build", ".next", ".cache", "target", ".idea", ".vscode"}

    ext_counts: dict[str, int] = {}
    total_files = 0
    total_dirs = 0
    tree_lines = []
    config_files = []
    entry_points = []

    config_names = {"package.json", "pyproject.toml", "setup.py", "setup.cfg",
                    "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
                    "Makefile", "Dockerfile", "docker-compose.yml",
                    ".env", "requirements.txt", "Pipfile", "tsconfig.json"}

    entry_names = {"main.py", "app.py", "index.js", "index.ts", "main.go",
                   "main.rs", "Main.java", "manage.py", "server.py", "server.js"}

    def walk(p: Path, depth: int, prefix: str = ""):
        nonlocal total_files, total_dirs
        if depth > max_depth:
            return
        try:
            items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
        except PermissionError:
            return

        dirs = [i for i in items if i.is_dir() and i.name not in skip_dirs and not i.name.startswith(".")]
        files = [i for i in items if i.is_file()]

        for f in files:
            total_files += 1
            ext = f.suffix.lower() or "(no ext)"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
            if f.name in config_names:
                config_files.append(str(f.relative_to(root)))
            if f.name in entry_names:
                entry_points.append(str(f.relative_to(root)))
            if depth <= max_depth:
                tree_lines.append(f"{prefix}  {f.name}")

        for d in dirs:
            total_dirs += 1
            if depth <= max_depth:
                tree_lines.append(f"{prefix}  {d.name}/")
            walk(d, depth + 1, prefix + "  ")

    tree_lines.append(f"{root.name}/")
    walk(root, 1)

    sections = [f"=== Project Overview: {root.absolute()} ==="]
    sections.append(f"Total: {total_files} files, {total_dirs} directories\n")

    if config_files:
        sections.append("--- Config Files ---")
        sections.extend(f"  {f}" for f in config_files)

    if entry_points:
        sections.append("\n--- Entry Points ---")
        sections.extend(f"  {f}" for f in entry_points)

    # Top languages
    sorted_ext = sorted(ext_counts.items(), key=lambda x: -x[1])[:15]
    sections.append("\n--- Languages ---")
    for ext, count in sorted_ext:
        sections.append(f"  {ext:<10} {count:>5} files")

    sections.append("\n--- Directory Tree ---")
    sections.extend(tree_lines[:200])
    if len(tree_lines) > 200:
        sections.append("  ... (truncated)")

    return "\n".join(sections)


@mcp.tool()
async def find_references(
    symbol: str,
    directory: str = ".",
    file_type: Optional[str] = None,
    max_results: int = 50,
) -> str:
    """Find all references to a symbol (function, class, variable) across the codebase.
    Uses grep/ag to search for exact or pattern matches.

    Args:
        symbol: Symbol name to search for
        directory: Directory to search in (default: current directory)
        file_type: File extension filter, e.g., 'py', 'js' (default: None for all)
        max_results: Max number of results (default: 50)
    """
    root = Path(directory)
    if not root.exists():
        return f"Error: Directory not found: {directory}"

    # Try ag first, fall back to grep
    for tool_args in [
        ["ag", "--nocolor", "--numbers", "-m", str(max_results)],
        ["grep", "-rn", "--include", f"*.{file_type}" if file_type else "*", "-m", str(max_results)],
    ]:
        try:
            cmd = list(tool_args)
            if tool_args[0] == "ag" and file_type:
                cmd.extend([f"--{file_type}"])
            cmd.append(symbol)
            cmd.append(str(root.absolute()))

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            if result.returncode <= 1:
                output = result.stdout.strip()
                if not output:
                    return f"No references found for '{symbol}' in {root.absolute()}"
                count = output.count("\n") + 1
                return f"References to '{symbol}' ({count} matches):\n\n{output}"
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return "Error: Search timed out."

    return "Error: Neither 'ag' nor 'grep' is available."


@mcp.tool()
async def dependency_graph(file_path: str) -> str:
    """Analyze import/dependency relationships for a file.
    Shows what this file imports and which files import it.

    Args:
        file_path: Path to the source file to analyze
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: File not found: {file_path}"

    suffix = path.suffix.lower()
    root = path.parent

    # Extract imports from the target file
    imports_out = []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".py":
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports_out.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    imports_out.append(node.module or "(relative)")
        else:
            import re
            patterns = LANG_PATTERNS.get(suffix, {})
            if "import" in patterns:
                for line in content.splitlines():
                    if re.match(patterns["import"], line):
                        imports_out.append(line.strip())
    except Exception as e:
        return f"Error parsing {file_path}: {e}"

    # Find files that import this file
    stem = path.stem
    imported_by = []
    try:
        for tool in ["ag", "grep"]:
            try:
                if tool == "ag":
                    cmd = ["ag", "--nocolor", "-l", stem, str(root.absolute())]
                else:
                    cmd = ["grep", "-rl", stem, str(root.absolute())]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                                        encoding="utf-8", errors="replace")
                if result.returncode == 0:
                    imported_by = [f for f in result.stdout.strip().splitlines()
                                   if f != str(path.absolute())]
                break
            except FileNotFoundError:
                continue
    except Exception:
        pass

    sections = [f"=== Dependency Analysis: {file_path} ==="]
    sections.append(f"\n--- This file imports ({len(imports_out)}) ---")
    sections.extend(f"  {imp}" for imp in imports_out)
    sections.append(f"\n--- Imported by ({len(imported_by)}) ---")
    sections.extend(f"  {f}" for f in imported_by[:30])

    return "\n".join(sections)


if __name__ == "__main__":
    mcp.run(transport="stdio")
