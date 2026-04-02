"""Tests for the orchestrator router and execution plan."""

import pytest
from src.orchestrator.router import TaskNode, ExecutionPlan


class TestExecutionPlan:
    def test_ready_tasks_no_dependencies(self):
        plan = ExecutionPlan(tasks=[
            TaskNode(task_id="t0", agent_type="researcher", description="Research"),
            TaskNode(task_id="t1", agent_type="coder", description="Code"),
        ])
        ready = plan.get_ready_tasks()
        assert len(ready) == 2  # Both should be ready (no deps)

    def test_ready_tasks_with_dependencies(self):
        plan = ExecutionPlan(tasks=[
            TaskNode(task_id="t0", agent_type="researcher", description="Research"),
            TaskNode(task_id="t1", agent_type="coder", description="Code", depends_on=["t0"]),
            TaskNode(task_id="t2", agent_type="reviewer", description="Review", depends_on=["t1"]),
        ])

        # Initially only t0 is ready
        ready = plan.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "t0"

        # After t0 completes, t1 becomes ready
        plan.tasks[0].status = "completed"
        ready = plan.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "t1"

        # After t1 completes, t2 becomes ready
        plan.tasks[1].status = "completed"
        ready = plan.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "t2"

    def test_parallel_tasks_after_common_dependency(self):
        plan = ExecutionPlan(tasks=[
            TaskNode(task_id="t0", agent_type="researcher", description="Research"),
            TaskNode(task_id="t1", agent_type="coder", description="Code A", depends_on=["t0"]),
            TaskNode(task_id="t2", agent_type="coder", description="Code B", depends_on=["t0"]),
        ])

        plan.tasks[0].status = "completed"
        ready = plan.get_ready_tasks()
        assert len(ready) == 2  # Both t1 and t2 ready


class TestCachePayload:
    def test_cache_breakpoint_placement(self):
        from src.cache.prompt_cache import build_cached_messages

        payload = build_cached_messages(
            system_prompt="You are a test agent.",
            shared_context="Project context here.",
            episodic_context="Recent work here.",
            user_message="Do something.",
        )

        # System should have cache_control
        assert payload["system"][0]["cache_control"]["type"] == "ephemeral"

        # Shared context message should have cache_control
        shared_msg = payload["messages"][0]
        assert shared_msg["content"][0]["cache_control"]["type"] == "ephemeral"

        # User message (episodic + task) should NOT have cache_control
        user_msg = payload["messages"][-1]
        for part in user_msg["content"]:
            assert "cache_control" not in part
