---
name: ydiff-review
description: Generate structural AST-level diff reports using the ydiff engine
user_invocable: true
---

# YDiff Structural Diff Skill

Use Code Hacker's ydiff engine (code-review server, port 8005) to generate AST-level structural diff reports. Unlike line-level diffs, ydiff understands code moves, renames, and structural changes.

## Prerequisites

- MCP servers running: `bash start_servers.sh`
- Target files/commits must be Python files (ydiff uses Python AST)

## Tools

### Compare Two Files

```
ydiff_files(file_a="/path/to/old.py", file_b="/path/to/new.py")
```

Compares two Python files at the AST level. Returns an interactive HTML report showing:
- Added/removed/modified functions and classes
- Moved code detection
- Complexity changes per function

### Review a Git Commit

```
ydiff_commit(project_dir="/path/to/repo", commit_id="HEAD")
ydiff_commit(project_dir="/path/to/repo", commit_id="abc1234")
```

Generates a multi-file structural diff for all Python files changed in a commit. The HTML report is saved to the project directory.

### Compare Two Git Refs

```
ydiff_git_changes(project_dir="/path/to/repo", ref_from="main", ref_to="feature-branch")
ydiff_git_changes(project_dir="/path/to/repo", ref_from="HEAD~5", ref_to="HEAD")
```

Compares structural changes across any two git refs (branches, tags, commit SHAs).

## Output

All ydiff commands generate interactive HTML reports with:
- Side-by-side structural comparison
- Function-level change highlighting
- Complexity direction indicators (e.g., complexity 8→15↑)
- Added/removed/modified function counts

The HTML file path is returned in the tool response. Open it in a browser to view.

## When to Use

- **Code review**: Understand what structurally changed in a PR or commit
- **Refactoring verification**: Confirm that a refactor didn't accidentally change behavior
- **AI code review**: Compare before/after versions of AI-generated code
- **Historical analysis**: See how code structure evolved between releases
