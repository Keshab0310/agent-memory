"""
Local LLM demo: 3 agents using Ollama (phi4).

No API key needed — runs entirely on your machine.

Usage:
  python run_local.py                          # Default: Ollama + phi4
  python run_local.py --model qwen2.5:7b       # Different model
  python run_local.py --url http://localhost:1234/v1  # LM Studio
"""

import os
import sys
import time
import argparse
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.memory.store import MemoryStore
from src.memory.context_builder import ContextBuilder, ContextBudget
from src.agents.base import AgentConfig
from src.agents.local_llm import LocalLLMAgent, LocalCondenser
from src.metrics.tracker import MetricsTracker


PROJECT = "local-demo"

# Agent system prompts (shorter for local models with smaller context windows)
PROMPTS = {
    "researcher": """You are a research agent. Investigate the given topic and report findings.

IMPORTANT: Structure your output as an XML observation block:
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
    <concept>topic1</concept>
  </concepts>
  <files_read></files_read>
  <files_modified></files_modified>
</observation>""",

    "coder": """You are a coding agent. Write clean, correct code.

After your code, include an XML observation:
<observation>
  <type>feature</type>
  <title>What you built</title>
  <subtitle>One-line summary</subtitle>
  <facts>
    <fact>Implementation detail 1</fact>
  </facts>
  <narrative>Explanation of approach</narrative>
  <concepts><concept>topic</concept></concepts>
  <files_read></files_read>
  <files_modified></files_modified>
</observation>""",

    "reviewer": """You are a code review agent. Analyze code for correctness and improvements.

Include an XML observation with your findings:
<observation>
  <type>discovery</type>
  <title>Review finding</title>
  <subtitle>Summary</subtitle>
  <facts>
    <fact>Finding 1</fact>
  </facts>
  <narrative>Detailed review</narrative>
  <concepts><concept>review</concept></concepts>
  <files_read></files_read>
  <files_modified></files_modified>
</observation>""",
}


def main():
    parser = argparse.ArgumentParser(description="Multi-agent demo with local LLM")
    parser.add_argument("--model", default="phi4:latest", help="Model name (default: phi4:latest)")
    parser.add_argument("--url", default="http://localhost:11434/v1", help="OpenAI-compatible API URL")
    parser.add_argument("--api-key", default="ollama", help="API key if needed")
    args = parser.parse_args()

    print("=" * 60)
    print("MULTI-AGENT SYSTEM - LOCAL LLM MODE")
    print("=" * 60)
    print(f"  Model:    {args.model}")
    print(f"  Endpoint: {args.url}")

    # Verify connectivity
    print(f"\n  Checking connection...", end=" ")
    try:
        from openai import OpenAI
        client = OpenAI(base_url=args.url, api_key=args.api_key)
        models = client.models.list()
        available = [m.id for m in models.data]
        print(f"OK ({len(available)} models available)")
        if args.model not in available and not any(args.model in m for m in available):
            print(f"  WARNING: '{args.model}' not found. Available: {available[:5]}")
            print(f"  Proceeding anyway (Ollama auto-pulls models)...")
    except Exception as e:
        print(f"FAILED\n  Error: {e}")
        print(f"\n  Make sure your local LLM server is running:")
        print(f"    Ollama:    ollama serve")
        print(f"    LM Studio: Start the app and enable API server")
        return 1

    # Setup memory
    tmpdir = tempfile.mkdtemp()
    memory = MemoryStore(
        sqlite_path=os.path.join(tmpdir, "local.db"),
        chroma_path=os.path.join(tmpdir, "chroma"),
    )
    condenser = LocalCondenser(
        memory=memory, model=args.model, base_url=args.url, threshold=5
    )
    context_builder = ContextBuilder(memory, ContextBudget(total=1500))  # Smaller budget for local models

    # Define agent pipeline
    agents_tasks = [
        ("researcher", "What are the 3 most effective strategies to reduce LLM token usage in multi-agent systems? Be concise and specific."),
        ("coder", "Write a Python function called `build_context(observations, budget=2000)` that selects observations to inject into an LLM prompt while staying under the token budget. Keep it simple."),
        ("reviewer", "Review this approach: using a greedy algorithm to select observations for context injection with a fixed token budget. What are the strengths and weaknesses? Be brief."),
    ]

    shared_context = "We are building a multi-agent LLM orchestration system that shares memory between agents to minimize redundant API calls."

    print("\n" + "=" * 60)
    print("RUNNING AGENT PIPELINE")
    print("=" * 60)

    results = []
    for agent_type, task in agents_tasks:
        print(f"\n{'─' * 60}")
        print(f"  AGENT: {agent_type.upper()}")
        print(f"  TASK:  {task[:70]}...")
        print(f"{'─' * 60}")

        config = AgentConfig(
            agent_type=agent_type,
            model=args.model,
            max_output_tokens=1024,
            system_prompt=PROMPTS[agent_type],
        )

        agent = LocalLLMAgent(
            config=config,
            memory=memory,
            project=PROJECT,
            base_url=args.url,
            api_key=args.api_key,
            context_builder=context_builder,
            condenser=condenser,
        )

        start = time.time()
        print(f"  Generating...", end=" ", flush=True)
        result = agent.execute(task, shared_context=shared_context)
        elapsed = time.time() - start
        print(f"done ({elapsed:.1f}s)")

        # Display result
        print(f"\n  Response ({len(result.response_text)} chars):")
        # Show first 500 chars
        preview = result.response_text[:500]
        for line in preview.split("\n"):
            print(f"    {line}")
        if len(result.response_text) > 500:
            print(f"    ... [{len(result.response_text) - 500} more chars]")

        print(f"\n  Observations extracted: {len(result.observations)}")
        for obs in result.observations:
            print(f"    [{obs.obs_type}] {obs.title[:60]}")
            if obs.facts:
                for f in obs.facts[:2]:
                    print(f"      - {f[:80]}")

        print(f"\n  Tokens: {result.metrics['input_tokens']} in / {result.metrics['output_tokens']} out")
        print(f"  Latency: {result.elapsed_ms}ms")

        results.append(result)

    # Cross-agent memory verification
    print(f"\n{'=' * 60}")
    print("CROSS-AGENT MEMORY VERIFICATION")
    print(f"{'=' * 60}")

    all_obs = memory.get_recent_observations(PROJECT, limit=20)
    agents_seen = set(o.agent_id for o in all_obs)
    print(f"\n  Total observations in shared memory: {len(all_obs)}")
    print(f"  Distinct agents: {len(agents_seen)}")
    for a in sorted(agents_seen):
        agent_obs = [o for o in all_obs if o.agent_id == a]
        print(f"    {a}: {len(agent_obs)} observations")
        for o in agent_obs:
            print(f"      [{o.obs_type}] {o.title[:50]}")

    # Semantic search
    print(f"\n  Semantic search: 'token budget optimization'")
    search = memory.semantic_search("token budget optimization", PROJECT, limit=3)
    for r in search:
        print(f"    [{r.agent_id}] {r.title[:60]}")

    # Token economics
    economics = memory.get_token_economics(PROJECT)
    print(f"\n  Token Economics:")
    print(f"    Observations:     {economics['total_observations']}")
    print(f"    Discovery tokens: {economics['discovery_tokens']:,}")
    print(f"    Read tokens:      {economics['read_tokens']:,}")
    if economics['read_tokens'] > 0:
        print(f"    Compression:      {economics['discovery_tokens'] / economics['read_tokens']:.1f}:1")
    print(f"    Savings:          {economics['savings_percent']}%")

    # Metrics dashboard
    print(f"\n{'=' * 60}")
    tracker = MetricsTracker(memory)
    tracker.print_dashboard(PROJECT)

    memory.close()
    print(f"\nData: {tmpdir}")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
