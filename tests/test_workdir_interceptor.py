"""Smoke test: WorkdirPathInterceptor rewrites MCP tool args correctly.

Run:  uv run python tests/test_workdir_interceptor.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tui_app import WorkdirPathInterceptor, TOOL_PATH_ARGS, _resolve_path_arg

# macOS /tmp is a symlink to /private/tmp. The real TUI always passes a
# resolved workdir (Path(...).resolve()), so test with the resolved form.
WORKDIR = Path("/tmp/fake-project").resolve()
EXPECT = str(WORKDIR)


class FakeRequest:
    """Minimal stand-in for MCPToolCallRequest."""

    def __init__(self, name, args):
        self.name = name
        self.args = args
        self.server_name = "test"

    def override(self, **kw):
        new = FakeRequest(self.name, dict(self.args))
        new.args.update(kw.get("args", {}))
        return new


class Recorder:
    """Handler that records the final request and returns a sentinel."""

    def __init__(self):
        self.seen = None

    async def __call__(self, request):
        self.seen = request
        return "HANDLER_RESULT"


def run(tool_name, args):
    rec = Recorder()
    req = FakeRequest(tool_name, args)
    out = asyncio.run(WorkdirPathInterceptor(WORKDIR)(req, rec))
    return rec, out


def check(label, cond):
    mark = "✓" if cond else "✗ FAIL"
    print(f"  {mark} {label}")
    if not cond:
        global FAILED
        FAILED = True


FAILED = False

print("== _resolve_path_arg ==")
check("relative dir '.' -> workdir", _resolve_path_arg(".", WORKDIR) == EXPECT)
check("relative dir 'src' -> workdir/src", _resolve_path_arg("src", WORKDIR) == EXPECT + "/src")
check("relative file kept", _resolve_path_arg("src/app.py", WORKDIR) == EXPECT + "/src/app.py")
check("absolute passthrough", _resolve_path_arg("/etc/hosts", WORKDIR) == "/etc/hosts")
check("empty passthrough", _resolve_path_arg("", WORKDIR) == "")
check("None passthrough", _resolve_path_arg(None, WORKDIR) is None)
check("~ expands to absolute home path", _resolve_path_arg("~/x", WORKDIR) == str(Path("~/x").expanduser()) and Path(_resolve_path_arg("~/x", WORKDIR)).is_absolute())

print("== interceptor: list_directory '.' ==")
rec, out = run("list_directory", {"directory_path": "."})
check("rewritten to workdir", rec.seen.args["directory_path"] == EXPECT)
check("handler still invoked", out == "HANDLER_RESULT")

print("== interceptor: git_status no-args (server default '.') ==")
rec, _ = run("git_status", {})
check("workdir injected for dot-default arg", rec.seen.args == {"repo_path": EXPECT})

print("== interceptor: read_file no-args (required param) ==")
rec, _ = run("read_file", {})
check("required param NOT injected (let server reject)", rec.seen.args == {})

print("== interceptor: git_status repo_path='.' ==")
rec, _ = run("git_status", {"repo_path": "."})
check("repo_path rewritten", rec.seen.args["repo_path"] == EXPECT)

print("== interceptor: read_file absolute ==")
rec, _ = run("read_file", {"file_path": "/etc/hosts"})
check("absolute path untouched", rec.seen.args["file_path"] == "/etc/hosts")

print("== interceptor: read_file relative ==")
rec, _ = run("read_file", {"file_path": "tui_app.py"})
check("relative path anchored", rec.seen.args["file_path"] == EXPECT + "/tui_app.py")

print("== interceptor: git_diff file_path NOT rewritten ==")
rec, _ = run("git_diff", {"repo_path": ".", "file_path": "src/x.py"})
check("repo_path rewritten", rec.seen.args["repo_path"] == EXPECT)
check("git file_path untouched (repo-relative)", rec.seen.args["file_path"] == "src/x.py")

print("== interceptor: ydiff_files two paths ==")
rec, _ = run("ydiff_files", {"file_path1": "a.py", "file_path2": "b.py"})
check("file_path1 anchored", rec.seen.args["file_path1"] == EXPECT + "/a.py")
check("file_path2 anchored", rec.seen.args["file_path2"] == EXPECT + "/b.py")

print("== interceptor: get_current_directory short-circuit ==")
rec, out = run("get_current_directory", {})
check("handler NOT called", rec.seen is None)
check("returns workdir text", "Current working directory" in str(out) and EXPECT in str(out))

print("== interceptor: unknown tool passes through ==")
rec, out = run("memory_search", {"query": "x"})
check("args untouched", rec.seen.args == {"query": "x"})
check("handler called", out == "HANDLER_RESULT")

print()
if FAILED:
    print("RESULT: FAILURES ✗")
    sys.exit(1)
print("RESULT: all checks passed ✓")
