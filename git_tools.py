import subprocess
from pathlib import Path
from typing import Optional
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(name="git-tools", host='localhost', port=8002)


def run_git(args: list[str], cwd: str = ".", timeout: int = 30) -> dict:
    """Run a git command and return result."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"Timed out after {timeout}s", "returncode": -1}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e), "returncode": -1}


def format_result(res: dict) -> str:
    out = []
    if res["stdout"]:
        out.append(res["stdout"])
    if res["stderr"]:
        out.append(f"[stderr] {res['stderr']}")
    if not res["success"]:
        out.insert(0, f"[exit {res['returncode']}]")
    return "\n".join(out) if out else "(no output)"


@mcp.tool()
async def git_status(repo_path: str = ".") -> str:
    """Show working tree status: staged, unstaged, and untracked files.

    Args:
        repo_path: Path to the git repository (default: current directory)
    """
    return format_result(run_git(["status"], cwd=repo_path))


@mcp.tool()
async def git_diff(repo_path: str = ".", staged: bool = False, file_path: Optional[str] = None) -> str:
    """Show changes in working tree or staged area.

    Args:
        repo_path: Path to the git repository
        staged: If True, show staged changes (--cached)
        file_path: Optional specific file to diff
    """
    args = ["diff"]
    if staged:
        args.append("--cached")
    if file_path:
        args.extend(["--", file_path])
    return format_result(run_git(args, cwd=repo_path))


@mcp.tool()
async def git_log(
    repo_path: str = ".",
    max_count: int = 20,
    oneline: bool = True,
    file_path: Optional[str] = None,
    branch: Optional[str] = None,
) -> str:
    """Show commit history.

    Args:
        repo_path: Path to the git repository
        max_count: Number of commits to show (default: 20)
        oneline: Use one-line format (default: True)
        file_path: Show history for a specific file
        branch: Show history for a specific branch
    """
    args = ["log", f"-{max_count}"]
    if oneline:
        args.append("--oneline")
    if branch:
        args.append(branch)
    if file_path:
        args.extend(["--", file_path])
    return format_result(run_git(args, cwd=repo_path))


@mcp.tool()
async def git_show(repo_path: str = ".", commit: str = "HEAD", file_path: Optional[str] = None) -> str:
    """Show the content of a commit or a file at a specific commit.

    Args:
        repo_path: Path to the git repository
        commit: Commit hash or ref (default: HEAD)
        file_path: Show a specific file at that commit (e.g., 'HEAD~3:src/main.py')
    """
    if file_path:
        return format_result(run_git(["show", f"{commit}:{file_path}"], cwd=repo_path))
    return format_result(run_git(["show", commit, "--stat"], cwd=repo_path))


@mcp.tool()
async def git_branch(repo_path: str = ".", show_all: bool = False) -> str:
    """List branches.

    Args:
        repo_path: Path to the git repository
        show_all: Show remote branches too (default: False)
    """
    args = ["branch"]
    if show_all:
        args.append("-a")
    return format_result(run_git(args, cwd=repo_path))


@mcp.tool()
async def git_add(repo_path: str = ".", files: str = ".") -> str:
    """Stage files for commit.

    Args:
        repo_path: Path to the git repository
        files: Files to stage, space-separated (default: '.' for all)
    """
    file_list = files.split()
    return format_result(run_git(["add"] + file_list, cwd=repo_path))


@mcp.tool()
async def git_commit(repo_path: str = ".", message: str = "") -> str:
    """Create a commit with staged changes.

    Args:
        repo_path: Path to the git repository
        message: Commit message
    """
    if not message:
        return "Error: Commit message is required."
    return format_result(run_git(["commit", "-m", message], cwd=repo_path))


@mcp.tool()
async def git_checkout(repo_path: str = ".", target: str = "") -> str:
    """Switch branches or restore files.

    Args:
        repo_path: Path to the git repository
        target: Branch name or file path to checkout
    """
    if not target:
        return "Error: Target branch or file is required."
    return format_result(run_git(["checkout", target], cwd=repo_path))


@mcp.tool()
async def git_create_branch(repo_path: str = ".", branch_name: str = "", base: str = "HEAD") -> str:
    """Create a new branch.

    Args:
        repo_path: Path to the git repository
        branch_name: Name of the new branch
        base: Base ref for the new branch (default: HEAD)
    """
    if not branch_name:
        return "Error: Branch name is required."
    return format_result(run_git(["checkout", "-b", branch_name, base], cwd=repo_path))


@mcp.tool()
async def git_stash(repo_path: str = ".", action: str = "push", message: Optional[str] = None) -> str:
    """Manage git stash. Actions: push, pop, list, show, drop.

    Args:
        repo_path: Path to the git repository
        action: Stash action - push, pop, list, show, drop (default: push)
        message: Message for stash push
    """
    args = ["stash", action]
    if action == "push" and message:
        args.extend(["-m", message])
    return format_result(run_git(args, cwd=repo_path))


@mcp.tool()
async def git_blame(repo_path: str = ".", file_path: str = "", start_line: int = 0, end_line: int = 0) -> str:
    """Show who last modified each line of a file.

    Args:
        repo_path: Path to the git repository
        file_path: File to blame
        start_line: Start line (0 = from beginning)
        end_line: End line (0 = to end)
    """
    if not file_path:
        return "Error: file_path is required."
    args = ["blame"]
    if start_line > 0 and end_line > 0:
        args.extend(["-L", f"{start_line},{end_line}"])
    args.append(file_path)
    return format_result(run_git(args, cwd=repo_path))


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
