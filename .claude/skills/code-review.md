---
name: code-review
description: Run a code quality review on a project or file using Code Hacker's MCP review tools
user_invocable: true
---

# Code Review Skill

Use the Code Hacker MCP servers (code-review on port 8005, code-refactor on port 8006) to perform code quality analysis.

## Prerequisites

- All 7 MCP servers must be running: `bash start_servers.sh`
- Verify with: `bash start_servers.sh status`

## Workflow

### Quick Health Check

```bash
# Check project health score (0-100)
curl -s http://localhost:8005/mcp -X POST \
  -H "Content-Type: application/json" \
  -d '{"method": "tools/call", "params": {"name": "health_score", "arguments": {"project_dir": "TARGET_DIR"}}}'
```

Or when used via DeepAgent / VS Code agent, the tools are called directly:

1. `health_score(project_dir)` — Quick 0–100 score
2. `review_project(project_dir)` — Full scan: health score + issue list + reorg suggestions
3. `find_complex_functions(project_dir)` — Top N highest complexity functions
4. `find_long_functions(project_dir)` — Top N longest functions
5. `review_function(file_path, function_name)` — Deep analysis with refactoring suggestions
6. `review_diff_text(old_code, new_code)` — Compare old/new code structural changes

### Full Review Process

1. **Get the big picture**: Run `health_score` for a quick overview
2. **Scan for issues**: Run `review_project` for comprehensive findings
3. **Find hotspots**: Run `find_complex_functions` and `find_long_functions`
4. **Deep dive**: Run `review_function` on the most problematic functions
5. **Generate structural diff**: Run `ydiff_commit` or `ydiff_files` for visual diff reports
6. **Preview refactoring**: Run `auto_refactor(apply=False)` before executing

### Reviewing AI-Generated Code

When reviewing code changes (e.g., from AI pair programming):

1. `review_diff_text(old_code, new_code)` — Quantify structural changes
2. `review_function(file, func)` — Deep analysis of problematic functions
3. Fix issues with `edit_file` — Precise replacements

## Output

Provide a summary with:
- Health score (X/100, grade)
- Top issues by severity (critical / medium / minor)
- Specific refactoring suggestions with file:line references
- Actionable next steps
