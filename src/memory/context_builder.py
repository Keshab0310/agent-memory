"""
Context Builder — Dynamic context injection with token budgeting.

Adapted from claude-mem's ContextBuilder + TokenCalculator pattern.
Assembles minimal, relevant context for each agent call.
"""

from dataclasses import dataclass
from typing import Optional

from .store import MemoryStore, Observation, Summary

CHARS_PER_TOKEN = 4


@dataclass
class ContextBudget:
    """Token budget allocation for context injection.

    Default 8000 tokens = 4% of Sonnet's 200K window.
    Quadruples effective agent memory vs the original 2000.
    """
    total: int = 8000
    task_description: int = 800
    own_observations: int = 4000
    cross_agent: int = 2400
    summaries: int = 800


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def format_observation_compact(obs: Observation) -> str:
    """Format observation into minimal text representation."""
    parts = [f"[{obs.obs_type}] {obs.title}"]
    if obs.subtitle:
        parts.append(f"  {obs.subtitle}")
    if obs.facts:
        for fact in obs.facts[:3]:  # Cap at 3 facts
            parts.append(f"  - {fact}")
    if obs.narrative and len(obs.narrative) <= 200:
        parts.append(f"  {obs.narrative}")
    elif obs.narrative:
        parts.append(f"  {obs.narrative[:200]}...")
    return "\n".join(parts)


def format_summary_compact(summary: Summary) -> str:
    """Format summary into minimal text."""
    parts = []
    if summary.request:
        parts.append(f"Task: {summary.request}")
    if summary.completed:
        parts.append(f"Done: {summary.completed}")
    if summary.learned:
        parts.append(f"Learned: {summary.learned}")
    if summary.next_steps:
        parts.append(f"Next: {summary.next_steps}")
    return " | ".join(parts)


class ContextBuilder:
    """Builds token-budgeted context for agent calls.

    Mirrors claude-mem's ContextBuilder pattern:
    1. Calculate token economics
    2. Query observations within budget
    3. Render timeline with full/compact modes
    4. Inject cross-agent observations by relevance
    """

    def __init__(self, memory: MemoryStore, budget: Optional[ContextBudget] = None):
        self.memory = memory
        self.budget = budget or ContextBudget()

    def build(
        self,
        project: str,
        agent_id: str,
        task_description: str,
        session_id: Optional[str] = None,
    ) -> str:
        """Build context string within token budget."""
        sections: list[str] = []
        budget_remaining = self.budget.total

        # Section 1: Task description (always included)
        task_text = f"## Current Task\n{task_description}"
        task_tokens = estimate_tokens(task_text)
        if task_tokens <= budget_remaining:
            sections.append(task_text)
            budget_remaining -= task_tokens

        # Section 2: This agent's recent observations
        own_budget = min(self.budget.own_observations, budget_remaining)
        own_obs = self.memory.get_recent_observations(
            project=project, agent_id=agent_id, limit=5
        )
        own_section_parts = ["## Your Recent Work"]
        for obs in own_obs:
            text = format_observation_compact(obs)
            tokens = estimate_tokens(text)
            if tokens <= own_budget:
                own_section_parts.append(text)
                own_budget -= tokens
                budget_remaining -= tokens
            else:
                break

        if len(own_section_parts) > 1:
            sections.append("\n".join(own_section_parts))

        # Section 3: Cross-agent observations (semantic search)
        cross_budget = min(self.budget.cross_agent, budget_remaining)
        if cross_budget > 100:
            cross_obs = self.memory.semantic_search(
                query=task_description,
                project=project,
                limit=3,
                exclude_agent=agent_id,
            )
            if cross_obs:
                cross_parts = ["## Related Work (Other Agents)"]
                for obs in cross_obs:
                    text = f"[{obs.agent_id}] {format_observation_compact(obs)}"
                    tokens = estimate_tokens(text)
                    if tokens <= cross_budget:
                        cross_parts.append(text)
                        cross_budget -= tokens
                        budget_remaining -= tokens
                if len(cross_parts) > 1:
                    sections.append("\n".join(cross_parts))

        return "\n\n".join(sections)

    def get_token_report(self, context: str) -> dict:
        """Report token usage for a built context."""
        tokens = estimate_tokens(context)
        return {
            "context_tokens": tokens,
            "budget_total": self.budget.total,
            "utilization_pct": round(tokens / self.budget.total * 100, 1),
        }
