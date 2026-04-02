# 2-Day GitHub Push Schedule

## Repository: `agent-memory`

---

## Day 1 (Today) — Foundation + Core Push

### Block 1: Repo Setup (30 min)
- [ ] Create GitHub repo `agent-memory` (public, MIT license)
- [ ] Initialize git, set remote
- [ ] Push initial commit with .gitignore, LICENSE, README.md

### Block 2: Core SDK Commit (1 hr)
- [ ] Commit `src/memory/` — store.py, context_builder.py, condenser.py
- [ ] Commit `src/cache/` — prompt_cache.py, rate_limiter.py
- [ ] Commit `src/agents/` — base.py, local_llm.py, registry.py
- [ ] Commit `src/orchestrator/` — router.py
- [ ] Commit `src/metrics/` — tracker.py
- [ ] Tag: "Core SDK — memory compression, prompt caching, rate limiting"

### Block 3: Tests + Config (30 min)
- [ ] Commit `tests/` — test_memory.py, test_orchestrator.py
- [ ] Commit config files — pyproject.toml, setup.py, requirements.txt, config.yaml
- [ ] Run `pytest` in CI to verify green

### Block 4: Demos (30 min)
- [ ] Commit `run_demo.py` (dry-run + live mode)
- [ ] Commit `run_local.py` (Ollama demo)
- [ ] Verify both demos run clean locally

### Block 5: Plugin Infrastructure (1 hr)
- [ ] Commit `plugin/` — mcp_server.py, hooks/, plugin.json
- [ ] Test MCP server starts without errors
- [ ] Tag: "Plugin infrastructure — MCP server + hooks"

### Day 1 End State
- [ ] GitHub repo has 4-5 clean commits
- [ ] README renders properly on GitHub
- [ ] Tests pass (verify with `pytest`)
- [ ] Demo works locally

---

## Day 2 (Tomorrow) — Docs + Polish + Launch

### Block 1: Documentation (1.5 hr)
- [ ] Push API_REFERENCE.md
- [ ] Push MULTI_AGENT_ARCHITECTURE.md
- [ ] Push PRODUCT_STRATEGY.md (or keep private — your call)
- [ ] Review README for broken links, typos, formatting

### Block 2: GitHub Polish (1 hr)
- [ ] Add GitHub topics: `llm`, `token-optimization`, `multi-agent`, `claude`, `memory`, `mcp`
- [ ] Create GitHub release v0.1.0-alpha with changelog
- [ ] Add repo description: "Save 60-90% on LLM token costs with intelligent memory compression"
- [ ] Optional: Add social preview image

### Block 3: Package Distribution (30 min)
- [ ] Test `pip install -e .` works
- [ ] Test `pip install -e ".[dev]"` installs all deps
- [ ] Test `pip install -e ".[local]"` for Ollama users
- [ ] Optional: Publish to PyPI as `agent-memory` (test.pypi.org first)

### Block 4: Final Verification (30 min)
- [ ] Fresh clone → install → run tests → run demo (all green)
- [ ] Verify README Quick Start code blocks actually work
- [ ] Check no secrets/data files accidentally committed
- [ ] Push final commit

### Day 2 End State
- [ ] Public GitHub repo with clean history
- [ ] Full documentation (README + API ref + architecture)
- [ ] Working demos (dry-run, live API, local LLM)
- [ ] 14/14 tests passing
- [ ] Ready for Claude Code marketplace submission

---

## Commit History Plan

```
1. feat: initial project structure with LICENSE, README, .gitignore
2. feat: core memory SDK — store, context builder, condenser
3. feat: agent framework — base agent, local LLM, prompt caching, rate limiter
4. feat: orchestrator — DAG router with concurrent execution
5. feat: metrics tracker with token economics dashboard
6. test: full test suite — memory, orchestrator, cache validation
7. feat: demos — dry-run validation + Ollama live demo
8. feat: Claude Code plugin — MCP server + hooks
9. docs: API reference + architecture document
10. chore: pyproject.toml, setup.py, packaging config
```

Each commit is atomic and independently meaningful. No "WIP" or "fix typo" commits.

---

## Pre-Push Checklist

- [ ] `pytest tests/ -v` — all 14 pass
- [ ] `python run_demo.py` — 3/3 validations pass
- [ ] `python run_local.py --model phi4:latest` — runs with Ollama (if available)
- [ ] No `.db`, `.db-wal`, `__pycache__/`, or `data/` directories in git
- [ ] No API keys or secrets anywhere in committed files
- [ ] README renders correctly (check GitHub preview)
- [ ] All imports resolve (no missing `__init__.py`)
