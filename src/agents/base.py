"""
Base Agent — Memory-integrated agent with prompt caching and observation pipeline.

Each agent:
1. Receives a task + injected context from the orchestrator
2. Executes via Anthropic API with prompt caching
3. Compresses its work into observations stored in shared memory
4. Triggers condensation when observation count exceeds threshold
"""

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import anthropic

from ..cache.prompt_cache import build_cached_messages, extract_cache_metrics
from ..memory.store import MemoryStore, Observation
from ..memory.context_builder import ContextBuilder
from ..memory.condenser import MemoryCondenser


@dataclass
class AgentConfig:
    agent_type: str
    model: str = "claude-sonnet-4-6-20250514"
    max_output_tokens: int = 2000
    system_prompt: str = ""
    tools: list[dict] = field(default_factory=list)


@dataclass
class AgentResult:
    agent_id: str
    agent_type: str
    response_text: str
    observations: list[Observation]
    metrics: dict
    elapsed_ms: int


class Agent:
    """A single agent with memory integration and prompt caching."""

    def __init__(
        self,
        config: AgentConfig,
        memory: MemoryStore,
        project: str,
        context_builder: Optional[ContextBuilder] = None,
        condenser: Optional[MemoryCondenser] = None,
    ):
        self.config = config
        self.memory = memory
        self.project = project
        self.context_builder = context_builder or ContextBuilder(memory)
        self.condenser = condenser or MemoryCondenser(memory)
        self.client = anthropic.Anthropic()
        self.agent_id = f"{config.agent_type}-{uuid.uuid4().hex[:8]}"
        self.session_id = f"sess-{uuid.uuid4().hex[:12]}"
        self.turn_count = 0

    def execute(self, task_description: str, shared_context: str = "") -> AgentResult:
        """Execute a single task turn with full memory integration."""
        start = time.time()
        self.turn_count += 1

        # Step 1: Build episodic context from memory
        episodic_context = self.context_builder.build(
            project=self.project,
            agent_id=self.agent_id,
            task_description=task_description,
            session_id=self.session_id,
        )

        # Step 2: Build cached API payload
        payload = build_cached_messages(
            system_prompt=self.config.system_prompt,
            shared_context=shared_context,
            episodic_context=episodic_context,
            user_message=task_description,
            tools=self.config.tools if self.config.tools else None,
        )

        # Step 3: Call Anthropic API
        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_output_tokens,
            **payload,
        )

        response_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                response_text += block.text

        # Step 4: Extract cache metrics
        cache_metrics = extract_cache_metrics(response)
        elapsed_ms = int((time.time() - start) * 1000)

        # Step 5: Extract and store observations from response
        observations = self._extract_and_store_observations(
            response_text, cache_metrics
        )

        # Step 6: Log API call metrics
        self.memory.log_api_call(
            agent_id=self.agent_id,
            session_id=self.session_id,
            model=self.config.model,
            input_tokens=cache_metrics["input_tokens"],
            output_tokens=cache_metrics["output_tokens"],
            cache_creation_tokens=cache_metrics["cache_creation_tokens"],
            cache_read_tokens=cache_metrics["cache_read_tokens"],
            latency_ms=elapsed_ms,
            memory_injected=len(episodic_context) // 4,  # Rough token count
            memory_created=len(observations),
        )

        # Step 7: Check if condensation is needed
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
            metrics=cache_metrics,
            elapsed_ms=elapsed_ms,
        )

    def _extract_and_store_observations(
        self, response_text: str, metrics: dict
    ) -> list[Observation]:
        """Parse <observation> XML blocks from response and store them.

        Uses the same XML format as claude-mem's parser.ts.
        """
        observations = []
        pattern = re.compile(r"<observation>([\s\S]*?)</observation>")

        for match in pattern.finditer(response_text):
            content = match.group(1)
            obs = Observation(
                agent_id=self.agent_id,
                project=self.project,
                session_id=self.session_id,
                obs_type=self._extract_field(content, "type") or "discovery",
                title=self._extract_field(content, "title") or "Untitled",
                subtitle=self._extract_field(content, "subtitle") or "",
                facts=self._extract_array(content, "facts", "fact"),
                narrative=self._extract_field(content, "narrative") or "",
                concepts=self._extract_array(content, "concepts", "concept"),
                files_read=self._extract_array(content, "files_read", "file"),
                files_modified=self._extract_array(content, "files_modified", "file"),
                discovery_tokens=(
                    metrics["input_tokens"]
                    + metrics["output_tokens"]
                    + metrics["cache_creation_tokens"]
                ),
            )
            obs_id = self.memory.store_observation(obs)
            obs.id = obs_id
            observations.append(obs)

        # If the response doesn't contain XML observations, auto-generate one
        if not observations and response_text.strip():
            obs = Observation(
                agent_id=self.agent_id,
                project=self.project,
                session_id=self.session_id,
                obs_type="discovery",
                title=response_text[:100].strip(),
                narrative=response_text[:500].strip() if len(response_text) > 100 else "",
                discovery_tokens=metrics["input_tokens"] + metrics["output_tokens"],
            )
            obs_id = self.memory.store_observation(obs)
            obs.id = obs_id
            observations.append(obs)

        return observations

    @staticmethod
    def _extract_field(content: str, field_name: str) -> Optional[str]:
        match = re.search(rf"<{field_name}>([\s\S]*?)</{field_name}>", content)
        return match.group(1).strip() if match else None

    @staticmethod
    def _extract_array(content: str, array_name: str, element_name: str) -> list[str]:
        array_match = re.search(
            rf"<{array_name}>([\s\S]*?)</{array_name}>", content
        )
        if not array_match:
            return []
        elements = re.findall(
            rf"<{element_name}>([\s\S]*?)</{element_name}>", array_match.group(1)
        )
        return [e.strip() for e in elements if e.strip()]
