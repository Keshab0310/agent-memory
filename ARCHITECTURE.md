# Multi-Agent Orchestration Architecture

## Token-Efficient LLM Pipeline Based on claude-mem Memory Principles

---

## 1. Repository Analysis: claude-mem Architecture

### Core Design Principles Extracted

After analyzing the full claude-mem codebase (v10.6.3), these are the architectural patterns most relevant to multi-agent token optimization:

#### 1.1 Observation Compression Pipeline
claude-mem's central insight: **raw tool output is 10-100x larger than its semantic content**. The system achieves ~86% token savings by running a dedicated "observer" Claude agent that watches the primary session and compresses tool interactions into structured observations.

**Flow:**
```
Raw Tool Call (1000s of tokens)
  → SDK Observer Agent (separate Claude session)
    → XML <observation> with type/title/facts/narrative/concepts
      → SQLite (structured storage)
      → ChromaDB (vector embeddings per field)
```

Key files analyzed:
- `src/sdk/prompts.ts` — Prompt templates for observation extraction and summarization
- `src/sdk/parser.ts` — XML parser for structured observation/summary extraction
- `src/services/context/ContextBuilder.ts` — Assembles minimal context from stored observations
- `src/services/context/TokenCalculator.ts` — Tracks discovery_tokens vs read_tokens (ROI)
- `src/services/context/ObservationCompiler.ts` — Queries observations with type/concept filtering

#### 1.2 Tiered Storage Architecture
```
┌─────────────────────────────────────────────┐
│  Layer 1: Working Memory (Context Window)   │
│  - Most recent N observations (configurable)│
│  - Latest session summary                   │
│  - Prior assistant message                  │
│  Budget: ~2-4K tokens via ContextConfig     │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  Layer 2: SQLite (Structured Metadata)      │
│  - All observations with full fields        │
│  - Session summaries                        │
│  - User prompts                             │
│  - Indexed by project, type, concept, epoch │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  Layer 3: ChromaDB (Semantic Vector Search) │
│  - Granular documents per observation field │
│  - Narrative, facts, concepts as separate   │
│    vector embeddings                        │
│  - Hybrid search: metadata filter → vector  │
│    ranking → intersection → hydration       │
└─────────────────────────────────────────────┘
```

#### 1.3 Token Economics Model
claude-mem tracks two metrics per observation:
- **discovery_tokens**: Total tokens consumed to generate the observation (input+output of observer agent)
- **read_tokens**: Tokens required to inject the compressed observation into context (~4 chars/token estimate)

The savings ratio is: `(discovery_tokens - read_tokens) / discovery_tokens`

In practice, the session header shows stats like: `50 obs (23,043t read) | 160,452t work | 86% savings`

#### 1.4 Search Strategy Pattern
The `SearchOrchestrator` uses a strategy pattern with automatic fallback:
1. **HybridSearchStrategy**: SQLite metadata filter → Chroma semantic ranking → intersection
2. **ChromaSearchStrategy**: Pure vector similarity search
3. **SQLiteSearchStrategy**: Structured queries only (fallback when Chroma unavailable)

This is directly applicable to multi-agent memory retrieval where different agents need different recall strategies.

---

## 2. Multi-Agent Architecture Design

### 2.1 System Overview

```
                    ┌──────────────────────┐
                    │   Orchestrator       │
                    │   (Router + Policy)  │
                    └──────┬───────────────┘
                           │
              ┌────────────┼────────────────┐
              │            │                │
        ┌─────▼────┐ ┌────▼─────┐  ┌──────▼──────┐
        │ Agent A   │ │ Agent B  │  │  Agent N    │
        │ (Research)│ │ (Code)   │  │  (Review)   │
        └─────┬────┘ └────┬─────┘  └──────┬──────┘
              │            │                │
              └────────────┼────────────────┘
                           │
              ┌────────────▼────────────────┐
              │     Shared Memory Bus       │
              │  ┌────────┐  ┌───────────┐  │
              │  │ SQLite  │  │ ChromaDB  │  │
              │  │(struct) │  │ (vector)  │  │
              │  └────────┘  └───────────┘  │
              └─────────────────────────────┘
```

### 2.2 Memory Architecture: Shared vs. Episodic

| Memory Type | Scope | Storage | Access Pattern |
|-------------|-------|---------|----------------|
| **Shared Context** | All agents | SQLite + Chroma | Read-heavy, append-only |
| **Agent Episodic** | Single agent | Per-agent SQLite partition | Read-write within session |
| **Working Memory** | Single agent turn | In-context window | Injected per-request |
| **Task State** | Orchestrator | SQLite task table | Orchestrator read-write |

#### Shared Memory
All agents contribute observations to a shared pool, tagged with `agent_id` and `project`. Any agent can query any other agent's observations via the SearchOrchestrator. This enables:
- Agent B can see what Agent A discovered without re-running the same queries
- The orchestrator can build a unified timeline across all agents

#### Episodic Memory (Per-Agent)
Each agent maintains its own conversation history and working context. This is critical because:
- Different agents have different system prompts (token overhead if shared)
- Agent-specific context (e.g., code agent's file state) is noise for other agents
- Prevents context pollution between concurrent agents

#### Memory Isolation Implementation
```python
# Partition key for all memory operations
class MemoryKey:
    project: str          # e.g., "my-app"
    agent_id: str         # e.g., "research-agent-1"
    session_id: str       # e.g., "sess_abc123"
    
# Shared queries: WHERE project = ?
# Episodic queries: WHERE project = ? AND agent_id = ?
# Session queries: WHERE session_id = ?
```

### 2.3 Agent Lifecycle

```
1. SPAWN      → Orchestrator creates agent with role + task + injected context
2. EXECUTE    → Agent runs, produces tool calls, generates observations
3. COMPRESS   → Observation pipeline compresses raw output to structured form
4. BROADCAST  → Compressed observations written to shared memory
5. CHECKPOINT → Agent's episodic state summarized at configurable intervals
6. COMPLETE   → Final summary written, resources released
```

### 2.4 Orchestrator Design

The orchestrator is the only component that talks to the Anthropic API for routing decisions. It uses a **lightweight model** (Haiku) for routing, reserving Sonnet/Opus for agent work.

```python
class Orchestrator:
    def route(self, user_request: str) -> AgentPlan:
        """Determine which agents to spawn and in what order."""
        # Uses Haiku for fast, cheap routing decisions
        # Returns a DAG of agent tasks with dependencies
        
    def inject_context(self, agent: Agent, task: Task) -> str:
        """Build minimal context for an agent from shared memory."""
        # Query only relevant observations from shared memory
        # Apply token budget constraints
        # Use prompt caching for static portions
        
    def merge_results(self, results: list[AgentResult]) -> str:
        """Synthesize outputs from multiple agents."""
```

---

## 3. Token Optimization Blueprint

### 3.1 Anthropic Prompt Caching Implementation

Anthropic's prompt caching is the **single highest-impact optimization** for multi-agent systems. Static content marked with cache breakpoints is stored server-side and charged at 10% of normal input token cost on cache hits.

#### What to Cache (with breakpoints)

```python
# TIER 1: System prompt (identical across all calls for same agent type)
# Cache hit rate: ~99% for agents of same type
system_prompt = {
    "type": "text",
    "text": AGENT_SYSTEM_PROMPT,
    "cache_control": {"type": "ephemeral"}  # 5-min TTL
}

# TIER 2: Shared project context (changes infrequently)
# Cache hit rate: ~90% within a task session
shared_context = {
    "type": "text",
    "text": build_shared_context(project),
    "cache_control": {"type": "ephemeral"}
}

# TIER 3: Agent-specific episodic context (changes per turn)
# NOT cached — this is the dynamic portion
episodic_context = {
    "type": "text",
    "text": build_episodic_context(agent)
    # No cache_control — always fresh
}
```

#### Token Savings Projection

For a 5-agent system with 10 turns each:

| Component | Tokens/call | Calls | Without Cache | With Cache |
|-----------|------------|-------|---------------|------------|
| System Prompt | 2,000 | 50 | 100,000 | 10,000 + 2,000 (first write) |
| Shared Context | 3,000 | 50 | 150,000 | 15,000 + 3,000 |
| Episodic | 1,500 | 50 | 75,000 | 75,000 (not cached) |
| **Total Input** | | | **325,000** | **105,000** |
| **Savings** | | | | **67% reduction** |

### 3.2 Tiered Memory Architecture

Adapted from claude-mem's ContextBuilder pattern:

```
┌─────────────────────────────────────────────────────┐
│  TIER 0: Prompt Cache (Anthropic server-side)       │
│  - System prompts, tool definitions, shared rules   │
│  - Cost: 0.1x on cache hit                          │
│  - TTL: 5 minutes (ephemeral)                       │
│  Budget: Unlimited (it's cached)                    │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│  TIER 1: Working Memory (injected into context)     │
│  - Last 3-5 compressed observations from THIS agent │
│  - Current task state and dependencies              │
│  - Relevant observations from OTHER agents (max 3)  │
│  Budget: 2,000 tokens (hard limit)                  │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│  TIER 2: Warm Storage (SQLite, queried on demand)   │
│  - All observations for this project                │
│  - Session summaries and task state                 │
│  - Queried via SearchOrchestrator when agent needs  │
│    historical context                               │
│  Budget: Query returns max 10 results               │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│  TIER 3: Cold Storage (ChromaDB, semantic search)   │
│  - Full vector embeddings of all observations       │
│  - Cross-project semantic search                    │
│  - Used for "find similar past work" queries        │
│  Budget: Query returns max 5 results                │
└─────────────────────────────────────────────────────┘
```

### 3.3 Dynamic Context Injection

Instead of dumping all memory into every agent call, use **relevance-scored injection**:

```python
def build_agent_context(agent: Agent, task: Task) -> list[dict]:
    """Build minimal context window for an agent."""
    
    context_parts = []
    budget_remaining = MAX_WORKING_MEMORY_TOKENS  # 2000
    
    # 1. Task description (always included)
    task_desc = format_task(task)
    budget_remaining -= estimate_tokens(task_desc)
    context_parts.append(task_desc)
    
    # 2. This agent's recent observations (most relevant)
    own_obs = query_observations(
        project=task.project,
        agent_id=agent.id,
        limit=5,
        order_by="created_at_epoch DESC"
    )
    for obs in own_obs:
        obs_text = format_observation_compact(obs)
        tokens = estimate_tokens(obs_text)
        if tokens <= budget_remaining:
            context_parts.append(obs_text)
            budget_remaining -= tokens
    
    # 3. Cross-agent observations (only if budget allows)
    if budget_remaining > 200:
        cross_obs = semantic_search(
            query=task.description,
            project=task.project,
            exclude_agent=agent.id,
            limit=3
        )
        for obs in cross_obs:
            obs_text = format_observation_compact(obs)
            tokens = estimate_tokens(obs_text)
            if tokens <= budget_remaining:
                context_parts.append(f"[From {obs.agent_id}]: {obs_text}")
                budget_remaining -= tokens
    
    return context_parts
```

### 3.4 Periodic Summarization/Condensation

Adapted from claude-mem's `buildSummaryPrompt`:

```
Every N turns (configurable, default=5):
  1. Agent receives SUMMARIZE signal
  2. Agent produces <summary> XML block
  3. Summary replaces the N observations it covers
  4. Previous working memory is evicted
  5. Summary becomes new "floor" for context

This bounds context growth: O(1) instead of O(turns)
```

**Condensation Pipeline:**
```python
async def condense_agent_memory(agent: Agent, session: Session):
    """Run at checkpoint intervals to prevent context explosion."""
    
    # Get observations since last condensation
    recent_obs = get_observations_since(agent.last_condensation_epoch)
    
    if len(recent_obs) < CONDENSATION_THRESHOLD:  # e.g., 5
        return
    
    # Ask the agent to summarize (uses the same session)
    summary_prompt = build_summary_prompt(
        observations=recent_obs,
        task=session.current_task
    )
    
    summary = await agent.generate(summary_prompt)
    parsed = parse_summary_xml(summary)
    
    # Store summary, mark observations as condensed
    store_summary(parsed, agent_id=agent.id, session_id=session.id)
    mark_observations_condensed(recent_obs)
    
    agent.last_condensation_epoch = time.time()
```

### 3.5 Additional Optimizations

#### A. Output Token Control
```python
# Set max_tokens per agent type to prevent verbose responses
AGENT_TOKEN_LIMITS = {
    "router": 500,        # Only needs to output a plan
    "researcher": 2000,   # Needs detail but bounded
    "coder": 4000,        # Code output needs room
    "reviewer": 1000,     # Structured feedback
    "summarizer": 500,    # Concise by design
}
```

#### B. Tool Output Truncation
```python
# Before passing tool results to the agent, truncate large outputs
def truncate_tool_output(output: str, max_chars: int = 5000) -> str:
    if len(output) <= max_chars:
        return output
    return output[:max_chars] + f"\n... [truncated {len(output) - max_chars} chars]"
```

#### C. Batch API for Non-Interactive Work
For agents that don't need real-time responses (e.g., background research), use Anthropic's Batch API at 50% cost:
```python
# Queue batch requests for background agents
batch_requests = [
    {"custom_id": f"agent_{i}", "params": agent_params}
    for i, agent_params in enumerate(background_agents)
]
batch = client.messages.batches.create(requests=batch_requests)
```

---

## 4. Implementation Plan

### 4.1 Directory Structure

```
multi_agent_sketch/
├── src/
│   ├── orchestrator/
│   │   ├── router.py          # Agent routing and task planning
│   │   └── merger.py          # Result synthesis
│   ├── memory/
│   │   ├── store.py           # SQLite + Chroma memory layer
│   │   ├── context_builder.py # Dynamic context injection
│   │   └── condenser.py       # Periodic summarization
│   ├── agents/
│   │   ├── base.py            # Base agent with memory integration
│   │   └── registry.py        # Agent type definitions
│   ├── cache/
│   │   └── prompt_cache.py    # Anthropic prompt caching wrapper
│   └── metrics/
│       └── tracker.py         # Token/latency/accuracy tracking
├── config.yaml                # Agent definitions and budgets
├── requirements.txt
└── tests/
    ├── test_orchestrator.py
    ├── test_memory.py
    └── test_metrics.py
```

### 4.2 Implementation Phases

**Phase 1 (Core):** Memory store + base agent + prompt caching
**Phase 2 (Orchestration):** Router + multi-agent lifecycle management
**Phase 3 (Optimization):** Condensation pipeline + dynamic context injection
**Phase 4 (Observability):** Metrics tracking + dashboards

---

## 5. Testing & Validation Metrics

### 5.1 Token Consumption Metrics

```python
@dataclass
class TokenMetrics:
    # Per-request metrics
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    
    # Derived
    @property
    def effective_input_cost(self) -> float:
        """Actual cost accounting for cache hits."""
        return (self.input_tokens * 1.0 +
                self.cache_creation_tokens * 1.25 +
                self.cache_read_tokens * 0.1)
    
    @property
    def cache_hit_rate(self) -> float:
        total = self.input_tokens + self.cache_read_tokens
        return self.cache_read_tokens / total if total > 0 else 0
```

### 5.2 Key Performance Indicators

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Cache Hit Rate | >80% | `cache_read_tokens / total_input_tokens` |
| Token Savings vs Naive | >60% | `1 - (total_with_system / total_without_system)` |
| Memory Retrieval Accuracy | >85% | Compare agent decisions with/without memory |
| Compression Ratio | >5:1 | `discovery_tokens / read_tokens` per observation |
| Agent Latency (p95) | <10s | Time from task dispatch to agent completion |
| Context Utilization | <80% | `actual_context / max_context_window` |
| Cross-Agent Reuse Rate | >30% | Observations read by agents other than creator |

### 5.3 Testing Framework

```python
class TokenBudgetTest:
    """Verify agents stay within token budgets."""
    
    def test_working_memory_budget(self):
        context = build_agent_context(agent, task)
        tokens = count_tokens(context)
        assert tokens <= MAX_WORKING_MEMORY_TOKENS
    
    def test_cache_hit_after_warmup(self):
        # First call creates cache
        r1 = agent.execute(task)
        # Second call should hit cache
        r2 = agent.execute(task_2)
        assert r2.metrics.cache_read_tokens > 0
        assert r2.metrics.cache_hit_rate > 0.5
    
    def test_condensation_bounds_growth(self):
        for i in range(20):
            agent.execute(make_task(i))
        # After 20 turns with threshold=5, should have ~4 summaries
        assert agent.observation_count < 10  # Not 20
    
    def test_cross_agent_memory_visibility(self):
        # Agent A stores an observation
        agent_a.store_observation(obs)
        # Agent B should find it via semantic search
        results = memory.search(query=obs.title, exclude_agent=agent_a.id)
        assert len(results) == 0  # Excluded
        results = memory.search(query=obs.title)
        assert len(results) > 0   # Found in shared pool
```

### 5.4 Logging Schema

Every API call logs:
```json
{
    "timestamp": "2026-04-01T12:00:00Z",
    "agent_id": "research-1",
    "session_id": "sess_abc",
    "request_type": "messages.create",
    "model": "claude-sonnet-4-6-20250514",
    "input_tokens": 1500,
    "output_tokens": 800,
    "cache_creation_tokens": 2000,
    "cache_read_tokens": 1800,
    "latency_ms": 3200,
    "memory_observations_injected": 3,
    "memory_observations_created": 1,
    "context_utilization_pct": 42
}
```

Aggregation queries:
```sql
-- Total cost per agent type
SELECT agent_type,
       SUM(input_tokens + output_tokens) as total_tokens,
       SUM(cache_read_tokens) as cached_tokens,
       AVG(latency_ms) as avg_latency
FROM api_calls
GROUP BY agent_type;

-- Cache effectiveness over time
SELECT DATE(timestamp) as day,
       SUM(cache_read_tokens) * 1.0 / SUM(input_tokens + cache_read_tokens) as hit_rate
FROM api_calls
GROUP BY day;
```

---

## 6. Expert Review Findings & Applied Fixes

Two expert agents (Senior Developer + AI Engineer) performed an independent review.
All critical fixes below have been applied to the codebase.

### 6.1 Critical Fixes Applied

| Fix | File | What Changed |
|-----|------|-------------|
| SQLite thread safety | `store.py` | WAL mode, per-thread connections via `threading.local()`, write lock |
| ChromaDB write safety | `store.py` | All writes serialized under `_write_lock` |
| ChromaDB optional | `store.py` | Falls back to FTS5/LIKE search if ChromaDB unavailable |
| FTS5 search index | `store.py` | Added `observations_fts` virtual table for no-dependency search |
| Condenser error handling | `condenser.py` | `check_and_condense()` + `_condense()` wrapped in try/except |
| Condenser lazy client | `condenser.py` | `anthropic.Anthropic()` lazily initialized — no crash without API key |
| DAG deadlock fix | `router.py` | Failed dependencies propagate to pending tasks |
| Staggered execution | `router.py` | 500ms stagger between concurrent agent launches |
| Rate limiter | `rate_limiter.py` | New: token bucket RPM/TPM limiter with exponential backoff |
| Working memory 4x | `context_builder.py` | Budget increased from 2000 to 8000 tokens (4% of 200K window) |
| Metrics agent_type | `store.py`, `tracker.py` | Stored as separate column, not fragile string parsing |

### 6.2 Claude Code Plugin Roadmap

**The current orchestrator model CANNOT be a plugin as-is.** Claude Code plugins work via hooks + MCP servers, not by controlling the full prompt pipeline. The path forward:

```
Phase 1: Extract core (MemoryStore + ContextBuilder + MetricsTracker)
Phase 2: Build MCP server exposing memory_search, memory_store, memory_condense tools
Phase 3: Build hooks:
  - PostToolUse: auto-extract observations from tool results
  - UserPromptSubmit: inject compressed context via ContextBuilder
  - SessionEnd: run condensation, flush metrics
Phase 4: Package as Claude Code plugin with plugin.json manifest
```

**What gets DROPPED**: The orchestrator, prompt cache layer, and agent base class.
Claude Code already handles agent orchestration and prompt construction.

**What gets KEPT**: MemoryStore (the compression + search engine), ContextBuilder,
MetricsTracker, and the condensation pipeline. These are the value.

### 6.3 If This Approach Doesn't Work: Alternatives

Ranked by likelihood of solving the token exhaustion problem:

1. **Single-agent-with-tools** (simplest): Skip multi-agent entirely. One Sonnet agent with well-defined tools often outperforms 5 poorly-coordinated agents. Zero orchestration overhead, maximum cache hit rate, no shared memory needed. Try this FIRST.

2. **Agent-as-a-tool pattern**: One orchestrator agent invokes sub-agents as tools. The orchestrator maintains coherent state; sub-agents are stateless and disposable. Simpler than peer-to-peer multi-agent, works natively with Claude's tool_use.

3. **MCP server memory**: Expose the memory store as an MCP server that Claude Code queries on demand. No hooks needed — the model decides when to check memory. Lower engineering effort than a full plugin.

4. **Drop ChromaDB, use FTS5 only**: For <1000 observations, SQLite FTS5 provides 90% of retrieval quality with zero dependencies. Already implemented as fallback in `store.py`.

5. **Batch API for background agents**: Submit non-interactive work as batch requests for 50% cost reduction. Not yet implemented but the DAG router's `TaskNode` can be extended with a `batch_eligible` flag.

### 6.4 Token Reduction Strategies Not Yet Implemented

| Strategy | Est. Savings | Effort |
|----------|-------------|--------|
| JSON mode instead of XML observations | 20% output tokens | 2hr |
| Tool result structural truncation | 30-50% input tokens | 3hr |
| Anthropic `count_tokens()` API | Better budget accuracy | 1hr |
| Batch API for research agents | 50% cost on batch | 4hr |
| Separate Haiku observer for observation extraction | 70% extraction cost | 3hr |
| Extended thinking with budget caps | Variable | 1hr |
