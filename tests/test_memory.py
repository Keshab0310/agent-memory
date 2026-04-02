"""Tests for the memory store, context builder, and condenser."""

import os
import tempfile
import pytest

from src.memory.store import MemoryStore, Observation, Summary
from src.memory.context_builder import ContextBuilder, ContextBudget, estimate_tokens
from src.memory.condenser import _parse_summary_xml


@pytest.fixture
def memory():
    """Create a temporary memory store for testing."""
    tmpdir = tempfile.mkdtemp()
    store = MemoryStore(
        sqlite_path=os.path.join(tmpdir, "test.db"),
        chroma_path=os.path.join(tmpdir, "chroma"),
    )
    yield store
    store.close()


class TestMemoryStore:
    def test_store_and_retrieve_observation(self, memory):
        obs = Observation(
            agent_id="researcher-abc123",
            project="test-project",
            session_id="sess-001",
            obs_type="discovery",
            title="Found API endpoint",
            narrative="The /users endpoint returns paginated results",
            facts=["Pagination uses cursor-based approach", "Max 100 per page"],
            concepts=["api", "pagination"],
        )
        obs_id = memory.store_observation(obs)
        assert obs_id > 0

        results = memory.get_recent_observations("test-project")
        assert len(results) == 1
        assert results[0].title == "Found API endpoint"
        assert results[0].facts == ["Pagination uses cursor-based approach", "Max 100 per page"]

    def test_agent_isolation(self, memory):
        """Observations from one agent should be filterable."""
        for agent in ["researcher-001", "coder-002"]:
            memory.store_observation(Observation(
                agent_id=agent,
                project="test-project",
                session_id="sess-001",
                title=f"Work by {agent}",
            ))

        all_obs = memory.get_recent_observations("test-project")
        assert len(all_obs) == 2

        researcher_obs = memory.get_recent_observations("test-project", agent_id="researcher-001")
        assert len(researcher_obs) == 1
        assert "researcher" in researcher_obs[0].agent_id

    def test_condensed_observations_excluded(self, memory):
        """Condensed observations should not appear in working memory."""
        obs = Observation(
            agent_id="test-agent",
            project="test-project",
            session_id="sess-001",
            title="Will be condensed",
        )
        obs_id = memory.store_observation(obs)

        memory.mark_observations_condensed([obs_id])

        results = memory.get_recent_observations("test-project", include_condensed=False)
        assert len(results) == 0

        results = memory.get_recent_observations("test-project", include_condensed=True)
        assert len(results) == 1

    def test_semantic_search(self, memory):
        """Vector search should find semantically relevant observations."""
        memory.store_observation(Observation(
            agent_id="agent-1",
            project="proj",
            session_id="s1",
            title="Database schema design",
            narrative="Designed the user table with email and password columns",
            concepts=["database", "schema"],
        ))
        memory.store_observation(Observation(
            agent_id="agent-2",
            project="proj",
            session_id="s2",
            title="API rate limiting",
            narrative="Implemented token bucket rate limiter for the REST API",
            concepts=["api", "security"],
        ))

        results = memory.semantic_search("database design", project="proj")
        assert len(results) >= 1
        assert results[0].title == "Database schema design"

    def test_token_economics(self, memory):
        """Should calculate compression ratio correctly."""
        memory.store_observation(Observation(
            agent_id="agent-1",
            project="proj",
            session_id="s1",
            title="Short title",
            narrative="A brief finding",
            discovery_tokens=5000,
        ))

        economics = memory.get_token_economics("proj")
        assert economics["total_observations"] == 1
        assert economics["discovery_tokens"] == 5000
        assert economics["read_tokens"] < 5000
        assert economics["savings_percent"] > 0


class TestContextBuilder:
    def test_budget_enforcement(self, memory):
        """Context should not exceed token budget."""
        # Store many observations
        for i in range(20):
            memory.store_observation(Observation(
                agent_id="agent-1",
                project="proj",
                session_id="s1",
                title=f"Observation {i}" * 10,
                narrative="x" * 500,
            ))

        builder = ContextBuilder(memory, ContextBudget(total=500))
        context = builder.build("proj", "agent-1", "Do something")

        tokens = estimate_tokens(context)
        assert tokens <= 550  # Allow small overflow from formatting

    def test_includes_task_description(self, memory):
        builder = ContextBuilder(memory)
        context = builder.build("proj", "agent-1", "Implement login feature")
        assert "Implement login feature" in context


class TestSummaryParser:
    def test_parse_valid_summary(self):
        xml = """<summary>
          <request>Build the auth system</request>
          <investigated>Looked at OAuth libraries</investigated>
          <learned>Passport.js is the best fit</learned>
          <completed>Set up basic auth routes</completed>
          <next_steps>Add password hashing</next_steps>
        </summary>"""
        result = _parse_summary_xml(xml)
        assert result is not None
        assert result["request"] == "Build the auth system"
        assert result["learned"] == "Passport.js is the best fit"

    def test_parse_missing_summary(self):
        result = _parse_summary_xml("No summary here")
        assert result is None


class TestMetricsLogging:
    def test_api_call_logging(self, memory):
        """Should log and retrieve API call metrics."""
        memory.log_api_call(
            agent_id="researcher-001",
            session_id="sess-001",
            model="claude-sonnet-4-6-20250514",
            input_tokens=1500,
            output_tokens=800,
            cache_creation_tokens=2000,
            cache_read_tokens=1800,
            latency_ms=3200,
            memory_injected=3,
            memory_created=1,
        )

        row = memory.db.execute("SELECT * FROM api_calls").fetchone()
        assert row["input_tokens"] == 1500
        assert row["cache_read_tokens"] == 1800
