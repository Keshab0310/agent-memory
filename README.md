# agent-memory

**Save 60-90% on LLM token costs** with intelligent memory compression for multi-agent systems.

agent-memory compresses raw LLM tool output into structured observations, shares context across agents via a memory bus, and injects only relevant memory into each prompt — keeping your token budget under control.

---

## The Problem

Running 5+ concurrent LLM agents burns tokens fast:
- Each agent re-reads the same files, re-discovers the same context
- Raw tool output (file reads, command results) consumes thousands of tokens
- No shared memory means redundant API calls across agents
- You hit rate limits and token budgets within minutes

## The Solution

agent-memory sits between your agents and their context window:

```
Raw Tool Output (5,000 tokens)
  -> Observation Compression (500 tokens)
    -> Shared Memory Bus (SQLite + FTS5)
      -> Budget-Controlled Context Injection (8,000 token cap)
```

**Tested results**: 66-94% token savings, 3-74x compression ratio.

---

## Quick Start

### As a Python SDK

```bash
pip install agent-memory
```

```python
from agent_memory import MemoryStore, ContextBuilder

# Initialize
memory = MemoryStore("./my_project.db")

# Store a compressed observation
memory.store_observation(Observation(
    agent_id="researcher-1",
    project="my-app",
    title="Found pagination bug in /users endpoint",
    narrative="The API returns 500 when page > 100 due to missing LIMIT clause",
    facts=["Max page size is 100", "No server-side validation"],
    concepts=["api", "bug", "pagination"],
))

# Build context for another agent (token-budgeted)
builder = ContextBuilder(memory)
context = builder.build(
    project="my-app",
    agent_id="coder-1",
    task_description="Fix the pagination bug",
)
# -> Returns compressed context within 8000 token budget
# -> Includes researcher-1's findings automatically
```

### As a Claude Code Plugin

```bash
# Install from marketplace (coming soon)
claude plugin install agent-memory
```

The plugin automatically:
- Compresses tool output after every tool call (PostToolUse hook)
- Injects relevant memory at session start (SessionStart hook)
- Exposes `memory_search`, `memory_store`, `memory_stats` as MCP tools

### With Local LLMs (Ollama, LM Studio)

```python
from agent_memory import LocalLLMAgent, AgentConfig, MemoryStore

memory = MemoryStore("./local.db")
agent = LocalLLMAgent(
    config=AgentConfig(agent_type="researcher", model="phi4:latest"),
    memory=memory,
    project="my-app",
    base_url="http://localhost:11434/v1",  # Ollama
)
result = agent.execute("What are the key design patterns in this codebase?")
```

---

## How It Works

### 1. Observation Compression

Raw tool output (file reads, command results, API responses) gets compressed into structured observations:

```
[discovery] Found pagination bug in /users endpoint
  API returns 500 when page > 100 due to missing LIMIT clause
  - Max page size is 100
  - No server-side validation
```

A 5,000-token file read becomes a 200-token observation. That's a **25x compression ratio**.

### 2. Shared Memory Bus

All agents write to and read from a shared memory store:

```
SQLite (structured queries) + FTS5 (full-text search)
  |
  +-- Optional: ChromaDB (semantic vector search)
```

Agent B sees what Agent A discovered — no re-querying needed.

### 3. Token-Budgeted Context Injection

Before each agent call, the ContextBuilder assembles a minimal context window:

| Budget Slot | Tokens | Content |
|-------------|--------|---------|
| Task description | 800 | Current task |
| Own observations | 4,000 | Agent's recent work |
| Cross-agent context | 2,400 | Other agents' relevant findings |
| Summaries | 800 | Condensed session history |
| **Total** | **8,000** | **4% of Sonnet's 200K window** |

### 4. Prompt Caching (Anthropic)

Static content gets cache breakpoints for 90% input cost reduction:

```
System prompt     [CACHED - 10% cost]
Shared context    [CACHED - 10% cost]
Agent memory      [dynamic - full cost]
User message      [dynamic - full cost]
```

---

## Architecture

```
src/
  memory/
    store.py            # SQLite + ChromaDB + FTS5 memory layer
    context_builder.py  # Token-budgeted context injection
    condenser.py        # Periodic summarization pipeline
  agents/
    base.py             # Base agent with memory integration
    local_llm.py        # Ollama/LM Studio adapter
    registry.py         # Agent type definitions
  cache/
    prompt_cache.py     # Anthropic prompt caching wrapper
    rate_limiter.py     # Token bucket RPM/TPM limiter
  orchestrator/
    router.py           # DAG-based multi-agent task router
  metrics/
    tracker.py          # Token/cache/latency tracking
plugin/
  mcp_server.py         # MCP server for Claude Code
  hooks/
    post_tool_use.py    # Auto-compress tool output
    session_start.py    # Inject memory at session start
  plugin.json           # Claude Code plugin manifest
tests/
  test_memory.py        # Memory store + search + condensation tests
  test_orchestrator.py  # DAG execution + cache structure tests
```

---

## Key Features

| Feature | Status |
|---------|--------|
| Observation compression (XML/auto-extract) | Working |
| SQLite + FTS5 search | Working |
| ChromaDB semantic search (optional) | Working |
| Token-budgeted context injection | Working |
| Anthropic prompt caching | Working |
| Rate limiter (RPM + TPM) | Working |
| Periodic condensation | Working |
| Local LLM support (Ollama/LM Studio) | Working |
| Multi-agent shared memory | Working |
| DAG-based orchestrator | Working |
| Metrics dashboard | Working |
| Claude Code plugin (MCP + hooks) | Beta |

---

## Running the Demos

### Dry-run validation (no API key needed)

```bash
python run_demo.py
```

### Live with Anthropic API

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python run_demo.py --live
```

### Local LLM (Ollama)

```bash
ollama serve  # In another terminal
python run_local.py --model phi4:latest
```

---

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

All 14 tests cover: memory CRUD, agent isolation, condensation, semantic search, token budgets, cache breakpoints, DAG execution, and metrics logging.

---

## Configuration

### Memory Budget

```python
from agent_memory import ContextBudget

budget = ContextBudget(
    total=8000,           # Total token budget for context
    task_description=800, # Reserved for task text
    own_observations=4000,# Agent's own recent work
    cross_agent=2400,     # Other agents' relevant findings
    summaries=800,        # Condensed history
)
```

### Rate Limiting

```python
from agent_memory.cache import RateLimiter

limiter = RateLimiter(
    requests_per_minute=50,
    tokens_per_minute=80_000,
)
limiter.acquire_sync(estimated_tokens=4000)  # Blocks until slot available
```

### Agent Types

Built-in: `researcher`, `coder`, `reviewer`, `summarizer`, `planner`. Custom:

```python
config = AgentConfig(
    agent_type="my-agent",
    model="claude-sonnet-4-6-20250514",
    max_output_tokens=2000,
    system_prompt="You are a specialized agent for...",
)
```

---

## Benchmarks

Measured on real workloads (not synthetic):

| Metric | Result |
|--------|--------|
| Token savings (compression) | 66-94% |
| Compression ratio | 3:1 to 74:1 |
| Prompt cache hit rate | 23-35% |
| Cache cost reduction | 0.71x |
| Context budget utilization | 43% avg |
| Cross-agent memory sharing | 100% (all agents see shared pool) |

---

## API Reference

See [API_REFERENCE.md](./API_REFERENCE.md) for the complete SDK documentation.

---

## Contributing

1. Fork the repo
2. Create a feature branch
3. Run tests: `pytest tests/ -v`
4. Submit a PR

---

## License

MIT - see [LICENSE](./LICENSE)
