"""
Orchestrator Router — Routes tasks to agents, manages lifecycle, merges results.

Design principles:
1. Use Haiku for routing decisions (cheap, fast)
2. Use Sonnet/Opus for actual agent work
3. Agents run concurrently where dependencies allow
4. All agents share memory through the MemoryStore
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import anthropic

from ..agents.base import Agent, AgentConfig, AgentResult
from ..agents.registry import get_agent_config
from ..memory.store import MemoryStore
from ..memory.context_builder import ContextBuilder
from ..memory.condenser import MemoryCondenser


@dataclass
class TaskNode:
    """A single task in the execution DAG."""
    task_id: str
    agent_type: str
    description: str
    depends_on: list[str] = field(default_factory=list)  # task_ids
    result: Optional[AgentResult] = None
    status: str = "pending"  # pending, running, completed, failed


@dataclass
class ExecutionPlan:
    """DAG of tasks to execute."""
    tasks: list[TaskNode]
    shared_context: str = ""

    def get_ready_tasks(self) -> list[TaskNode]:
        """Get tasks whose dependencies are all completed."""
        completed_ids = {t.task_id for t in self.tasks if t.status == "completed"}
        return [
            t for t in self.tasks
            if t.status == "pending" and all(d in completed_ids for d in t.depends_on)
        ]


class Orchestrator:
    """Routes user requests to agent teams and merges results."""

    def __init__(
        self,
        memory: MemoryStore,
        project: str,
        router_model: str = "claude-haiku-4-5-20251001",
        max_concurrent: int = 5,
    ):
        self.memory = memory
        self.project = project
        self.router_model = router_model
        self.max_concurrent = max_concurrent
        self.client = anthropic.Anthropic()
        self.context_builder = ContextBuilder(memory)
        self.condenser = MemoryCondenser(memory)

    async def run(self, user_request: str) -> str:
        """Full orchestration: plan → execute → merge."""
        # Step 1: Plan
        plan = await self.plan(user_request)

        # Step 2: Execute DAG
        await self.execute_plan(plan)

        # Step 3: Merge results
        return self.merge_results(plan, user_request)

    async def plan(self, user_request: str) -> ExecutionPlan:
        """Use Haiku to decompose the request into an agent execution plan.

        The router model is deliberately cheap — it only needs to output
        a structured plan, not do the actual work.
        """
        # Check memory for similar past tasks
        past_work = self.memory.semantic_search(
            query=user_request, project=self.project, limit=3
        )
        history_text = ""
        if past_work:
            history_text = "\n\nRelevant past work:\n" + "\n".join(
                f"- [{o.agent_id}] {o.title}: {o.narrative[:100]}" for o in past_work
            )

        response = self.client.messages.create(
            model=self.router_model,
            max_tokens=1000,
            system=[{
                "type": "text",
                "text": """You are a task router. Given a user request, decompose it into
a list of agent tasks. Available agent types: researcher, coder, reviewer, planner, summarizer.

Output a JSON array of tasks:
[
  {"agent_type": "researcher", "description": "...", "depends_on": []},
  {"agent_type": "coder", "description": "...", "depends_on": ["task_0"]},
  ...
]

Keep it minimal — use the fewest agents possible. Tasks with no depends_on run in parallel.""",
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": f"Request: {user_request}{history_text}",
            }],
        )

        # Parse the plan
        import json
        import re

        text = response.content[0].text
        json_match = re.search(r"\[[\s\S]*\]", text)
        if not json_match:
            # Fallback: single researcher agent
            return ExecutionPlan(tasks=[
                TaskNode(
                    task_id="task_0",
                    agent_type="researcher",
                    description=user_request,
                )
            ])

        raw_tasks = json.loads(json_match.group())
        tasks = []
        for i, rt in enumerate(raw_tasks):
            tasks.append(TaskNode(
                task_id=f"task_{i}",
                agent_type=rt.get("agent_type", "researcher"),
                description=rt.get("description", user_request),
                depends_on=[f"task_{d}" if isinstance(d, int) else d
                           for d in rt.get("depends_on", [])],
            ))

        return ExecutionPlan(tasks=tasks)

    async def execute_plan(self, plan: ExecutionPlan):
        """Execute the DAG with concurrency control and failure propagation."""
        semaphore = asyncio.Semaphore(self.max_concurrent)

        while any(t.status in ("pending", "running") for t in plan.tasks):
            ready = plan.get_ready_tasks()
            if not ready:
                # Check for deadlock: pending tasks with failed dependencies
                pending = [t for t in plan.tasks if t.status == "pending"]
                running = any(t.status == "running" for t in plan.tasks)
                if pending and not running:
                    failed_ids = {t.task_id for t in plan.tasks if t.status == "failed"}
                    for t in pending:
                        if any(d in failed_ids for d in t.depends_on):
                            t.status = "failed"
                            t.result = AgentResult(
                                agent_id="", agent_type=t.agent_type,
                                response_text=f"Skipped: dependency failed",
                                observations=[], metrics={}, elapsed_ms=0,
                            )
                    # Break if everything resolved
                    if not any(t.status in ("pending", "running") for t in plan.tasks):
                        break
                await asyncio.sleep(0.1)
                continue

            # Launch ready tasks with staggered starts to avoid rate limit bursts
            coros = [
                self._execute_task(task, plan, semaphore, stagger=i * 0.5)
                for i, task in enumerate(ready)
            ]
            await asyncio.gather(*coros)

    async def _execute_task(
        self, task: TaskNode, plan: ExecutionPlan, semaphore: asyncio.Semaphore,
        stagger: float = 0,
    ):
        """Execute a single task node with optional stagger delay."""
        if stagger > 0:
            await asyncio.sleep(stagger)
        async with semaphore:
            task.status = "running"

            config = get_agent_config(task.agent_type)
            agent = Agent(
                config=config,
                memory=self.memory,
                project=self.project,
                context_builder=self.context_builder,
                condenser=self.condenser,
            )

            # Build shared context from dependency results
            dep_context = ""
            for dep_id in task.depends_on:
                dep_task = next((t for t in plan.tasks if t.task_id == dep_id), None)
                if dep_task and dep_task.result:
                    dep_context += f"\n[{dep_task.agent_type} result]: {dep_task.result.response_text[:500]}\n"

            try:
                # Run in executor to not block the event loop
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: agent.execute(
                        task_description=task.description,
                        shared_context=dep_context,
                    ),
                )
                task.result = result
                task.status = "completed"
            except Exception as e:
                task.status = "failed"
                task.result = AgentResult(
                    agent_id=agent.agent_id,
                    agent_type=task.agent_type,
                    response_text=f"Error: {e}",
                    observations=[],
                    metrics={},
                    elapsed_ms=0,
                )

    def merge_results(self, plan: ExecutionPlan, original_request: str) -> str:
        """Synthesize all agent results into a final response."""
        completed = [t for t in plan.tasks if t.status == "completed" and t.result]

        if not completed:
            return "No agents completed successfully."

        if len(completed) == 1:
            return completed[0].result.response_text

        # For multi-agent results, use a summarizer to merge
        parts = []
        for task in completed:
            parts.append(
                f"## {task.agent_type} ({task.task_id})\n{task.result.response_text[:1000]}"
            )

        merge_prompt = f"""Original request: {original_request}

Agent results:
{"---".join(parts)}

Synthesize these results into a single coherent response."""

        config = get_agent_config("summarizer")
        summarizer = Agent(
            config=config,
            memory=self.memory,
            project=self.project,
        )
        result = summarizer.execute(merge_prompt)
        return result.response_text
