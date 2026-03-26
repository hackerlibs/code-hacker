---
name: multi-project
description: Set up and manage multi-project workspaces for cross-repo coordination
user_invocable: true
---

# Multi-Project Workspace Skill

Use Code Hacker's multi-project MCP server (port 8007) to coordinate work across multiple repositories.

## Prerequisites

- MCP servers running: `bash start_servers.sh`
- At least 2 project directories to coordinate

## Workflow

### Step 1: Register Projects

```
workspace_add(project_path="/path/to/frontend", alias="frontend", role="app", description="React frontend")
workspace_add(project_path="/path/to/backend", alias="backend", role="api", description="FastAPI backend")
workspace_add(project_path="/path/to/shared-lib", alias="lib", role="library", description="Shared utilities")
```

### Step 2: Explore the Workspace

```
workspace_list()              — See all registered projects with git status
workspace_overview()          — High-level overview: languages, configs, structure
```

### Step 3: Cross-Project Search

```
workspace_search(pattern="buildHelper")           — Regex search across all repos
workspace_find_files(pattern="*.py")               — Glob search across all repos
workspace_find_dependencies(symbol="buildHelper")  — Trace a symbol across repos
```

### Step 4: Coordinated Edits

```
workspace_read_file(project="lib", file_path="src/helper.py")
workspace_edit_file(project="lib", file_path="src/helper.py", old_string="...", new_string="...")
workspace_edit_file(project="frontend", file_path="src/api.ts", old_string="...", new_string="...")
```

### Step 5: Verify and Commit

```
workspace_git_status()        — Bird's-eye view of changes across all repos
workspace_git_diff()          — Diff summary across repos
workspace_commit(projects="lib,frontend", message="feat: update helper API and frontend consumer")
```

## Common Scenarios

### Jenkinsfile + Library
Register the shared library and the project with the Jenkinsfile. Use `workspace_find_dependencies` to trace function usage, then make coordinated edits.

### Frontend + Backend
Register both repos. Use `workspace_search` to find API endpoint definitions and their consumers, then update both sides atomically.

### Microservices
Register all services. Use `workspace_find_files("docker-compose*.yml")` and `workspace_search("SERVICE_NAME")` to understand service topology.

## Key Principles

- **Always check cross-project impact** before making changes
- Use `workspace_find_dependencies` to trace symbols across repos
- Use `workspace_git_status` to verify all changes before committing
- Coordinated commits keep related changes in sync across repos
