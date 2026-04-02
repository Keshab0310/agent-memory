"""
Model Profiles — Plan-aware, model-aware auto-configuration.

Real-world Claude plan structure (as of 2026):
  - Pro Plan: Default is Sonnet 4.6 with 1M context.
    Opus 4.6 accessible but 1M variant needs /extra-usage.
    Usage is CAPPED — adaptive thinking on "High" burns limits fast.
    Pricing: $5 input / $25 output per 1M tokens (standard).
  - Max Plan: Unlimited usage, higher rate limits.
    Both Sonnet and Opus with 1M context, no caps.

Key insight: On Pro Plan, the context window is 1M for BOTH Sonnet and Opus,
but the usage cap means every injected token counts against your daily limit.
Opus on Pro with "High" effort thinking can consume 10-20x more tokens than
Sonnet for the same task. Our memory injection must account for this.

Usage:
    from src.profiles import detect_profile

    # Auto-detects model + plan from environment
    profile = detect_profile()

    # Or explicit
    profile = detect_profile("claude-sonnet-4-6-20250514")
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelProfile:
    """Complete configuration profile for a model + plan combination."""

    # Identity
    name: str
    description: str
    plan: str  # "pro", "max", "api", "local"

    # Model IDs
    work_model: str
    router_model: str
    condenser_model: str

    # Context window
    context_window: int
    max_output_tokens: int

    # Memory budgets (tokens)
    working_memory_total: int
    budget_task_description: int
    budget_own_observations: int
    budget_cross_agent: int
    budget_summaries: int

    # Observation rendering
    max_facts_per_obs: int
    max_narrative_chars: int
    max_own_observations: int
    max_cross_observations: int

    # Condensation
    condensation_threshold: int

    # Rate limits
    requests_per_minute: int
    tokens_per_minute: int

    # Agent output limits per type
    output_limits: dict = field(default_factory=dict)

    # Cost per 1M tokens (input/output)
    cost_per_1m_input: float = 0.0
    cost_per_1m_output: float = 0.0

    # Adaptive thinking budget control
    # On Pro Plan Opus, "High" effort burns limits fast.
    # We recommend capping thinking budget to save allowance.
    thinking_budget_tokens: Optional[int] = None


# ═══════════════════════════════════════════════════════════
# PRO PLAN PROFILES
# Usage is CAPPED. Every token counts against daily limit.
# Strategy: lean injection, aggressive condensation.
# ═══════════════════════════════════════════════════════════

SONNET_PRO_PROFILE = ModelProfile(
    name="sonnet-pro",
    description="Sonnet 4.6 on Pro Plan — 1M context, capped usage. Default for most Pro users.",
    plan="pro",

    work_model="claude-sonnet-4-6-20250514",
    router_model="claude-haiku-4-5-20251001",
    condenser_model="claude-haiku-4-5-20251001",

    # Pro Plan Sonnet has 1M context (NOT 200K)
    context_window=1_000_000,
    max_output_tokens=16_000,

    # Conservative: 8K memory. Pro usage is capped, don't waste tokens.
    # 8K = 0.8% of 1M window — barely noticeable in context but keeps usage low.
    working_memory_total=8_000,
    budget_task_description=800,
    budget_own_observations=4_000,
    budget_cross_agent=2_400,
    budget_summaries=800,

    max_facts_per_obs=3,
    max_narrative_chars=200,
    max_own_observations=5,
    max_cross_observations=3,

    # Condense at 5 — keep observation count low to reduce future injection size
    condensation_threshold=5,

    requests_per_minute=50,
    tokens_per_minute=80_000,

    output_limits={
        "researcher": 2000,
        "coder": 4096,
        "reviewer": 1500,
        "summarizer": 500,
        "planner": 1500,
    },

    cost_per_1m_input=5.0,
    cost_per_1m_output=25.0,
    thinking_budget_tokens=None,  # Sonnet thinking is cheaper, no need to cap
)

OPUS_PRO_PROFILE = ModelProfile(
    name="opus-pro",
    description="Opus 4.6 on Pro Plan — 1M context, VERY limited usage. Ultra-conservative.",
    plan="pro",

    work_model="claude-opus-4-6-20250514",
    router_model="claude-haiku-4-5-20251001",
    condenser_model="claude-haiku-4-5-20251001",

    # 1M context available but usage drains FAST on Pro with Opus
    context_window=1_000_000,
    max_output_tokens=8_000,  # Hard cap — Opus output is 5x more expensive

    # MINIMAL memory injection. On Pro, Opus messages are precious.
    # 50K injected context on Opus = 50K * $5/1M = $0.25 PER MESSAGE just for memory.
    # With "High" adaptive thinking, Opus may generate 20K+ thinking tokens on top.
    # A single Opus message can cost $0.25-1.50 on Pro. We can't add to that.
    #
    # Strategy: inject LESS context but make it HIGHER QUALITY.
    # Opus is smart enough to do more with less. Give it the best 5K tokens, not 50K mediocre ones.
    working_memory_total=5_000,
    budget_task_description=500,
    budget_own_observations=2_500,
    budget_cross_agent=1_500,
    budget_summaries=500,

    # Quality over quantity — Opus extracts more value from each observation
    max_facts_per_obs=5,
    max_narrative_chars=300,
    max_own_observations=3,    # Only the 3 most relevant
    max_cross_observations=2,

    # Condense VERY aggressively — fewer stored observations = less to inject later
    condensation_threshold=3,

    # Pro Plan has tighter limits, and Opus burns them faster
    requests_per_minute=30,
    tokens_per_minute=60_000,

    output_limits={
        "researcher": 1500,
        "coder": 3000,
        "reviewer": 1000,
        "summarizer": 400,
        "planner": 1000,
    },

    cost_per_1m_input=5.0,
    cost_per_1m_output=25.0,

    # CRITICAL: Cap adaptive thinking to prevent runaway token usage.
    # "High" effort on Opus can generate 30K+ thinking tokens per message.
    # Capping at 10K saves ~60% of thinking costs while keeping quality high.
    thinking_budget_tokens=10_000,
)


# ═══════════════════════════════════════════════════════════
# MAX PLAN PROFILES
# Unlimited usage. Go wide with memory injection.
# ═══════════════════════════════════════════════════════════

SONNET_MAX_PROFILE = ModelProfile(
    name="sonnet-max",
    description="Sonnet 4.6 on Max Plan — 1M context, unlimited usage. Generous memory.",
    plan="max",

    work_model="claude-sonnet-4-6-20250514",
    router_model="claude-haiku-4-5-20251001",
    condenser_model="claude-haiku-4-5-20251001",

    context_window=1_000_000,
    max_output_tokens=16_000,

    # Max Plan: we can afford 16K memory (1.6% of 1M)
    working_memory_total=16_000,
    budget_task_description=1_500,
    budget_own_observations=8_000,
    budget_cross_agent=5_000,
    budget_summaries=1_500,

    max_facts_per_obs=5,
    max_narrative_chars=400,
    max_own_observations=10,
    max_cross_observations=5,

    condensation_threshold=10,

    requests_per_minute=100,
    tokens_per_minute=200_000,

    output_limits={
        "researcher": 4000,
        "coder": 8192,
        "reviewer": 3000,
        "summarizer": 1000,
        "planner": 3000,
    },

    cost_per_1m_input=5.0,
    cost_per_1m_output=25.0,
    thinking_budget_tokens=None,
)

OPUS_MAX_PROFILE = ModelProfile(
    name="opus-max",
    description="Opus 4.6 on Max Plan — 1M context, unlimited usage. Maximum memory.",
    plan="max",

    work_model="claude-opus-4-6-20250514",
    router_model="claude-haiku-4-5-20251001",
    condenser_model="claude-haiku-4-5-20251001",

    context_window=1_000_000,
    max_output_tokens=64_000,

    # Max Plan + Opus: go wide. 50K memory = 5% of window.
    working_memory_total=50_000,
    budget_task_description=2_000,
    budget_own_observations=25_000,
    budget_cross_agent=18_000,
    budget_summaries=5_000,

    max_facts_per_obs=10,
    max_narrative_chars=1000,
    max_own_observations=20,
    max_cross_observations=10,

    condensation_threshold=25,

    requests_per_minute=100,
    tokens_per_minute=400_000,

    output_limits={
        "researcher": 8000,
        "coder": 16000,
        "reviewer": 4000,
        "summarizer": 2000,
        "planner": 4000,
    },

    cost_per_1m_input=5.0,
    cost_per_1m_output=25.0,
    thinking_budget_tokens=None,  # Unlimited plan, no need to cap
)


# ═══════════════════════════════════════════════════════════
# API PLAN (direct API key, pay-per-token)
# ═══════════════════════════════════════════════════════════

SONNET_API_PROFILE = ModelProfile(
    name="sonnet-api",
    description="Sonnet 4.6 via API key — pay per token, 200K context.",
    plan="api",

    work_model="claude-sonnet-4-6-20250514",
    router_model="claude-haiku-4-5-20251001",
    condenser_model="claude-haiku-4-5-20251001",

    context_window=200_000,
    max_output_tokens=16_000,

    working_memory_total=8_000,
    budget_task_description=800,
    budget_own_observations=4_000,
    budget_cross_agent=2_400,
    budget_summaries=800,

    max_facts_per_obs=3,
    max_narrative_chars=200,
    max_own_observations=5,
    max_cross_observations=3,

    condensation_threshold=5,

    requests_per_minute=50,
    tokens_per_minute=80_000,

    output_limits={
        "researcher": 2000,
        "coder": 4096,
        "reviewer": 1500,
        "summarizer": 500,
        "planner": 1500,
    },

    cost_per_1m_input=3.0,
    cost_per_1m_output=15.0,
)

HAIKU_PROFILE = ModelProfile(
    name="haiku",
    description="Haiku 4.5 — 200K context, cheapest and fastest.",
    plan="api",

    work_model="claude-haiku-4-5-20251001",
    router_model="claude-haiku-4-5-20251001",
    condenser_model="claude-haiku-4-5-20251001",

    context_window=200_000,
    max_output_tokens=8_000,

    working_memory_total=4_000,
    budget_task_description=500,
    budget_own_observations=2_000,
    budget_cross_agent=1_000,
    budget_summaries=500,

    max_facts_per_obs=2,
    max_narrative_chars=100,
    max_own_observations=3,
    max_cross_observations=2,

    condensation_threshold=3,

    requests_per_minute=100,
    tokens_per_minute=200_000,

    output_limits={
        "researcher": 1000,
        "coder": 2000,
        "reviewer": 800,
        "summarizer": 300,
        "planner": 800,
    },

    cost_per_1m_input=0.80,
    cost_per_1m_output=4.0,
)

LOCAL_PROFILE = ModelProfile(
    name="local",
    description="Local LLM (Ollama/LM Studio). No API costs.",
    plan="local",

    work_model="phi4:latest",
    router_model="phi4:latest",
    condenser_model="phi4:latest",

    context_window=16_000,
    max_output_tokens=2_048,

    working_memory_total=1_500,
    budget_task_description=300,
    budget_own_observations=700,
    budget_cross_agent=300,
    budget_summaries=200,

    max_facts_per_obs=2,
    max_narrative_chars=100,
    max_own_observations=3,
    max_cross_observations=1,

    condensation_threshold=3,

    requests_per_minute=999,
    tokens_per_minute=999_999,

    output_limits={
        "researcher": 1024,
        "coder": 1024,
        "reviewer": 512,
        "summarizer": 256,
        "planner": 512,
    },

    cost_per_1m_input=0.0,
    cost_per_1m_output=0.0,
)


# ═══════════════════════════════════════════════════════════
# Profile Registry
# ═══════════════════════════════════════════════════════════

PROFILES = {
    # Explicit plan+model combos
    "opus-max": OPUS_MAX_PROFILE,
    "opus-pro": OPUS_PRO_PROFILE,
    "sonnet-max": SONNET_MAX_PROFILE,
    "sonnet-pro": SONNET_PRO_PROFILE,
    "sonnet-api": SONNET_API_PROFILE,
    "haiku": HAIKU_PROFILE,
    "local": LOCAL_PROFILE,
    # Short aliases (default to Pro since that's the most common plan)
    "opus": OPUS_PRO_PROFILE,
    "sonnet": SONNET_PRO_PROFILE,
}


def get_profile(name: str) -> ModelProfile:
    """Get a profile by name. Defaults to sonnet-pro."""
    return PROFILES.get(name.lower(), SONNET_PRO_PROFILE)


def detect_profile(model_id: Optional[str] = None) -> ModelProfile:
    """Auto-detect the correct profile from model ID + plan.

    Detection chain:
    1. Model ID (arg or ANTHROPIC_MODEL / CLAUDE_MODEL env)
    2. Plan detection (CLAUDE_CODE_MAX_PLAN or ANTHROPIC_API_KEY presence)
    3. Combine model + plan to select profile

    The critical distinction:
    - Pro Plan Opus: 5K memory, condense at 3, cap thinking at 10K
    - Max Plan Opus: 50K memory, condense at 25, no thinking cap
    - Pro Plan Sonnet (default): 8K memory, condense at 5

    Examples:
        detect_profile("claude-opus-4-6-20250514")
          -> Pro user? OPUS_PRO (lean 5K)
          -> Max user? OPUS_MAX (rich 50K)

        detect_profile()  # no args, reads env
          -> Pro + Sonnet (default): SONNET_PRO
    """
    import os

    if model_id is None:
        model_id = (
            os.environ.get("ANTHROPIC_MODEL")
            or os.environ.get("CLAUDE_MODEL")
            or ""
        )

    model_lower = model_id.lower()

    # Detect plan tier
    plan = _detect_plan()

    # Match model family + plan
    if "opus" in model_lower:
        if plan == "max":
            return OPUS_MAX_PROFILE
        return OPUS_PRO_PROFILE  # Pro is the safe default for Opus

    elif "sonnet" in model_lower:
        if plan == "max":
            return SONNET_MAX_PROFILE
        if plan == "api":
            return SONNET_API_PROFILE
        return SONNET_PRO_PROFILE

    elif "haiku" in model_lower:
        return HAIKU_PROFILE

    elif _is_local_model(model_lower):
        return LOCAL_PROFILE

    else:
        # Unknown model — default based on plan
        if plan == "max":
            return SONNET_MAX_PROFILE
        return SONNET_PRO_PROFILE


def _detect_plan() -> str:
    """Detect which plan the user is on.

    Returns: "max", "pro", "api", or "local"

    Detection signals:
    - CLAUDE_CODE_MAX_PLAN=1/true -> "max"
    - AGENT_MEMORY_PLAN env override -> whatever they set
    - ANTHROPIC_API_KEY present (not from Claude Code) -> "api"
    - Running inside Claude Code (no API key, subscription auth) -> "pro"
    - Default -> "pro" (safest — treats tokens as precious)
    """
    import os

    # Explicit override — user knows their plan
    explicit = os.environ.get("AGENT_MEMORY_PLAN", "").lower()
    if explicit in ("max", "pro", "api", "local"):
        return explicit

    # Max Plan flag
    if os.environ.get("CLAUDE_CODE_MAX_PLAN", "").lower() in ("1", "true"):
        return "max"

    # If ANTHROPIC_API_KEY is set AND we're not in Claude Code, it's API usage
    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    in_claude_code = bool(os.environ.get("CLAUDE_CODE_VERSION") or os.environ.get("CLAUDE_PLUGIN_ROOT"))

    if has_api_key and not in_claude_code:
        return "api"

    # Default: assume Pro (most conservative for subscription users)
    return "pro"


def _is_local_model(model_lower: str) -> bool:
    """Check if a model ID looks like a local/Ollama model."""
    local_indicators = [
        ":", "phi", "llama", "mistral", "qwen", "gemma", "deepseek",
        "codellama", "vicuna", "orca", "neural", "yi:", "command-r",
    ]
    return any(indicator in model_lower for indicator in local_indicators)


def get_context_budget(profile: ModelProfile):
    """Convert a profile to a ContextBudget dataclass."""
    from src.memory.context_builder import ContextBudget
    return ContextBudget(
        total=profile.working_memory_total,
        task_description=profile.budget_task_description,
        own_observations=profile.budget_own_observations,
        cross_agent=profile.budget_cross_agent,
        summaries=profile.budget_summaries,
    )
