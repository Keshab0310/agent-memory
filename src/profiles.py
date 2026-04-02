"""
Model Profiles — Pre-configured settings for different Claude models and plans.

Each profile adjusts token budgets, condensation thresholds, rate limits,
and model routing to match the model's context window and pricing.

Usage:
    from src.profiles import get_profile, PROFILES

    profile = get_profile("opus")  # or "sonnet", "haiku", "local"
    budget = profile.context_budget
    condenser_threshold = profile.condensation_threshold
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelProfile:
    """Complete configuration profile for a model tier."""

    # Identity
    name: str
    description: str

    # Model IDs
    work_model: str          # Primary model for agent work
    router_model: str        # Cheap model for routing decisions
    condenser_model: str     # Model for summarization/condensation

    # Context window
    context_window: int      # Total context window size in tokens
    max_output_tokens: int   # Default max output per agent call

    # Memory budgets (all in tokens)
    working_memory_total: int
    budget_task_description: int
    budget_own_observations: int
    budget_cross_agent: int
    budget_summaries: int

    # Observation rendering
    max_facts_per_obs: int          # Facts to show per observation in context
    max_narrative_chars: int        # Narrative truncation limit in context
    max_own_observations: int       # How many own observations to fetch
    max_cross_observations: int     # How many cross-agent observations to fetch

    # Condensation
    condensation_threshold: int     # Condense after N observations per agent

    # Rate limits
    requests_per_minute: int
    tokens_per_minute: int

    # Agent output limits per type
    output_limits: dict = field(default_factory=dict)

    # Cost per 1M tokens (input/output) for ROI tracking
    cost_per_1m_input: float = 0.0
    cost_per_1m_output: float = 0.0


# ── Profile Definitions ──

OPUS_PROFILE = ModelProfile(
    name="opus",
    description="Claude Opus 4.6 — 1M context, Max Plan. Maximum memory, relaxed condensation.",

    work_model="claude-opus-4-6-20250514",
    router_model="claude-haiku-4-5-20251001",
    condenser_model="claude-haiku-4-5-20251001",

    context_window=1_000_000,
    max_output_tokens=64_000,

    # 5% of 1M = 50K tokens for working memory
    working_memory_total=50_000,
    budget_task_description=2_000,
    budget_own_observations=25_000,
    budget_cross_agent=18_000,
    budget_summaries=5_000,

    # Rich observation rendering — we have room
    max_facts_per_obs=10,
    max_narrative_chars=1000,
    max_own_observations=20,
    max_cross_observations=10,

    # Condense less aggressively — context window is massive
    condensation_threshold=25,

    # Max Plan has generous limits
    requests_per_minute=100,
    tokens_per_minute=400_000,

    output_limits={
        "researcher": 8000,
        "coder": 16000,
        "reviewer": 4000,
        "summarizer": 2000,
        "planner": 4000,
    },

    cost_per_1m_input=15.0,
    cost_per_1m_output=75.0,
)

SONNET_PROFILE = ModelProfile(
    name="sonnet",
    description="Claude Sonnet 4.6 — 200K context. Balanced cost/performance.",

    work_model="claude-sonnet-4-6-20250514",
    router_model="claude-haiku-4-5-20251001",
    condenser_model="claude-haiku-4-5-20251001",

    context_window=200_000,
    max_output_tokens=16_000,

    # 4% of 200K = 8K tokens
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
    description="Claude Haiku 4.5 — 200K context. Cheapest, fastest. For cost-sensitive workloads.",

    work_model="claude-haiku-4-5-20251001",
    router_model="claude-haiku-4-5-20251001",
    condenser_model="claude-haiku-4-5-20251001",

    context_window=200_000,
    max_output_tokens=8_000,

    # Smaller budget — Haiku is cheap, prioritize speed over depth
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
    description="Local LLM (Ollama/LM Studio). No API costs, smaller context windows.",

    work_model="phi4:latest",
    router_model="phi4:latest",
    condenser_model="phi4:latest",

    context_window=16_000,  # Most local models have 4K-16K
    max_output_tokens=2_048,

    # Conservative — local models have small windows
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

    # No rate limits for local
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


PROFILES = {
    "opus": OPUS_PROFILE,
    "sonnet": SONNET_PROFILE,
    "haiku": HAIKU_PROFILE,
    "local": LOCAL_PROFILE,
}


def get_profile(name: str) -> ModelProfile:
    """Get a model profile by name. Defaults to sonnet."""
    return PROFILES.get(name.lower(), SONNET_PROFILE)


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
