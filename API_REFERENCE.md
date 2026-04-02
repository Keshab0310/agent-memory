# API Reference -- agent-memory v0.1.0

Multi-agent memory SDK with SQLite + ChromaDB dual-layer storage, prompt caching,
automatic observation condensation, and orchestrated agent execution.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Core Classes](#core-classes)
  - [Observation](#observation)
  - [Summary](#summary)
  - [MemoryStore](#memorystore)
  - [ContextBuilder](#contextbuilder)
  - [ContextBudget](#contextbudget)
  - [MemoryCondenser](#memorycondenser)
- [Agent Classes](#agent-classes)
  - [AgentConfig](#agentconfig)
  - [AgentResult](#agentresult)
  - [Agent](#agent)
  - [LocalLLMAgent](#localllmagent)
  - [LocalCondenser](#localcondenser)
  - [Agent Registry](#agent-registry)
- [Orchestrator](#orchestrator)
  - [TaskNode](#tasknode)
  - [ExecutionPlan](#executionplan)
  - [Orchestrator](#orchestrator-1)
- [Caching](#caching)
  - [build_cached_messages](#build_cached_messages)
  - [extract_cache_metrics](#extract_cache_metrics)
  - [RateLimiter](#ratelimiter)
  - [retry_with_backoff](#retry_with_backoff)
- [Metrics](#metrics)
  - [AgentMetricsSummary](#agentmetricssummary)
  - [SystemMetricsSummary](#systemmetricssummary)
  - [MetricsTracker](#metricstracker)
- [MCP Server](#mcp-server)
- [Local LLM Integration](#local-llm-integration)

---

## Quick Start

```python
from src.memory.store import MemoryStore, Observation
from src.memory.context_builder import ContextBuilder
from src.agents.base import Agent, AgentConfig

memory = MemoryStore()
agent = Agent(AgentConfig(agent_type="researcher"), memory=memory, project="my-project")
result = agent.execute("Investigate the authentication module")
print(result.response_text)
```

That is enough to get a memory-integrated agent running. The agent stores observations
automatically, injects prior context on subsequent calls, and triggers condensation
when the observation count exceeds the threshold.

---

## Core Classes

### Observation

```
src.memory.store.Observation
```

The core unit of agent memory. Each observation captures a compressed record of
work performed by an agent during a single turn.

```python
@dataclass
class Observation:
    id: Optional[int] = None
    agent_id: str = ""
    project: str = ""
    session_id: str = ""
    obs_type: str = "discovery"   # discovery | bugfix | feature | refactor | change | decision
    title: str = ""
    subtitle: str = ""
    facts: list[str] = field(default_factory=list)
    narrative: str = ""
    concepts: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    discovery_tokens: int = 0     # Tokens consumed to produce this observation
    created_at_epoch: float = 0.0
    condensed: bool = False       # True after rolled into a summary
```

**Fields**

| Field | Type | Description |
|-------|------|-------------|
| `id` | `Optional[int]` | Auto-assigned SQLite row ID after storage. |
| `agent_id` | `str` | Identifier of the agent that produced this observation. |
| `project` | `str` | Project namespace for multi-project isolation. |
| `session_id` | `str` | Session grouping for condensation scope. |
| `obs_type` | `str` | Category. One of: `discovery`, `bugfix`, `feature`, `refactor`, `change`, `decision`. |
| `title` | `str` | Brief title describing the observation. |
| `subtitle` | `str` | One-line supplementary description. |
| `facts` | `list[str]` | Key factual findings, stored as a JSON array in SQLite. |
| `narrative` | `str` | Detailed explanation. Truncated to 200 chars in compact display. |
| `concepts` | `list[str]` | Semantic tags for search and cross-referencing. |
| `files_read` | `list[str]` | File paths read during the work that produced this observation. |
| `files_modified` | `list[str]` | File paths modified during the work. |
| `discovery_tokens` | `int` | Total tokens consumed (input + output + cache creation) to generate this observation. Used for compression ratio calculations. |
| `created_at_epoch` | `float` | Unix timestamp. Auto-set to `time.time()` on storage if zero. |
| `condensed` | `bool` | Set to `True` after this observation has been rolled into a `Summary`. Condensed observations are excluded from working memory but remain searchable. |

**Example**

```python
obs = Observation(
    agent_id="researcher-abc123",
    project="my-project",
    session_id="sess-001",
    obs_type="discovery",
    title="Auth module uses JWT with RS256",
    facts=["Tokens expire after 1 hour", "Refresh tokens stored in HttpOnly cookies"],
    narrative="The authentication module implements...",
    concepts=["authentication", "jwt", "security"],
    files_read=["src/auth/handler.py"],
)
```

---

### Summary

```
src.memory.store.Summary
```

Session summary produced by the condensation pipeline. Replaces multiple observations
in working memory to bound context growth.

```python
@dataclass
class Summary:
    id: Optional[int] = None
    agent_id: str = ""
    project: str = ""
    session_id: str = ""
    request: str = ""
    investigated: str = ""
    learned: str = ""
    completed: str = ""
    next_steps: str = ""
    observation_count: int = 0
    created_at_epoch: float = 0.0
```

**Fields**

| Field | Type | Description |
|-------|------|-------------|
| `request` | `str` | What the agent was trying to accomplish. |
| `investigated` | `str` | What was explored or analyzed. |
| `learned` | `str` | Key findings or insights. |
| `completed` | `str` | What was actually done or changed. |
| `next_steps` | `str` | What remains to be done. |
| `observation_count` | `int` | Number of observations this summary covers. |

---

### MemoryStore

```
src.memory.store.MemoryStore
```

Dual-layer memory system: SQLite for structured metadata queries, optional ChromaDB
for semantic vector search. Falls back to FTS5 (then LIKE) when ChromaDB is unavailable.

Thread-safe: WAL mode, per-thread connections via `threading.local()`, serialized
writes via `threading.Lock()`.

```python
def __init__(
    self,
    sqlite_path: str = "./data/memory.db",
    chroma_path: Optional[str] = "./data/chroma",
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sqlite_path` | `str` | `"./data/memory.db"` | Path to the SQLite database file. Parent directories are created automatically. |
| `chroma_path` | `Optional[str]` | `"./data/chroma"` | Path to ChromaDB persistent storage. Set to `None` to disable vector search entirely. |

**Example**

```python
# Default -- SQLite + ChromaDB
memory = MemoryStore()

# SQLite only (no vector search dependency)
memory = MemoryStore(chroma_path=None)

# Custom paths
memory = MemoryStore(
    sqlite_path="/var/data/project.db",
    chroma_path="/var/data/chroma",
)
```

#### MemoryStore.store_observation

```python
def store_observation(self, obs: Observation) -> int
```

Store an observation in SQLite, sync to FTS5 index, and sync to ChromaDB (if available).
Thread-safe. Sets `created_at_epoch` to `time.time()` if the field is zero.

**Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `obs` | `Observation` | The observation to store. |

**Returns**: `int` -- The SQLite row ID of the stored observation.

**Example**

```python
obs = Observation(
    agent_id="coder-xyz",
    project="my-project",
    session_id="sess-001",
    obs_type="feature",
    title="Implemented rate limiting middleware",
    facts=["Uses token bucket algorithm", "Configurable per-route"],
)
obs_id = memory.store_observation(obs)
```

#### MemoryStore.get_recent_observations

```python
def get_recent_observations(
    self,
    project: str,
    agent_id: Optional[str] = None,
    limit: int = 10,
    include_condensed: bool = False,
) -> list[Observation]
```

Retrieve recent observations ordered by `created_at_epoch DESC`.

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project` | `str` | -- | Required. Project namespace filter. |
| `agent_id` | `Optional[str]` | `None` | Filter to a specific agent. `None` returns all agents. |
| `limit` | `int` | `10` | Maximum number of observations to return. |
| `include_condensed` | `bool` | `False` | When `False`, excludes observations already rolled into summaries. |

**Returns**: `list[Observation]`

**Example**

```python
# Get the 5 most recent uncondensed observations for a specific agent
recent = memory.get_recent_observations(
    project="my-project",
    agent_id="researcher-abc123",
    limit=5,
)

# Get all recent observations across all agents, including condensed
all_recent = memory.get_recent_observations(
    project="my-project",
    limit=20,
    include_condensed=True,
)
```

#### MemoryStore.semantic_search

```python
def semantic_search(
    self,
    query: str,
    project: str,
    limit: int = 5,
    exclude_agent: Optional[str] = None,
) -> list[Observation]
```

Hybrid search: tries ChromaDB vector search first, falls back to FTS5, then falls
back to LIKE-based search. ChromaDB results are hydrated from SQLite to return full
`Observation` objects.

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `str` | -- | Natural language search query. |
| `project` | `str` | -- | Project namespace filter. |
| `limit` | `int` | `5` | Maximum results to return. |
| `exclude_agent` | `Optional[str]` | `None` | Exclude observations from this agent. Useful for cross-agent context where you want observations from _other_ agents only. |

**Returns**: `list[Observation]`

**Example**

```python
# Find observations related to authentication from any agent
results = memory.semantic_search(
    query="JWT token validation and refresh flow",
    project="my-project",
)

# Cross-agent search: find what other agents learned about auth
cross_agent = memory.semantic_search(
    query="authentication",
    project="my-project",
    exclude_agent="coder-xyz",
)
```

#### MemoryStore.store_summary

```python
def store_summary(self, summary: Summary) -> int
```

Store a condensation summary. Sets `created_at_epoch` automatically if zero.

**Returns**: `int` -- The SQLite row ID.

#### MemoryStore.mark_observations_condensed

```python
def mark_observations_condensed(self, observation_ids: list[int]) -> None
```

Mark a list of observations as condensed. Condensed observations are excluded from
`get_recent_observations()` by default but remain available via `semantic_search()`.

#### MemoryStore.log_api_call

```python
def log_api_call(
    self,
    agent_id: str,
    session_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
    latency_ms: int,
    memory_injected: int,
    memory_created: int,
) -> None
```

Record an API call for metrics tracking. The `agent_type` is automatically extracted
from `agent_id` by splitting on the last hyphen (e.g., `"researcher-abc123"` yields
`"researcher"`).

#### MemoryStore.get_token_economics

```python
def get_token_economics(self, project: str) -> dict
```

Calculate the compression ratio across all observations in a project.

**Returns**: `dict` with the following keys:

| Key | Type | Description |
|-----|------|-------------|
| `total_observations` | `int` | Total observation count for the project. |
| `read_tokens` | `int` | Estimated tokens to read all stored observations (~chars/4). |
| `discovery_tokens` | `int` | Total tokens consumed to produce those observations. |
| `savings` | `int` | `discovery_tokens - read_tokens`. |
| `savings_percent` | `int` | Percentage of tokens saved through compression. |

**Example**

```python
economics = memory.get_token_economics("my-project")
print(f"Compression savings: {economics['savings_percent']}%")
# Compression savings: 87%
```

#### MemoryStore.close

```python
def close(self) -> None
```

Close the thread-local SQLite connection. Call this during cleanup.

---

### ContextBuilder

```
src.memory.context_builder.ContextBuilder
```

Builds token-budgeted context strings for agent calls. Assembles three sections
within a configurable token budget:

1. **Current task description** (always included)
2. **Agent's own recent observations** (capped by `own_observations` budget)
3. **Cross-agent observations** via semantic search (capped by `cross_agent` budget)

```python
def __init__(
    self,
    memory: MemoryStore,
    budget: Optional[ContextBudget] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `memory` | `MemoryStore` | -- | Required. The shared memory store to query. |
| `budget` | `Optional[ContextBudget]` | `ContextBudget()` | Token budget allocation. Defaults to 8000 total tokens. |

#### ContextBuilder.build

```python
def build(
    self,
    project: str,
    agent_id: str,
    task_description: str,
    session_id: Optional[str] = None,
) -> str
```

Build a context string within the token budget.

**Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `project` | `str` | Project namespace for memory queries. |
| `agent_id` | `str` | The calling agent's ID. Used to fetch its own observations and exclude itself from cross-agent search. |
| `task_description` | `str` | The current task. Used as the semantic search query for cross-agent context. |
| `session_id` | `Optional[str]` | Session filter (currently unused in queries but reserved). |

**Returns**: `str` -- A formatted context string with markdown-style section headers.

**Example**

```python
builder = ContextBuilder(memory)
context = builder.build(
    project="my-project",
    agent_id="coder-xyz",
    task_description="Add rate limiting to the /orders endpoint",
)
# Returns a string like:
# ## Current Task
# Add rate limiting to the /orders endpoint
#
# ## Your Recent Work
# [feature] Implemented middleware pipeline
#   - Supports pre/post hooks
#
# ## Related Work (Other Agents)
# [researcher-abc] [discovery] Rate limiting best practices
#   - Token bucket preferred over sliding window
```

#### ContextBuilder.get_token_report

```python
def get_token_report(self, context: str) -> dict
```

Report token usage for a built context string.

**Returns**: `dict`

| Key | Type | Description |
|-----|------|-------------|
| `context_tokens` | `int` | Estimated tokens in the context string. |
| `budget_total` | `int` | The configured total budget. |
| `utilization_pct` | `float` | Percentage of budget used. |

---

### ContextBudget

```
src.memory.context_builder.ContextBudget
```

Token budget allocation for context injection. The default 8000 tokens uses
approximately 4% of Claude Sonnet's 200K context window.

```python
@dataclass
class ContextBudget:
    total: int = 8000
    task_description: int = 800
    own_observations: int = 4000
    cross_agent: int = 2400
    summaries: int = 800
```

| Field | Default | Description |
|-------|---------|-------------|
| `total` | `8000` | Maximum total tokens for injected context. |
| `task_description` | `800` | Budget reserved for the task description section. |
| `own_observations` | `4000` | Budget for the agent's own recent observations. |
| `cross_agent` | `2400` | Budget for semantically relevant observations from other agents. |
| `summaries` | `800` | Budget reserved for condensation summaries. |

**Example**

```python
# Use a larger budget for complex multi-agent projects
budget = ContextBudget(total=16000, own_observations=8000, cross_agent=5000)
builder = ContextBuilder(memory, budget=budget)
```

---

### MemoryCondenser

```
src.memory.condenser.MemoryCondenser
```

Periodic summarization pipeline. When an agent's uncondensed observation count
reaches the threshold, the oldest N observations are compressed into a single
`Summary` via an LLM call. Original observations remain in storage for deep search
but are excluded from working memory.

```python
def __init__(
    self,
    memory: MemoryStore,
    threshold: int = 5,
    model: str = "claude-haiku-4-5-20251001",
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `memory` | `MemoryStore` | -- | Required. The shared memory store. |
| `threshold` | `int` | `5` | Number of uncondensed observations that triggers condensation. |
| `model` | `str` | `"claude-haiku-4-5-20251001"` | Model used for the condensation LLM call. Haiku is the default for cost efficiency. |

The Anthropic client is lazily initialized on first use, so you can instantiate
`MemoryCondenser` without setting `ANTHROPIC_API_KEY` if you never trigger condensation.

#### MemoryCondenser.check_and_condense

```python
def check_and_condense(
    self,
    project: str,
    agent_id: str,
    session_id: str,
) -> Optional[Summary]
```

Check if the uncondensed observation count for the given agent exceeds the threshold.
If it does, condense the oldest batch into a summary. Fails gracefully -- if the
LLM call errors, observations remain uncondensed and are retried on the next check.

**Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `project` | `str` | Project namespace. |
| `agent_id` | `str` | The agent whose observations to check. |
| `session_id` | `str` | Session ID for the produced summary. |

**Returns**: `Optional[Summary]` -- The produced summary, or `None` if condensation
was not needed or failed.

**Example**

```python
condenser = MemoryCondenser(memory, threshold=5)

# Typically called automatically by Agent.execute(), but can be called manually:
summary = condenser.check_and_condense(
    project="my-project",
    agent_id="researcher-abc123",
    session_id="sess-001",
)
if summary:
    print(f"Condensed {summary.observation_count} observations")
```

---

## Agent Classes

### AgentConfig

```
src.agents.base.AgentConfig
```

Configuration for an agent instance.

```python
@dataclass
class AgentConfig:
    agent_type: str
    model: str = "claude-sonnet-4-6-20250514"
    max_output_tokens: int = 2000
    system_prompt: str = ""
    tools: list[dict] = field(default_factory=list)
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `agent_type` | `str` | -- | Required. Identifies the agent role (e.g., `"researcher"`, `"coder"`, `"reviewer"`). |
| `model` | `str` | `"claude-sonnet-4-6-20250514"` | Anthropic model ID. |
| `max_output_tokens` | `int` | `2000` | Maximum tokens in the model response. |
| `system_prompt` | `str` | `""` | System prompt defining agent behavior. Use the registry for built-in prompts. |
| `tools` | `list[dict]` | `[]` | Tool definitions in Anthropic tool-use format. The last tool in the list receives a cache breakpoint. |

---

### AgentResult

```
src.agents.base.AgentResult
```

Return value from `Agent.execute()`.

```python
@dataclass
class AgentResult:
    agent_id: str
    agent_type: str
    response_text: str
    observations: list[Observation]
    metrics: dict
    elapsed_ms: int
```

| Field | Type | Description |
|-------|------|-------------|
| `agent_id` | `str` | The unique agent instance ID (e.g., `"researcher-a1b2c3d4"`). |
| `agent_type` | `str` | The agent type from config. |
| `response_text` | `str` | Full text response from the model. |
| `observations` | `list[Observation]` | Observations extracted from the response and stored in memory. |
| `metrics` | `dict` | Cache and token metrics from `extract_cache_metrics()`. |
| `elapsed_ms` | `int` | Wall-clock execution time in milliseconds. |

---

### Agent

```
src.agents.base.Agent
```

A single agent with full memory integration: context injection, prompt caching,
observation extraction, and automatic condensation.

```python
def __init__(
    self,
    config: AgentConfig,
    memory: MemoryStore,
    project: str,
    context_builder: Optional[ContextBuilder] = None,
    condenser: Optional[MemoryCondenser] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `config` | `AgentConfig` | -- | Required. Agent configuration. |
| `memory` | `MemoryStore` | -- | Required. Shared memory store. |
| `project` | `str` | -- | Required. Project namespace. |
| `context_builder` | `Optional[ContextBuilder]` | `None` | Custom context builder. Defaults to `ContextBuilder(memory)`. |
| `condenser` | `Optional[MemoryCondenser]` | `None` | Custom condenser. Defaults to `MemoryCondenser(memory)`. |

The constructor generates a unique `agent_id` (format: `"{agent_type}-{8 hex chars}"`)
and `session_id` (format: `"sess-{12 hex chars}"`).

#### Agent.execute

```python
def execute(
    self,
    task_description: str,
    shared_context: str = "",
) -> AgentResult
```

Execute a single task turn. This method runs the full pipeline:

1. Build episodic context from memory via `ContextBuilder.build()`
2. Construct the API payload with prompt caching via `build_cached_messages()`
3. Call the Anthropic API
4. Extract `<observation>` XML blocks from the response and store them
5. Log API call metrics to the memory store
6. Check if condensation is needed and trigger it if so

If the response does not contain `<observation>` XML blocks, an observation is
auto-generated from the first 100 characters of the response text.

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `task_description` | `str` | -- | The task for the agent to perform. |
| `shared_context` | `str` | `""` | Additional context shared across agents (e.g., dependency results from upstream tasks in a DAG). |

**Returns**: `AgentResult`

**Example**

```python
from src.agents.base import Agent, AgentConfig
from src.agents.registry import get_agent_config
from src.memory.store import MemoryStore

memory = MemoryStore()
config = get_agent_config("researcher")
agent = Agent(config=config, memory=memory, project="my-project")

result = agent.execute("Investigate how the payment module handles refunds")
print(result.response_text)
print(f"Observations stored: {len(result.observations)}")
print(f"Cache hit rate: {result.metrics.get('cache_hit_rate', 0):.1%}")
```

**Observation XML Format**

Agents are prompted to emit structured observations in this format:

```xml
<observation>
  <type>discovery</type>
  <title>Brief title</title>
  <subtitle>One-line summary</subtitle>
  <facts>
    <fact>Key finding 1</fact>
    <fact>Key finding 2</fact>
  </facts>
  <narrative>Detailed explanation</narrative>
  <concepts>
    <concept>relevant_topic</concept>
  </concepts>
  <files_read><file>src/payments/refund.py</file></files_read>
  <files_modified><file>src/payments/handler.py</file></files_modified>
</observation>
```

---

### LocalLLMAgent

```
src.agents.local_llm.LocalLLMAgent
```

Agent powered by a local LLM via any OpenAI-compatible API (Ollama, LM Studio, vLLM).
Inherits from `Agent` but bypasses the Anthropic client and prompt caching layer.
Token savings come entirely from the memory compression pipeline.

```python
def __init__(
    self,
    config: AgentConfig,
    memory: MemoryStore,
    project: str,
    base_url: str = "http://localhost:11434/v1",
    api_key: str = "ollama",
    context_builder: Optional[ContextBuilder] = None,
    condenser: Optional[MemoryCondenser] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `config` | `AgentConfig` | -- | Required. Set `config.model` to your local model name (e.g., `"phi4:latest"`). |
| `memory` | `MemoryStore` | -- | Required. Shared memory store. |
| `project` | `str` | -- | Required. Project namespace. |
| `base_url` | `str` | `"http://localhost:11434/v1"` | OpenAI-compatible API base URL. |
| `api_key` | `str` | `"ollama"` | API key. Ollama does not require a real key. |
| `context_builder` | `Optional[ContextBuilder]` | `None` | Custom context builder. |
| `condenser` | `Optional[MemoryCondenser]` | `None` | Custom condenser. |

#### LocalLLMAgent.execute

Same signature and return type as `Agent.execute()`. The metrics dict includes
two additional fields:

| Key | Type | Description |
|-----|------|-------------|
| `provider` | `str` | Always `"local"`. |
| `base_url` | `str` | The base URL of the local LLM server. |

Cache-related metrics (`cache_creation_tokens`, `cache_read_tokens`, `cache_hit_rate`,
`savings_pct`) are always zero for local LLMs.

**Example**

```python
from src.agents.local_llm import LocalLLMAgent
from src.agents.base import AgentConfig
from src.memory.store import MemoryStore

memory = MemoryStore()
config = AgentConfig(agent_type="researcher", model="phi4:latest")

agent = LocalLLMAgent(
    config=config,
    memory=memory,
    project="my-project",
    base_url="http://localhost:11434/v1",
)

result = agent.execute("Summarize the authentication flow")
```

---

### LocalCondenser

```
src.agents.local_llm.LocalCondenser
```

Condensation using a local LLM instead of the Anthropic API. Use this when you want
the entire pipeline to run without cloud API calls.

```python
def __init__(
    self,
    memory: MemoryStore,
    threshold: int = 5,
    model: str = "phi4:latest",
    base_url: str = "http://localhost:11434/v1",
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `memory` | `MemoryStore` | -- | Required. |
| `threshold` | `int` | `5` | Observations before condensation triggers. |
| `model` | `str` | `"phi4:latest"` | Local model name. |
| `base_url` | `str` | `"http://localhost:11434/v1"` | OpenAI-compatible API base URL. |

**Example**

```python
from src.agents.local_llm import LocalLLMAgent, LocalCondenser
from src.agents.base import AgentConfig
from src.memory.store import MemoryStore

memory = MemoryStore()
condenser = LocalCondenser(memory, model="phi4:latest")

config = AgentConfig(agent_type="researcher", model="phi4:latest")
agent = LocalLLMAgent(
    config=config,
    memory=memory,
    project="my-project",
    condenser=condenser,
)
```

---

### Agent Registry

```
src.agents.registry
```

Provides pre-configured agent types with tuned system prompts and model defaults.

#### get_agent_config

```python
def get_agent_config(agent_type: str, **overrides) -> AgentConfig
```

Get a pre-configured `AgentConfig` for a known agent type. Unknown types fall back
to Sonnet with 2000 max output tokens and a generic system prompt.

**Built-in Agent Types**

| Type | Model | Max Tokens | Role |
|------|-------|------------|------|
| `"researcher"` | `claude-sonnet-4-6-20250514` | `2000` | Investigate questions, gather information, report findings. |
| `"coder"` | `claude-sonnet-4-6-20250514` | `4096` | Write clean, correct code. |
| `"reviewer"` | `claude-sonnet-4-6-20250514` | `1500` | Analyze code for correctness, security, maintainability. |
| `"summarizer"` | `claude-haiku-4-5-20251001` | `500` | Produce concise synthesis from other agent observations. |
| `"planner"` | `claude-sonnet-4-6-20250514` | `1500` | Break down complex tasks into actionable steps. |

**Overrides**: Pass any `AgentConfig` field as a keyword argument to override the default.

**Example**

```python
from src.agents.registry import get_agent_config

# Use defaults
config = get_agent_config("researcher")

# Override model and token limit
config = get_agent_config("coder", model="claude-sonnet-4-6-20250514", max_output_tokens=8192)

# Custom agent type (gets generic defaults)
config = get_agent_config("debugger", system_prompt="You are a debugging specialist...")
```

---

## Orchestrator

### TaskNode

```
src.orchestrator.router.TaskNode
```

A single task in the execution DAG.

```python
@dataclass
class TaskNode:
    task_id: str
    agent_type: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    result: Optional[AgentResult] = None
    status: str = "pending"   # pending | running | completed | failed
```

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `str` | Unique task identifier (e.g., `"task_0"`, `"task_1"`). |
| `agent_type` | `str` | The agent type to handle this task. |
| `description` | `str` | Task description passed to the agent. |
| `depends_on` | `list[str]` | List of `task_id` values that must complete before this task runs. Empty list means no dependencies (runs immediately). |
| `result` | `Optional[AgentResult]` | Populated after execution. |
| `status` | `str` | Lifecycle state: `pending`, `running`, `completed`, or `failed`. |

---

### ExecutionPlan

```
src.orchestrator.router.ExecutionPlan
```

A DAG of tasks to execute.

```python
@dataclass
class ExecutionPlan:
    tasks: list[TaskNode]
    shared_context: str = ""
```

#### ExecutionPlan.get_ready_tasks

```python
def get_ready_tasks(self) -> list[TaskNode]
```

Returns tasks whose status is `"pending"` and whose dependencies have all reached
`"completed"` status.

**Example**

```python
plan = ExecutionPlan(tasks=[
    TaskNode(task_id="task_0", agent_type="researcher", description="Research auth patterns"),
    TaskNode(task_id="task_1", agent_type="coder", description="Implement auth", depends_on=["task_0"]),
    TaskNode(task_id="task_2", agent_type="reviewer", description="Review auth code", depends_on=["task_1"]),
])

ready = plan.get_ready_tasks()
# Returns [TaskNode(task_id="task_0", ...)] -- only the task with no dependencies
```

---

### Orchestrator

```
src.orchestrator.router.Orchestrator
```

Routes user requests to agent teams, manages the execution DAG, and merges results.
Uses Haiku for cheap, fast routing decisions and Sonnet/Opus for actual agent work.

```python
def __init__(
    self,
    memory: MemoryStore,
    project: str,
    router_model: str = "claude-haiku-4-5-20251001",
    max_concurrent: int = 5,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `memory` | `MemoryStore` | -- | Required. Shared memory store. |
| `project` | `str` | -- | Required. Project namespace. |
| `router_model` | `str` | `"claude-haiku-4-5-20251001"` | Model for routing/planning decisions. |
| `max_concurrent` | `int` | `5` | Maximum number of agents running concurrently. |

#### Orchestrator.run

```python
async def run(self, user_request: str) -> str
```

Full orchestration pipeline: plan, execute DAG, merge results.

**Returns**: `str` -- The final merged response text.

**Example**

```python
import asyncio
from src.orchestrator.router import Orchestrator
from src.memory.store import MemoryStore

memory = MemoryStore()
orchestrator = Orchestrator(memory=memory, project="my-project")

response = asyncio.run(orchestrator.run(
    "Add rate limiting to the orders API with tests and a code review"
))
print(response)
```

#### Orchestrator.plan

```python
async def plan(self, user_request: str) -> ExecutionPlan
```

Decompose a user request into an execution plan using the router model. The router
checks memory for similar past tasks to inform the plan. If the router response
cannot be parsed as JSON, falls back to a single researcher task.

**Returns**: `ExecutionPlan`

#### Orchestrator.execute_plan

```python
async def execute_plan(self, plan: ExecutionPlan) -> None
```

Execute the DAG with concurrency control. Tasks with no dependencies start
immediately (with staggered 0.5s delays to avoid rate limit bursts). Tasks with
failed dependencies are automatically marked as failed. Each task runs in
a thread executor to avoid blocking the event loop.

#### Orchestrator.merge_results

```python
def merge_results(self, plan: ExecutionPlan, original_request: str) -> str
```

Synthesize all completed agent results into a final response. If only one agent
completed, returns its response directly. For multi-agent results, a summarizer
agent merges the outputs.

**Returns**: `str`

---

## Caching

### build_cached_messages

```
src.cache.prompt_cache.build_cached_messages
```

```python
def build_cached_messages(
    system_prompt: str,
    shared_context: str,
    episodic_context: str,
    user_message: str,
    tools: Optional[list[dict]] = None,
) -> dict
```

Build an Anthropic `messages.create()` payload with three-tier prompt caching.

**Cache Layout**

```
+----------------------------+
| System prompt              | <-- cache breakpoint (Tier 0, 5-min TTL)
+----------------------------+
| Shared project context     | <-- cache breakpoint (Tier 1, 5-min TTL)
+----------------------------+
| Episodic context           | <-- NOT cached (changes per turn)
+----------------------------+
| User message               | <-- NOT cached
+----------------------------+
```

The system prompt and shared context are identical across multiple calls for the
same agent type and project. Cache hits reduce input token costs by 90%.

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `system_prompt` | `str` | -- | The agent's system prompt. Gets `cache_control: ephemeral`. |
| `shared_context` | `str` | -- | Project-level context shared across turns. Injected as a user/assistant prefill pair with `cache_control: ephemeral`. |
| `episodic_context` | `str` | -- | Per-turn memory context from `ContextBuilder.build()`. Not cached. |
| `user_message` | `str` | -- | The actual user/task message. |
| `tools` | `Optional[list[dict]]` | `None` | Anthropic tool definitions. The last tool gets a cache breakpoint. |

**Returns**: `dict` -- Kwargs dict ready to unpack into `client.messages.create(**payload)`.

**Example**

```python
from src.cache.prompt_cache import build_cached_messages

payload = build_cached_messages(
    system_prompt="You are a research agent...",
    shared_context="Project uses Python 3.12, FastAPI, PostgreSQL.",
    episodic_context="## Your Recent Work\n[discovery] Found auth uses JWT",
    user_message="Investigate the database migration strategy",
)

response = client.messages.create(
    model="claude-sonnet-4-6-20250514",
    max_tokens=2000,
    **payload,
)
```

---

### extract_cache_metrics

```
src.cache.prompt_cache.extract_cache_metrics
```

```python
def extract_cache_metrics(response) -> dict
```

Extract cache performance metrics from an Anthropic API response.

**Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `response` | `anthropic.types.Message` | The API response object. |

**Returns**: `dict`

| Key | Type | Description |
|-----|------|-------------|
| `input_tokens` | `int` | Non-cached input tokens. |
| `output_tokens` | `int` | Output tokens. |
| `cache_creation_tokens` | `int` | Tokens written to cache (1.25x cost). |
| `cache_read_tokens` | `int` | Tokens read from cache (0.1x cost). |
| `cache_hit_rate` | `float` | Ratio of cache reads to total input (0.0 to 1.0). |
| `effective_cost_units` | `float` | Weighted cost: normal=1x, write=1.25x, read=0.1x. |
| `naive_cost_units` | `int` | What the cost would be without caching. |
| `savings_pct` | `float` | Percentage saved vs. naive cost. |

---

### RateLimiter

```
src.cache.rate_limiter.RateLimiter
```

Thread-safe token bucket rate limiter enforcing both RPM (requests per minute)
and TPM (tokens per minute) limits. Shared across all agents via the Orchestrator
to prevent 429 errors.

```python
def __init__(
    self,
    requests_per_minute: int = 50,
    tokens_per_minute: int = 80_000,
    max_retries: int = 3,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `requests_per_minute` | `int` | `50` | Maximum requests allowed per 60-second window. |
| `tokens_per_minute` | `int` | `80_000` | Maximum tokens allowed per 60-second window. |
| `max_retries` | `int` | `3` | Stored for external retry logic. |

#### RateLimiter.acquire_sync

```python
def acquire_sync(self, estimated_tokens: int = 4000) -> None
```

Block the calling thread until a request slot is available. Use this from
synchronous code (e.g., `Agent.execute()`).

#### RateLimiter.acquire

```python
async def acquire(self, estimated_tokens: int = 4000) -> None
```

Async version. Yields control via `asyncio.sleep()` while waiting. Use this
from async code (e.g., `Orchestrator.execute_plan()`).

#### RateLimiter.record_actual_usage

```python
def record_actual_usage(self, actual_tokens: int) -> None
```

Update the last entry with actual token count after receiving the API response.
Call this to refine rate limiting accuracy.

#### RateLimiter.get_status

```python
def get_status(self) -> dict
```

**Returns**: `dict`

| Key | Type | Description |
|-----|------|-------------|
| `current_rpm` | `int` | Requests in the current 60-second window. |
| `rpm_limit` | `int` | Configured RPM limit. |
| `current_tpm` | `int` | Tokens in the current 60-second window. |
| `tpm_limit` | `int` | Configured TPM limit. |
| `rpm_headroom` | `int` | Remaining requests before hitting the limit. |
| `tpm_headroom` | `int` | Remaining tokens before hitting the limit. |

**Example**

```python
from src.cache.rate_limiter import RateLimiter

limiter = RateLimiter(requests_per_minute=50, tokens_per_minute=80_000)

# Before each API call
limiter.acquire_sync(estimated_tokens=4000)
response = client.messages.create(...)

# After receiving the response
actual = response.usage.input_tokens + response.usage.output_tokens
limiter.record_actual_usage(actual)

# Check current state
status = limiter.get_status()
print(f"RPM headroom: {status['rpm_headroom']}")
```

---

### retry_with_backoff

```
src.cache.rate_limiter.retry_with_backoff
```

```python
def retry_with_backoff(
    func,
    max_retries: int = 3,
    base_delay: float = 1.0,
)
```

Retry a callable with exponential backoff and jitter. Only retries on transient
errors: `429`, `rate_limit`, `overloaded`, `529`, `timeout`, `connection`.

**Parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `func` | `callable` | -- | A zero-argument callable to execute. |
| `max_retries` | `int` | `3` | Maximum retry attempts after the initial call. |
| `base_delay` | `float` | `1.0` | Base delay in seconds. Multiplied by `2^attempt` plus random jitter. |

**Returns**: The return value of `func()`.

**Raises**: The last exception if all retries fail, or any non-retryable exception immediately.

**Example**

```python
from src.cache.rate_limiter import retry_with_backoff

result = retry_with_backoff(
    lambda: client.messages.create(model="claude-sonnet-4-6-20250514", ...),
    max_retries=3,
)
```

---

## Metrics

### AgentMetricsSummary

```
src.metrics.tracker.AgentMetricsSummary
```

Per-agent-type metrics aggregation.

```python
@dataclass
class AgentMetricsSummary:
    agent_type: str
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_cache_creation: int
    total_cache_read: int
    avg_latency_ms: float
    cache_hit_rate: float
    effective_cost_ratio: float   # vs naive (no caching), <1.0 means savings
```

---

### SystemMetricsSummary

```
src.metrics.tracker.SystemMetricsSummary
```

System-wide metrics aggregation.

```python
@dataclass
class SystemMetricsSummary:
    total_api_calls: int
    total_tokens: int
    total_cached_tokens: int
    overall_cache_hit_rate: float
    overall_savings_pct: float
    total_observations: int
    compression_ratio: float      # discovery_tokens / read_tokens
    avg_latency_ms: float
```

---

### MetricsTracker

```
src.metrics.tracker.MetricsTracker
```

Aggregates and reports metrics from the MemoryStore's `api_calls` table.

```python
def __init__(self, memory: MemoryStore)
```

#### MetricsTracker.get_agent_summary

```python
def get_agent_summary(
    self,
    agent_type: Optional[str] = None,
) -> list[AgentMetricsSummary]
```

Get metrics grouped by agent type. Optionally filter to a single agent type.

**Returns**: `list[AgentMetricsSummary]`

#### MetricsTracker.get_system_summary

```python
def get_system_summary(self, project: str) -> SystemMetricsSummary
```

Get overall system metrics including token economics from observation compression.

**Returns**: `SystemMetricsSummary`

#### MetricsTracker.print_dashboard

```python
def print_dashboard(self, project: str) -> None
```

Print a human-readable metrics dashboard to stdout.

**Example**

```python
from src.metrics.tracker import MetricsTracker
from src.memory.store import MemoryStore

memory = MemoryStore()
tracker = MetricsTracker(memory)

# After running some agents...
tracker.print_dashboard("my-project")
```

**Sample Output**

```
============================================================
MULTI-AGENT SYSTEM METRICS
============================================================
Total API calls:      12
Total tokens:         45,230
Cached tokens:        28,100
Cache hit rate:       62.1%
Compression ratio:    8.3:1
Token savings:        87%
Observations stored:  23
Avg latency:          1450ms

Per-Agent Breakdown:
------------------------------------------------------------
Type            Calls   Cache%  Cost Ratio  Latency
researcher      5       58.2%   0.65x       1200ms
coder           4       71.3%   0.52x       1800ms
reviewer        3       55.0%   0.70x       1100ms
============================================================
```

---

## MCP Server

The SDK can run as a Claude Code plugin via the Model Context Protocol (MCP).
Configuration is defined in `plugin/plugin.json`.

### Plugin Configuration

```json
{
  "name": "agent-memory",
  "version": "0.1.0",
  "description": "Save 60-90% on LLM token costs with intelligent memory compression",
  "mcp_servers": {
    "agent-memory": {
      "command": "python",
      "args": ["plugin/mcp_server.py"],
      "transport": "stdio"
    }
  },
  "hooks": {
    "PostToolUse": {
      "command": "python plugin/hooks/post_tool_use.py",
      "timeout": 5000
    },
    "SessionStart": {
      "command": "python plugin/hooks/session_start.py",
      "timeout": 5000
    }
  }
}
```

### Exposed MCP Tools

The MCP server (when implemented at `plugin/mcp_server.py`) exposes the following
tools for use within Claude Code sessions:

| Tool | Description |
|------|-------------|
| `get_observations` | Retrieve recent observations from the memory store. |
| `search` | Semantic search across observations. |
| `smart_search` | Natural language search with relevance ranking. |
| `smart_outline` | Get a structural outline of stored knowledge. |
| `smart_unfold` | Expand a specific observation or concept in detail. |
| `timeline` | View the chronological history of observations. |

### Hook Behavior

| Hook | Trigger | Purpose |
|------|---------|---------|
| `PostToolUse` | After every tool call in a Claude Code session | Automatically captures observations from tool outputs (file reads, code edits, command results). |
| `SessionStart` | When a new Claude Code session begins | Loads relevant memory context for the current project. |

### Setup

To use as a Claude Code plugin, place the `plugin/` directory in your project and
register it in your Claude Code settings:

```bash
# Install with MCP dependencies
pip install agent-memory[mcp]
```

---

## Local LLM Integration

The SDK supports any local LLM that exposes an OpenAI-compatible
`/v1/chat/completions` endpoint. Tested with Ollama and LM Studio.

### Ollama

```bash
# Install and start Ollama
ollama serve

# Pull a model
ollama pull phi4:latest
```

```python
from src.agents.local_llm import LocalLLMAgent, LocalCondenser
from src.agents.base import AgentConfig
from src.memory.store import MemoryStore

memory = MemoryStore()

# Use LocalCondenser so condensation also runs locally
condenser = LocalCondenser(memory, model="phi4:latest")

config = AgentConfig(
    agent_type="researcher",
    model="phi4:latest",
    max_output_tokens=2000,
    system_prompt="You are a research agent. Structure findings as <observation> blocks.",
)

agent = LocalLLMAgent(
    config=config,
    memory=memory,
    project="my-project",
    base_url="http://localhost:11434/v1",  # Ollama default
    condenser=condenser,
)

result = agent.execute("Analyze the error handling patterns in this codebase")
```

### LM Studio

```python
agent = LocalLLMAgent(
    config=AgentConfig(agent_type="coder", model="local-model"),
    memory=memory,
    project="my-project",
    base_url="http://localhost:1234/v1",  # LM Studio default
    api_key="lm-studio",
)
```

### Limitations

- Local LLMs do not support Anthropic prompt caching. The `cache_creation_tokens`,
  `cache_read_tokens`, `cache_hit_rate`, and `savings_pct` fields are always zero.
- Token savings come entirely from the memory compression pipeline (observations
  replace raw conversation context).
- Condensation quality depends on the local model's ability to follow the XML
  summary format. Models with strong instruction-following (Phi-4, Llama 3, Mistral)
  work best.
- The `MemoryCondenser` default model is `claude-haiku-4-5-20251001` (Anthropic).
  If you want fully local operation, pass a `LocalCondenser` instance explicitly.

---

## Utility Functions

### estimate_tokens

```
src.memory.context_builder.estimate_tokens
```

```python
def estimate_tokens(text: str) -> int
```

Rough token estimate at 4 characters per token. Used for budget calculations.

### format_observation_compact

```
src.memory.context_builder.format_observation_compact
```

```python
def format_observation_compact(obs: Observation) -> str
```

Format an observation into a minimal text representation. Facts are capped at 3.
Narratives longer than 200 characters are truncated.

### format_summary_compact

```
src.memory.context_builder.format_summary_compact
```

```python
def format_summary_compact(summary: Summary) -> str
```

Format a summary into a single-line pipe-delimited string
(e.g., `"Task: ... | Done: ... | Learned: ... | Next: ..."`).
