"""
Prompt Cache Manager — Anthropic prompt caching wrapper.

Implements the three-tier caching strategy:
- Tier 0: System prompt (cache_control: ephemeral, 5-min TTL)
- Tier 1: Shared project context (cache_control: ephemeral)
- Tier 2: Dynamic episodic context (no caching)

Prompt caching reduces input token costs by 90% on cache hits.
Cache write costs 25% more than normal, but amortizes across all
subsequent requests within the 5-minute TTL.
"""

from typing import Optional


def build_cached_messages(
    system_prompt: str,
    shared_context: str,
    episodic_context: str,
    user_message: str,
    tools: Optional[list[dict]] = None,
) -> dict:
    """Build an Anthropic messages.create() payload with prompt caching.

    Returns kwargs dict ready to be unpacked into client.messages.create(**kwargs).

    Cache layout:
    ┌──────────────────────────┐
    │ System prompt            │ ← cache breakpoint (Tier 0)
    ├──────────────────────────┤
    │ Shared project context   │ ← cache breakpoint (Tier 1)
    ├──────────────────────────┤
    │ Episodic context         │ ← NOT cached (changes per turn)
    ├──────────────────────────┤
    │ User message             │ ← NOT cached
    └──────────────────────────┘

    The system prompt and shared context are identical across multiple
    calls for the same agent type and project. By placing cache breakpoints
    after each, Anthropic stores them server-side and charges only 10%
    on subsequent requests.
    """

    # System messages with cache breakpoints
    system = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},  # 5-min TTL
        }
    ]

    # Build user messages — shared context gets its own breakpoint
    messages = []

    # Shared context as a prefill turn (cached)
    if shared_context:
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"<shared_context>\n{shared_context}\n</shared_context>",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        })
        messages.append({
            "role": "assistant",
            "content": "I've loaded the shared project context. Ready for the task.",
        })

    # Episodic context + user message (NOT cached — changes every turn)
    user_content_parts = []
    if episodic_context:
        user_content_parts.append({
            "type": "text",
            "text": f"<agent_memory>\n{episodic_context}\n</agent_memory>",
        })
    user_content_parts.append({
        "type": "text",
        "text": user_message,
    })

    messages.append({
        "role": "user",
        "content": user_content_parts,
    })

    payload = {
        "system": system,
        "messages": messages,
    }

    # Tools also benefit from caching when using the same tool set
    if tools:
        # Tool definitions are static — mark last one with cache breakpoint
        cached_tools = []
        for i, tool in enumerate(tools):
            t = dict(tool)
            if i == len(tools) - 1:
                t["cache_control"] = {"type": "ephemeral"}
            cached_tools.append(t)
        payload["tools"] = cached_tools

    return payload


def extract_cache_metrics(response) -> dict:
    """Extract cache performance metrics from an Anthropic API response.

    Args:
        response: An anthropic.types.Message object

    Returns:
        Dict with cache performance data
    """
    usage = response.usage

    input_tokens = getattr(usage, "input_tokens", 0)
    output_tokens = getattr(usage, "output_tokens", 0)
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0)
    cache_read = getattr(usage, "cache_read_input_tokens", 0)

    total_input = input_tokens + cache_creation + cache_read
    cache_hit_rate = cache_read / total_input if total_input > 0 else 0

    # Cost estimation (relative units)
    # Normal: 1x, Cache write: 1.25x, Cache read: 0.1x
    effective_cost = (
        input_tokens * 1.0
        + cache_creation * 1.25
        + cache_read * 0.1
        + output_tokens * 1.0  # Output always full price
    )

    naive_cost = total_input + output_tokens  # Without caching

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_creation,
        "cache_read_tokens": cache_read,
        "cache_hit_rate": round(cache_hit_rate, 3),
        "effective_cost_units": round(effective_cost, 1),
        "naive_cost_units": naive_cost,
        "savings_pct": round((1 - effective_cost / naive_cost) * 100, 1) if naive_cost > 0 else 0,
    }
