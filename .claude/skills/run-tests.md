---
name: run-tests
description: Run the LLM-powered integration test suite for Code Hacker
user_invocable: true
---

# Run Tests Skill

Run the Code Hacker integration test suite. These are LLM-powered tests that exercise real scenarios through the DeepAgent.

## Prerequisites

- All 7 MCP servers running: `bash start_servers.sh`
- `OPENROUTER_API_KEY` (or `OPENAI_API_KEY`) set in environment
- Dependencies installed: `uv sync`

## Running Tests

```bash
# Run all 13 test scenarios
NO_PROXY=localhost,127.0.0.1 uv run pytest tests/test_scenarios.py -v -s

# Run a single scenario
NO_PROXY=localhost,127.0.0.1 uv run pytest tests/test_scenarios.py::test_ydiff_commit_review -v -s

# Run with shorter timeout (default is per-scenario)
NO_PROXY=localhost,127.0.0.1 uv run pytest tests/test_scenarios.py -v -s -k "health_score"
```

## Test Scenarios

The 13 scenarios cover:

1. **ydiff commit review** — Structural diff of a git commit
2. **project health score** — Quick 0–100 project quality score
3. **git history investigation** — Deep dive into git history
4. **Python AST analysis** — AST-level code analysis
5. **cross-file search** — Search patterns across files
6. **dependency graph** — File import relationship analysis
7. **workspace registration** — Multi-project workspace setup
8. **memory save/recall** — Persistent memory round-trip
9. **Jenkinsfile pipeline generation** — Cross-project code generation
10. **complex function detection** — Find highest complexity functions
11. **git blame** — Line-by-line attribution
12. **QA experience recording** — Save problem-solving patterns
13. **project overview** — Full project panorama

## Troubleshooting

- **502 errors**: Set `NO_PROXY=localhost,127.0.0.1` before the command
- **Connection refused**: Check `bash start_servers.sh status` — all 7 must be UP
- **API key errors**: Verify `OPENROUTER_API_KEY` is set
- **Timeout**: These tests call real LLMs, so they can be slow. Be patient or run individual scenarios.

## Writing New Tests

Tests live in `tests/test_scenarios.py` and use fixtures from `tests/conftest.py`:

```python
@pytest.mark.asyncio
async def test_my_scenario(run_agent_query):
    result = await run_agent_query("Your prompt to the agent here")
    assert "expected_keyword" in result.lower()
```

The `run_agent_query` fixture creates a DeepAgent session with all MCP tools loaded.
