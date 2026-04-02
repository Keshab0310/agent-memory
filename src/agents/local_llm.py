"""
Local LLM Provider — OpenAI-compatible adapter for Ollama, LM Studio, vLLM, etc.

Wraps any local LLM that exposes an OpenAI-compatible /v1/chat/completions endpoint.
Ollama exposes this at http://localhost:11434/v1 by default.

Note: Local LLMs don't support Anthropic prompt caching, so the caching layer
is bypassed. Token savings come entirely from the memory compression pipeline.
"""

import time
from typing import Optional

from openai import OpenAI

from ..memory.store import MemoryStore, Observation
from ..memory.context_builder import ContextBuilder
from ..memory.condenser import MemoryCondenser
from .base import Agent, AgentConfig, AgentResult

import re
import uuid


class LocalLLMAgent(Agent):
    """Agent powered by a local LLM via OpenAI-compatible API."""

    def __init__(
        self,
        config: AgentConfig,
        memory: MemoryStore,
        project: str,
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "ollama",  # Ollama doesn't need a real key
        context_builder: Optional[ContextBuilder] = None,
        condenser: Optional[MemoryCondenser] = None,
    ):
        # Skip parent __init__ to avoid creating anthropic client
        self.config = config
        self.memory = memory
        self.project = project
        self.context_builder = context_builder or ContextBuilder(memory)
        self.condenser = condenser or MemoryCondenser(
            memory, model=config.model  # Won't work for condensation — see note below
        )
        self.agent_id = f"{config.agent_type}-{uuid.uuid4().hex[:8]}"
        self.session_id = f"sess-{uuid.uuid4().hex[:12]}"
        self.turn_count = 0

        # Local LLM client
        self.local_client = OpenAI(base_url=base_url, api_key=api_key)
        self.base_url = base_url

    def execute(self, task_description: str, shared_context: str = "") -> AgentResult:
        """Execute via local LLM with full memory integration."""
        start = time.time()
        self.turn_count += 1

        # Build context from memory (same as Anthropic path)
        episodic_context = self.context_builder.build(
            project=self.project,
            agent_id=self.agent_id,
            task_description=task_description,
            session_id=self.session_id,
        )

        # Build messages for OpenAI-compatible API
        messages = []

        # System prompt
        messages.append({
            "role": "system",
            "content": self.config.system_prompt,
        })

        # Shared context
        if shared_context:
            messages.append({
                "role": "user",
                "content": f"<shared_context>\n{shared_context}\n</shared_context>",
            })
            messages.append({
                "role": "assistant",
                "content": "I've loaded the shared project context. Ready for the task.",
            })

        # Episodic context + task
        user_content = ""
        if episodic_context:
            user_content += f"<agent_memory>\n{episodic_context}\n</agent_memory>\n\n"
        user_content += task_description

        messages.append({"role": "user", "content": user_content})

        # Call local LLM
        response = self.local_client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            max_tokens=self.config.max_output_tokens,
            temperature=0.7,
        )

        response_text = response.choices[0].message.content or ""
        elapsed_ms = int((time.time() - start) * 1000)

        # Extract token usage
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0

        metrics = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_tokens": 0,  # No caching for local LLMs
            "cache_read_tokens": 0,
            "cache_hit_rate": 0,
            "savings_pct": 0,
            "provider": "local",
            "base_url": self.base_url,
        }

        # Extract and store observations (same XML parsing as Anthropic path)
        observations = self._extract_and_store_observations(response_text, metrics)

        # Log metrics
        self.memory.log_api_call(
            agent_id=self.agent_id,
            session_id=self.session_id,
            model=self.config.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            latency_ms=elapsed_ms,
            memory_injected=len(episodic_context) // 4,
            memory_created=len(observations),
        )

        # Check condensation
        self.condenser.check_and_condense(
            project=self.project,
            agent_id=self.agent_id,
            session_id=self.session_id,
        )

        return AgentResult(
            agent_id=self.agent_id,
            agent_type=self.config.agent_type,
            response_text=response_text,
            observations=observations,
            metrics=metrics,
            elapsed_ms=elapsed_ms,
        )


class LocalCondenser(MemoryCondenser):
    """Condensation using local LLM instead of Anthropic API."""

    def __init__(
        self,
        memory: MemoryStore,
        threshold: int = 5,
        model: str = "phi4:latest",
        base_url: str = "http://localhost:11434/v1",
    ):
        self.memory = memory
        self.threshold = threshold
        self.model = model
        self.local_client = OpenAI(base_url=base_url, api_key="ollama")

    def _condense(self, observations, project, agent_id, session_id):
        """Override to use local LLM for condensation."""
        from .base import Agent  # avoid circular
        from ..memory.store import Summary
        from ..memory.condenser import _parse_summary_xml, SUMMARY_SYSTEM_PROMPT

        obs_text_parts = []
        for obs in observations:
            parts = [f"[{obs.obs_type}] {obs.title}"]
            if obs.narrative:
                parts.append(obs.narrative)
            if obs.facts:
                parts.extend(f"- {f}" for f in obs.facts)
            obs_text_parts.append("\n".join(parts))

        observations_text = "\n---\n".join(obs_text_parts)
        user_prompt = f"Condense these {len(observations)} observations into a single summary:\n\n{observations_text}"

        response = self.local_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=500,
            temperature=0.3,
        )

        response_text = response.choices[0].message.content or ""
        parsed = _parse_summary_xml(response_text)

        if not parsed:
            parsed = {
                "request": observations[0].title,
                "investigated": ", ".join(o.title for o in observations),
                "learned": observations[-1].narrative or "",
                "completed": f"{len(observations)} observations processed",
                "next_steps": "",
            }

        import time as _time
        summary = Summary(
            agent_id=agent_id,
            project=project,
            session_id=session_id,
            request=parsed.get("request", ""),
            investigated=parsed.get("investigated", ""),
            learned=parsed.get("learned", ""),
            completed=parsed.get("completed", ""),
            next_steps=parsed.get("next_steps", ""),
            observation_count=len(observations),
            created_at_epoch=_time.time(),
        )

        summary_id = self.memory.store_summary(summary)
        summary.id = summary_id

        obs_ids = [o.id for o in observations if o.id is not None]
        self.memory.mark_observations_condensed(obs_ids)

        return summary
