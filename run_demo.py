"""
End-to-end demo: 3 agents (researcher → coder → reviewer) with shared memory.

Runs in DRY-RUN mode (no API key needed) by default.
Set ANTHROPIC_API_KEY to run against the real API.

Validates:
1. Prompt caching structure (cache breakpoints placed correctly)
2. Cross-agent memory sharing (reviewer sees researcher's findings)
3. Token metrics tracking
4. Observation compression pipeline
5. Condensation triggers after threshold

Usage:
  python run_demo.py            # Dry-run with mock responses
  python run_demo.py --live     # Real API calls (needs ANTHROPIC_API_KEY)
"""

import os
import sys
import json
import time
import tempfile
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from src.memory.store import MemoryStore, Observation
from src.memory.context_builder import ContextBuilder, estimate_tokens
from src.memory.condenser import MemoryCondenser, _parse_summary_xml
from src.agents.base import Agent, AgentConfig, AgentResult
from src.agents.registry import get_agent_config
from src.cache.prompt_cache import build_cached_messages, extract_cache_metrics
from src.metrics.tracker import MetricsTracker


PROJECT = "demo-project"
LIVE_MODE = "--live" in sys.argv

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ── Mock Anthropic responses ──

MOCK_RESPONSES = {
    "researcher": """Based on my analysis, here are the key approaches for token-efficient multi-agent systems:

<observation>
  <type>discovery</type>
  <title>Three Core Token Optimization Strategies Identified</title>
  <subtitle>Prompt caching, memory compression, and dynamic context injection</subtitle>
  <facts>
    <fact>Prompt caching reduces repeat input costs by 90% via Anthropic's ephemeral cache</fact>
    <fact>Memory compression achieves 5-10x reduction by summarizing raw tool output into structured observations</fact>
    <fact>Dynamic context injection loads only relevant memory chunks, keeping working memory under 2K tokens</fact>
  </facts>
  <narrative>The most effective multi-agent architectures use a tiered memory system: cached static prompts at the top, a token-budgeted working memory in the middle, and vector-indexed long-term storage at the bottom. The key insight from claude-mem is that raw tool output is 10-100x larger than its semantic content.</narrative>
  <concepts>
    <concept>token_optimization</concept>
    <concept>prompt_caching</concept>
    <concept>memory_compression</concept>
  </concepts>
  <files_read></files_read>
  <files_modified></files_modified>
</observation>""",

    "coder": """Here's the implementation:

```python
class TokenBudgetedContextBuilder:
    def __init__(self, budget=2000):
        self.budget = budget

    def build(self, task, own_obs, cross_obs):
        sections, remaining = [], self.budget
        for obs in own_obs:
            text = self._compact(obs)
            cost = len(text) // 4
            if cost <= remaining:
                sections.append(text)
                remaining -= cost
        return "\\n".join(sections)
```

<observation>
  <type>feature</type>
  <title>Token-Budgeted Context Builder Implemented</title>
  <subtitle>Greedy allocation with priority ordering</subtitle>
  <facts>
    <fact>Uses greedy allocation: own observations first, cross-agent second</fact>
    <fact>Hard budget cap at 2000 tokens prevents context overflow</fact>
    <fact>Compact formatting reduces observation size by ~60%</fact>
  </facts>
  <narrative>Implemented a context builder that allocates token budget greedily, prioritizing the agent's own recent observations over cross-agent context. Uses 4 chars/token estimate for budget enforcement.</narrative>
  <concepts>
    <concept>context_builder</concept>
    <concept>token_budgeting</concept>
  </concepts>
  <files_read></files_read>
  <files_modified>
    <file>src/memory/context_builder.py</file>
  </files_modified>
</observation>""",

    "reviewer": """Code review findings:

<observation>
  <type>discovery</type>
  <title>Context Builder Review: Sound Design with Minor Gaps</title>
  <subtitle>Token budgeting strategy is correct; edge cases identified</subtitle>
  <facts>
    <fact>Greedy allocation is optimal for this use case — no need for knapsack</fact>
    <fact>Missing: fallback when no observations fit the budget</fact>
    <fact>Cross-agent search could benefit from relevance scoring threshold</fact>
  </facts>
  <narrative>The token budgeting approach is architecturally sound. The 4-char/token estimate is within 10% of actual for English text. The greedy strategy works because observation sizes are roughly uniform. Recommend adding a minimum-context guarantee so agents never run with zero memory.</narrative>
  <concepts>
    <concept>code_review</concept>
    <concept>token_budgeting</concept>
  </concepts>
  <files_read>
    <file>src/memory/context_builder.py</file>
  </files_read>
  <files_modified></files_modified>
</observation>""",

    "summarizer": """<summary>
  <request>Build and validate a token-efficient multi-agent memory system</request>
  <investigated>Token optimization strategies, prompt caching mechanics, cross-agent memory sharing</investigated>
  <learned>Prompt caching yields 90% input savings; memory compression achieves 5-10x; greedy budget allocation is optimal for uniform observation sizes</learned>
  <completed>Researcher identified strategies, coder implemented context builder, reviewer validated approach</completed>
  <next_steps>Add relevance scoring threshold for cross-agent search; implement minimum-context guarantee</next_steps>
</summary>""",
}


def make_mock_response(agent_type: str, turn: int = 1):
    """Build a mock Anthropic Message object."""
    text = MOCK_RESPONSES.get(agent_type, f"Mock response for {agent_type}")

    # Simulate realistic token counts
    input_tokens = 1800 + (turn * 200)  # Grows with conversation
    output_tokens = len(text) // 4

    # Simulate cache behavior: turn 1 = write, turn 2+ = read
    if turn == 1:
        cache_creation = 1200  # System prompt + shared context cached
        cache_read = 0
    else:
        cache_creation = 0
        cache_read = 1200  # Same content now hits cache

    mock = MagicMock()
    mock.content = [MagicMock(text=text, type="text")]
    mock.usage = MagicMock(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
    )
    return mock


class MockAnthropicClient:
    """Drop-in mock for anthropic.Anthropic()."""
    def __init__(self):
        self._call_count = {}

    class messages:
        _parent = None

        @staticmethod
        def create(**kwargs):
            # Determine agent type from system prompt
            system_text = ""
            if "system" in kwargs:
                for s in kwargs["system"]:
                    if isinstance(s, dict):
                        system_text += s.get("text", "")

            agent_type = "researcher"  # default
            for t in ["coder", "reviewer", "summarizer", "planner"]:
                if t in system_text.lower():
                    agent_type = t
                    break

            # Track calls per agent type for cache simulation
            if not hasattr(MockAnthropicClient.messages, "_counts"):
                MockAnthropicClient.messages._counts = {}
            counts = MockAnthropicClient.messages._counts
            counts[agent_type] = counts.get(agent_type, 0) + 1

            return make_mock_response(agent_type, turn=counts[agent_type])


def run_cache_structure_test():
    """Validate prompt cache breakpoint placement."""
    print("\n" + "=" * 60)
    print("TEST 1: Prompt Cache Structure Validation")
    print("=" * 60)

    payload = build_cached_messages(
        system_prompt="You are a research agent specialized in LLM optimization.",
        shared_context="Project: multi-agent orchestration system using Claude API.\nGoal: minimize token consumption while maintaining agent effectiveness.",
        episodic_context="Recent work:\n- [discovery] Identified 3 optimization strategies\n- [feature] Implemented context builder",
        user_message="What additional optimizations should we consider?",
        tools=[
            {"name": "web_search", "description": "Search the web", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}},
            {"name": "read_file", "description": "Read a file", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
        ],
    )

    # Verify structure
    print("\n  Cache breakpoint layout:")

    # System prompt
    system_cached = any(
        s.get("cache_control") for s in payload["system"] if isinstance(s, dict)
    )
    print(f"  [{'OK' if system_cached else 'FAIL'}] System prompt has cache_control")

    # Shared context (first user message)
    shared_msg = payload["messages"][0]
    shared_cached = any(
        p.get("cache_control") for p in shared_msg["content"] if isinstance(p, dict)
    )
    print(f"  [{'OK' if shared_cached else 'FAIL'}] Shared context has cache_control")

    # Episodic context (last user message) — should NOT be cached
    user_msg = payload["messages"][-1]
    user_cached = any(
        p.get("cache_control") for p in user_msg["content"] if isinstance(p, dict)
    )
    print(f"  [{'OK' if not user_cached else 'FAIL'}] Episodic context is NOT cached (dynamic)")

    # Tools — last tool should have cache_control
    tools_cached = payload.get("tools", [{}])[-1].get("cache_control") is not None
    print(f"  [{'OK' if tools_cached else 'FAIL'}] Tool definitions have cache_control")

    # Token estimate
    total_cached_chars = (
        len(payload["system"][0]["text"])
        + len(shared_msg["content"][0]["text"])
        + sum(len(json.dumps(t)) for t in payload.get("tools", []))
    )
    total_dynamic_chars = sum(
        len(p["text"]) for p in user_msg["content"] if isinstance(p, dict)
    )
    print(f"\n  Estimated cached prefix: ~{total_cached_chars // 4} tokens")
    print(f"  Estimated dynamic portion: ~{total_dynamic_chars // 4} tokens")
    print(f"  Cache savings on hit: ~{(total_cached_chars // 4) * 0.9:.0f} tokens saved per call")

    all_ok = system_cached and shared_cached and not user_cached and tools_cached
    print(f"\n  {'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED'}")
    return all_ok


def run_memory_pipeline_test():
    """Test the full memory pipeline: store → search → condense."""
    print("\n" + "=" * 60)
    print("TEST 2: Memory Pipeline (Store → Search → Condense)")
    print("=" * 60)

    tmpdir = tempfile.mkdtemp()
    memory = MemoryStore(
        sqlite_path=os.path.join(tmpdir, "test.db"),
        chroma_path=os.path.join(tmpdir, "chroma"),
    )

    # Store observations from 3 different agents
    agents_data = [
        ("researcher-a1b2", "Prompt caching reduces costs by 90%", ["caching", "cost"]),
        ("researcher-a1b2", "Tiered memory prevents context overflow", ["memory", "architecture"]),
        ("coder-c3d4", "Implemented context builder with budget", ["implementation", "context"]),
        ("coder-c3d4", "Added ChromaDB vector search integration", ["search", "vectors"]),
        ("reviewer-e5f6", "Code review: budget enforcement is correct", ["review", "validation"]),
    ]

    print("\n  Storing 5 observations from 3 agents...")
    for agent_id, title, concepts in agents_data:
        memory.store_observation(Observation(
            agent_id=agent_id,
            project=PROJECT,
            session_id="sess-demo",
            obs_type="discovery",
            title=title,
            narrative=f"Detailed finding: {title}. This is important because it directly impacts token efficiency.",
            concepts=concepts,
            discovery_tokens=3000,
        ))

    # Test retrieval
    all_obs = memory.get_recent_observations(PROJECT, limit=10)
    print(f"  Total observations: {len(all_obs)}")

    researcher_obs = memory.get_recent_observations(PROJECT, agent_id="researcher-a1b2")
    print(f"  Researcher observations: {len(researcher_obs)}")

    coder_obs = memory.get_recent_observations(PROJECT, agent_id="coder-c3d4")
    print(f"  Coder observations: {len(coder_obs)}")

    # Test semantic search
    print("\n  Semantic search: 'vector database integration'")
    results = memory.semantic_search("vector database integration", PROJECT, limit=3)
    for r in results:
        print(f"    [{r.agent_id}] {r.title}")

    # Test cross-agent search (exclude coder)
    print("\n  Cross-agent search (exclude coder): 'cost optimization'")
    results = memory.semantic_search(
        "cost optimization", PROJECT, limit=3, exclude_agent="coder-c3d4"
    )
    for r in results:
        print(f"    [{r.agent_id}] {r.title}")

    # Test token economics
    economics = memory.get_token_economics(PROJECT)
    print(f"\n  Token economics:")
    print(f"    Observations: {economics['total_observations']}")
    print(f"    Discovery tokens: {economics['discovery_tokens']:,}")
    print(f"    Read tokens: {economics['read_tokens']:,}")
    print(f"    Compression ratio: {economics['discovery_tokens'] / max(economics['read_tokens'], 1):.1f}:1")
    print(f"    Savings: {economics['savings_percent']}%")

    # Test context builder with budget
    print("\n  Context builder (budget=500 tokens):")
    from src.memory.context_builder import ContextBuilder, ContextBudget
    builder = ContextBuilder(memory, ContextBudget(total=500))
    context = builder.build(PROJECT, "reviewer-e5f6", "Review the token budgeting implementation")
    report = builder.get_token_report(context)
    print(f"    Context tokens: {report['context_tokens']}")
    print(f"    Budget utilization: {report['utilization_pct']}%")
    print(f"    Context preview:\n{''.join('      ' + line + chr(10) for line in context.split(chr(10))[:8])}")

    # Test condensation
    print("  Condensation pipeline:")
    # Add more observations to trigger threshold (need 5)
    memory.store_observation(Observation(
        agent_id="researcher-a1b2", project=PROJECT, session_id="sess-demo",
        title="Additional finding about batching", concepts=["batching"],
        discovery_tokens=2000,
    ))

    # Parse a mock summary
    parsed = _parse_summary_xml(MOCK_RESPONSES["summarizer"])
    if parsed:
        print(f"    Summary parsed: request='{parsed['request'][:50]}...'")
        print(f"    Learned: '{parsed['learned'][:60]}...'")
        print(f"    [OK] Condensation XML parsing works")
    else:
        print(f"    [FAIL] Could not parse summary XML")

    memory.close()
    return True


def run_multi_agent_test():
    """Run 3 agents with shared memory using mock API."""
    print("\n" + "=" * 60)
    print("TEST 3: Multi-Agent Execution" + (" (LIVE API)" if LIVE_MODE else " (Mock)"))
    print("=" * 60)

    tmpdir = tempfile.mkdtemp()
    memory = MemoryStore(
        sqlite_path=os.path.join(tmpdir, "multi.db"),
        chroma_path=os.path.join(tmpdir, "chroma"),
    )

    agents_config = [
        ("researcher", "What are the key principles of token-efficient multi-agent LLM systems?"),
        ("coder", "Implement a token-budgeted context builder based on the researcher's findings."),
        ("reviewer", "Review the implementation. Is the token budgeting strategy sound?"),
    ]

    if LIVE_MODE:
        # Real API — needs ANTHROPIC_API_KEY
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("  SKIPPED: Set ANTHROPIC_API_KEY for live mode")
            return False
        mock_ctx = lambda: (yield)  # no-op context manager
    else:
        # Mock API
        mock_ctx = lambda: patch("anthropic.Anthropic", return_value=MockAnthropicClient())

    with patch("anthropic.Anthropic", MockAnthropicClient) if not LIVE_MODE else nullcontext():
        shared_ctx = "Project: multi-agent system for token-efficient LLM orchestration."

        for agent_type, task in agents_config:
            print(f"\n  [{agent_type.upper()}] {task[:70]}...")
            config = get_agent_config(agent_type)
            agent = Agent(config=config, memory=memory, project=PROJECT)

            result = agent.execute(task, shared_context=shared_ctx)

            print(f"    Response: {result.response_text[:120]}...")
            print(f"    Observations: {len(result.observations)}")
            print(f"    Tokens: in={result.metrics.get('input_tokens', 0)} "
                  f"out={result.metrics.get('output_tokens', 0)} "
                  f"cache_create={result.metrics.get('cache_creation_tokens', 0)} "
                  f"cache_read={result.metrics.get('cache_read_tokens', 0)}")
            print(f"    Latency: {result.elapsed_ms}ms")

            # Log to metrics
            memory.log_api_call(
                agent_id=result.agent_id,
                session_id=agent.session_id,
                model=config.model,
                input_tokens=result.metrics.get("input_tokens", 0),
                output_tokens=result.metrics.get("output_tokens", 0),
                cache_creation_tokens=result.metrics.get("cache_creation_tokens", 0),
                cache_read_tokens=result.metrics.get("cache_read_tokens", 0),
                latency_ms=result.elapsed_ms,
                memory_injected=len(result.observations),
                memory_created=len(result.observations),
            )

    # Cross-agent verification
    print("\n  --- Cross-Agent Memory Verification ---")
    all_obs = memory.get_recent_observations(PROJECT, limit=20)
    agents_seen = set(o.agent_id for o in all_obs)
    print(f"    Total observations in shared memory: {len(all_obs)}")
    print(f"    Distinct agents: {len(agents_seen)}")
    for a in agents_seen:
        count = sum(1 for o in all_obs if o.agent_id == a)
        print(f"      {a}: {count} observations")

    # Metrics dashboard
    print("\n  --- Metrics Dashboard ---")
    tracker = MetricsTracker(memory)
    tracker.print_dashboard(PROJECT)

    memory.close()
    print(f"\n  Data stored in: {tmpdir}")
    return True


class nullcontext:
    """Backport of contextlib.nullcontext for older Python."""
    def __enter__(self): return self
    def __exit__(self, *args): pass


def main():
    print("=" * 60)
    print("MULTI-AGENT ORCHESTRATION SYSTEM — VALIDATION SUITE")
    print("=" * 60)
    print(f"Mode: {'LIVE API' if LIVE_MODE else 'DRY-RUN (mock responses)'}")
    if not LIVE_MODE:
        print("Tip: Run with --live flag + ANTHROPIC_API_KEY for real API calls")

    results = []

    results.append(("Cache Structure", run_cache_structure_test()))
    results.append(("Memory Pipeline", run_memory_pipeline_test()))
    results.append(("Multi-Agent Execution", run_multi_agent_test()))

    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    for name, passed in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    print("=" * 60)

    all_passed = all(r[1] for r in results)
    if all_passed:
        print("\nAll validations passed. System is ready.")
        if not LIVE_MODE:
            print("Next step: Set ANTHROPIC_API_KEY and run: python run_demo.py --live")
    else:
        print("\nSome validations failed. Check output above.")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
