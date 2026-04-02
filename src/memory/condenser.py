"""
Memory Condenser — Periodic summarization pipeline.

Adapted from claude-mem's buildSummaryPrompt pattern.
Prevents unbounded context growth by compressing N observations into 1 summary.
"""

import re
import time
from typing import Optional

import anthropic

from .store import MemoryStore, Observation, Summary

SUMMARY_SYSTEM_PROMPT = """You are a memory compression agent. Given a set of observations
from an AI agent's work session, produce a concise summary.

Output ONLY the following XML format:
<summary>
  <request>What was the agent trying to accomplish</request>
  <investigated>What was explored or analyzed</investigated>
  <learned>Key findings or insights</learned>
  <completed>What was actually done/changed</completed>
  <next_steps>What remains to be done</next_steps>
</summary>"""


def _parse_summary_xml(text: str) -> Optional[dict]:
    """Parse <summary> XML block from model response."""
    match = re.search(r"<summary>([\s\S]*?)</summary>", text)
    if not match:
        return None

    content = match.group(1)
    fields = {}
    for field_name in ("request", "investigated", "learned", "completed", "next_steps"):
        field_match = re.search(
            rf"<{field_name}>([\s\S]*?)</{field_name}>", content
        )
        fields[field_name] = field_match.group(1).strip() if field_match else None

    return fields


class MemoryCondenser:
    """Condenses agent observations into summaries at configurable intervals.

    Key design from claude-mem:
    - Summaries REPLACE the observations they cover in working memory
    - Original observations remain in SQLite/Chroma for deep search
    - This bounds context growth to O(1) per agent
    """

    def __init__(
        self,
        memory: MemoryStore,
        threshold: int = 5,
        model: str = "claude-haiku-4-5-20251001",
    ):
        self.memory = memory
        self.threshold = threshold
        self.model = model
        self._client = None  # Lazy init — avoids requiring API key for local-only use

    @property
    def client(self):
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def check_and_condense(
        self, project: str, agent_id: str, session_id: str
    ) -> Optional[Summary]:
        """Check if condensation is needed and run it if so.

        Fails gracefully — if condensation errors, observations remain
        uncondensed and will be retried next threshold check.
        """
        try:
            uncondensed = self.memory.get_recent_observations(
                project=project,
                agent_id=agent_id,
                limit=self.threshold + 1,
                include_condensed=False,
            )

            if len(uncondensed) < self.threshold:
                return None

            # Take the oldest N observations to condense
            to_condense = sorted(uncondensed, key=lambda o: o.created_at_epoch)[
                : self.threshold
            ]
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Condensation check failed, will retry: {e}")
            return None

        try:
            return self._condense(to_condense, project, agent_id, session_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Condensation LLM call failed, will retry: {e}")
            return None

    def _condense(
        self,
        observations: list[Observation],
        project: str,
        agent_id: str,
        session_id: str,
    ) -> Summary:
        """Run condensation via LLM call."""
        # Build observation text for the summary prompt
        obs_text_parts = []
        for obs in observations:
            parts = [f"[{obs.obs_type}] {obs.title}"]
            if obs.narrative:
                parts.append(obs.narrative)
            if obs.facts:
                parts.extend(f"- {f}" for f in obs.facts)
            obs_text_parts.append("\n".join(parts))

        observations_text = "\n---\n".join(obs_text_parts)

        user_prompt = f"""Condense these {len(observations)} observations into a single summary:

{observations_text}"""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            system=SUMMARY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        response_text = response.content[0].text
        parsed = _parse_summary_xml(response_text)

        if not parsed:
            # Fallback: create a basic summary from the observations
            parsed = {
                "request": observations[0].title,
                "investigated": ", ".join(o.title for o in observations),
                "learned": observations[-1].narrative or "",
                "completed": f"{len(observations)} observations processed",
                "next_steps": "",
            }

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
            created_at_epoch=time.time(),
        )

        summary_id = self.memory.store_summary(summary)
        summary.id = summary_id

        # Mark observations as condensed (they remain searchable but excluded from working memory)
        obs_ids = [o.id for o in observations if o.id is not None]
        self.memory.mark_observations_condensed(obs_ids)

        return summary
