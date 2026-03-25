"""
Unit tests for auto_refactor two-phase commit strategy.

Tests the #not-need-review / #need-review commit separation logic
without requiring MCP servers or LLM.

Run:
    uv run pytest tests/test_refactor_commit.py -v
"""

import os
import sys
import subprocess
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field
from typing import Optional

import pytest

# Add project root + lib to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))


# ─── Helpers ────────────────────────────────────────────────────────────────

def _git(cwd, *args):
    """Run a git command in cwd."""
    result = subprocess.run(
        ["git"] + list(args), cwd=cwd,
        capture_output=True, text=True, timeout=15,
    )
    return result


def _init_git_repo(tmp_path):
    """Create a git repo with an initial commit."""
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@test.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("# test\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "initial")


def _get_commits(tmp_path):
    """Return list of (hash, subject) tuples, newest first."""
    result = _git(tmp_path, "log", "--oneline", "--format=%h %s")
    lines = result.stdout.strip().splitlines()
    return [(l.split(" ", 1)[0], l.split(" ", 1)[1]) for l in lines if l.strip()]


# ─── _git_commit unit tests ────────────────────────────────────────────────

class TestGitCommitHelper:
    """Test the _git_commit helper function."""

    def test_commit_success(self, tmp_path):
        """Normal commit should succeed and return True."""
        from code_refactor import _git_commit

        _init_git_repo(tmp_path)
        (tmp_path / "new_file.py").write_text("x = 1\n")

        ok, output = _git_commit(str(tmp_path), "test commit")
        assert ok is True
        assert "nothing to commit" not in output

        commits = _get_commits(tmp_path)
        assert commits[0][1] == "test commit"

    def test_commit_nothing_to_commit(self, tmp_path):
        """Committing with no changes should return True (not an error)."""
        from code_refactor import _git_commit

        _init_git_repo(tmp_path)

        ok, output = _git_commit(str(tmp_path), "empty")
        assert ok is True
        assert "nothing to commit" in output

    def test_commit_not_a_git_repo(self, tmp_path):
        """Committing in a non-git directory should return False."""
        from code_refactor import _git_commit

        (tmp_path / "file.txt").write_text("hello\n")
        ok, output = _git_commit(str(tmp_path), "should fail")
        assert ok is False

    def test_commit_message_preserved(self, tmp_path):
        """Multi-line commit message with tags should be preserved."""
        from code_refactor import _git_commit

        _init_git_repo(tmp_path)
        (tmp_path / "a.py").write_text("a = 1\n")

        msg = "refactor: move code #not-need-review\n\n机械性移动"
        ok, _ = _git_commit(str(tmp_path), msg)
        assert ok is True

        result = _git(tmp_path, "log", "-1", "--format=%B")
        assert "#not-need-review" in result.stdout


# ─── Two-phase commit integration tests ────────────────────────────────────

class TestTwoPhaseCommit:
    """Test that auto_refactor creates separate commits for file splits vs func splits."""

    def _create_project_with_long_file(self, tmp_path):
        """Create a Python project that triggers both file and function splits."""
        src = tmp_path / "project"
        src.mkdir()
        _init_git_repo(src)

        # Create a large file with multiple classes and a long function
        # to trigger both split_file and split_func
        code_lines = ['"""Big module."""\n', "import os\n", "import sys\n", "\n"]

        # Add two classes (to trigger file split)
        for cls_name in ["AlphaHandler", "AlphaProcessor", "BetaHandler", "BetaProcessor", "GammaService"]:
            code_lines.append(f"\nclass {cls_name}:\n")
            code_lines.append(f'    """A {cls_name}."""\n')
            for i in range(5):
                code_lines.append(f"    def method_{i}(self):\n")
                code_lines.append(f"        return {i}\n")
                code_lines.append("\n")

        # Add a very long function (to trigger func split)
        code_lines.append("\ndef very_long_function(data):\n")
        code_lines.append('    """Process data in many steps."""\n')
        code_lines.append("    result = []\n")
        for i in range(20):
            code_lines.append(f"    step_{i} = data + {i}\n")
            code_lines.append(f"    if step_{i} > 0:\n")
            code_lines.append(f"        result.append(step_{i})\n")
            code_lines.append(f"        val_{i} = step_{i} * 2\n")
            code_lines.append(f"        result.append(val_{i})\n")
        code_lines.append("    return result\n")

        big_file = src / "big_module.py"
        big_file.write_text("".join(code_lines))

        _git(src, "add", "-A")
        _git(src, "commit", "-m", "add big module")
        return src

    @pytest.mark.asyncio
    async def test_preview_shows_two_phase_hint(self, tmp_path):
        """Preview mode should mention the two-commit strategy."""
        src = self._create_project_with_long_file(tmp_path)

        from code_refactor import auto_refactor
        # unwrap the mcp.tool() decorator — call the inner function
        fn = auto_refactor.__wrapped__ if hasattr(auto_refactor, '__wrapped__') else auto_refactor
        result = await fn(
            project_dir=str(src),
            apply=False,
            max_func_lines=15,
            max_file_lines=50,
        )

        assert "预览" in result
        # If both types detected, should hint about two commits
        if "函数拆分" in result and "文件拆分" in result:
            assert "#not-need-review" in result or "两个 commit" in result or "commit" in result

    @pytest.mark.asyncio
    async def test_apply_creates_separate_commits(self, tmp_path):
        """apply=True should create separate commits for file splits and func splits."""
        src = self._create_project_with_long_file(tmp_path)

        commits_before = _get_commits(src)

        from code_refactor import auto_refactor
        fn = auto_refactor.__wrapped__ if hasattr(auto_refactor, '__wrapped__') else auto_refactor
        result = await fn(
            project_dir=str(src),
            apply=True,
            backup=False,
            auto_commit=True,
            max_func_lines=15,
            max_file_lines=50,
        )

        commits_after = _get_commits(src)
        new_commits = commits_after[:len(commits_after) - len(commits_before)]

        assert "执行" in result
        # Should have created at least one commit
        assert len(new_commits) >= 1, f"Expected new commits, got none. Result:\n{result}"

        # Check commit messages for tags
        subjects = [c[1] for c in new_commits]
        all_subjects = "\n".join(subjects)

        # If both phases ran, should have both tags
        if "[Phase 1]" in result and "[Phase 2]" in result:
            assert any("#not-need-review" in s for s in subjects), \
                f"Expected #not-need-review commit, got: {subjects}"
            assert any("#need-review" in s for s in subjects), \
                f"Expected #need-review commit, got: {subjects}"
        elif "[Phase 1]" in result:
            assert any("#not-need-review" in s for s in subjects)
        elif "[Phase 2]" in result:
            assert any("#need-review" in s for s in subjects)

    @pytest.mark.asyncio
    async def test_auto_commit_false_skips_commits(self, tmp_path):
        """auto_commit=False should apply changes but not create git commits."""
        src = self._create_project_with_long_file(tmp_path)

        commits_before = _get_commits(src)

        from code_refactor import auto_refactor
        fn = auto_refactor.__wrapped__ if hasattr(auto_refactor, '__wrapped__') else auto_refactor
        result = await fn(
            project_dir=str(src),
            apply=True,
            backup=False,
            auto_commit=False,
            max_func_lines=15,
            max_file_lines=50,
        )

        commits_after = _get_commits(src)

        assert "执行" in result
        assert "[GIT]" not in result
        assert len(commits_after) == len(commits_before), \
            "auto_commit=False should not create new commits"

    @pytest.mark.asyncio
    async def test_no_actions_returns_clean(self, tmp_path):
        """Project with clean code should return '无需重构'."""
        src = tmp_path / "clean_project"
        src.mkdir()
        _init_git_repo(src)
        (src / "clean.py").write_text("def hello():\n    return 'world'\n")
        _git(src, "add", "-A")
        _git(src, "commit", "-m", "clean code")

        from code_refactor import auto_refactor
        fn = auto_refactor.__wrapped__ if hasattr(auto_refactor, '__wrapped__') else auto_refactor
        result = await fn(project_dir=str(src), apply=True)
        assert "无需重构" in result


# ─── Commit tag format tests ───────────────────────────────────────────────

class TestCommitTagFormat:
    """Test that commit messages follow the expected format."""

    def test_not_need_review_tag_in_subject_line(self, tmp_path):
        """#not-need-review should be in the first line (subject) for easy git log filtering."""
        from code_refactor import _git_commit

        _init_git_repo(tmp_path)
        (tmp_path / "moved.py").write_text("x = 1\n")

        msg = "refactor: move code to separate modules #not-need-review\n\n机械性移动"
        _git_commit(str(tmp_path), msg)

        # git log --oneline only shows subject line
        result = _git(tmp_path, "log", "-1", "--oneline")
        assert "#not-need-review" in result.stdout

    def test_need_review_tag_in_subject_line(self, tmp_path):
        """#need-review should be in the first line for easy filtering."""
        from code_refactor import _git_commit

        _init_git_repo(tmp_path)
        (tmp_path / "split.py").write_text("y = 2\n")

        msg = "refactor: split long functions #need-review\n\n逻辑性拆分"
        _git_commit(str(tmp_path), msg)

        result = _git(tmp_path, "log", "-1", "--oneline")
        assert "#need-review" in result.stdout

    def test_grep_filters_correctly(self, tmp_path):
        """git log --grep should correctly filter by tags."""
        from code_refactor import _git_commit

        _init_git_repo(tmp_path)

        # Create two commits with different tags
        (tmp_path / "a.py").write_text("a = 1\n")
        _git_commit(str(tmp_path), "refactor: move things #not-need-review")

        (tmp_path / "b.py").write_text("b = 2\n")
        _git_commit(str(tmp_path), "refactor: split funcs #need-review")

        # Filter for review-needed commits
        result = _git(tmp_path, "log", "--oneline", "--grep=#need-review")
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        assert len(lines) == 1
        assert "#need-review" in lines[0]
        assert "#not-need-review" not in lines[0]

        # Filter for skip-review commits
        result = _git(tmp_path, "log", "--oneline", "--grep=#not-need-review")
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        assert len(lines) == 1
        assert "#not-need-review" in lines[0]

        # Invert: show only commits that need review (exclude #not-need-review)
        result = _git(tmp_path, "log", "--oneline", "--grep=#not-need-review", "--invert-grep")
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        # Should contain the #need-review commit and the initial commit
        assert all("#not-need-review" not in l for l in lines)
