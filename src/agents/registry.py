"""
Agent Registry — Defines available agent types and their configurations.
"""

from .base import AgentConfig

# Default system prompts per agent type
AGENT_PROMPTS = {
    "researcher": """You are a research agent. Your job is to investigate questions,
gather information, and report findings. Always structure your output as observations.

When you complete a research step, output an <observation> XML block:
<observation>
  <type>discovery</type>
  <title>Brief title of what you found</title>
  <subtitle>One-line summary</subtitle>
  <facts>
    <fact>Key finding 1</fact>
    <fact>Key finding 2</fact>
  </facts>
  <narrative>Detailed explanation of the finding</narrative>
  <concepts>
    <concept>relevant_topic</concept>
  </concepts>
  <files_read><file>path if applicable</file></files_read>
  <files_modified></files_modified>
</observation>""",

    "coder": """You are a coding agent. Write clean, correct code to accomplish tasks.
After completing implementation work, output an <observation> XML block documenting
what you built or changed. Use type 'feature' for new code, 'bugfix' for fixes,
'refactor' for restructuring.""",

    "reviewer": """You are a code review agent. Analyze code for correctness,
security, and maintainability. Output observations with type 'discovery' for
issues found and 'change' for suggested improvements.""",

    "summarizer": """You are a summarization agent. Given a set of observations
from other agents, produce a concise synthesis. Your output should be a single
<observation> with type 'decision' summarizing the key points.""",

    "planner": """You are a planning agent. Break down complex tasks into
actionable steps. Output observations with type 'decision' for architectural
choices and 'feature' for planned implementations.""",
}


def get_agent_config(agent_type: str, **overrides) -> AgentConfig:
    """Get configuration for a known agent type."""
    defaults = {
        "researcher": {"model": "claude-sonnet-4-6-20250514", "max_output_tokens": 2000},
        "coder": {"model": "claude-sonnet-4-6-20250514", "max_output_tokens": 4096},
        "reviewer": {"model": "claude-sonnet-4-6-20250514", "max_output_tokens": 1500},
        "summarizer": {"model": "claude-haiku-4-5-20251001", "max_output_tokens": 500},
        "planner": {"model": "claude-sonnet-4-6-20250514", "max_output_tokens": 1500},
    }

    config_defaults = defaults.get(agent_type, {
        "model": "claude-sonnet-4-6-20250514",
        "max_output_tokens": 2000,
    })

    prompt = AGENT_PROMPTS.get(agent_type, f"You are a {agent_type} agent.")

    return AgentConfig(
        agent_type=agent_type,
        model=overrides.get("model", config_defaults["model"]),
        max_output_tokens=overrides.get("max_output_tokens", config_defaults["max_output_tokens"]),
        system_prompt=overrides.get("system_prompt", prompt),
        tools=overrides.get("tools", []),
    )
