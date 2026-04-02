"""
Metrics Tracker — Token consumption, latency, and memory accuracy tracking.

Provides the observability layer for the multi-agent system.
All metrics are stored in SQLite via MemoryStore.log_api_call().
This module provides aggregation and reporting.
"""

from dataclasses import dataclass
from typing import Optional

from ..memory.store import MemoryStore


@dataclass
class AgentMetricsSummary:
    agent_type: str
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_cache_creation: int
    total_cache_read: int
    avg_latency_ms: float
    cache_hit_rate: float
    effective_cost_ratio: float  # vs naive (no caching)


@dataclass
class SystemMetricsSummary:
    total_api_calls: int
    total_tokens: int
    total_cached_tokens: int
    overall_cache_hit_rate: float
    overall_savings_pct: float
    total_observations: int
    compression_ratio: float  # discovery_tokens / read_tokens
    avg_latency_ms: float


class MetricsTracker:
    """Aggregates and reports metrics from the MemoryStore."""

    def __init__(self, memory: MemoryStore):
        self.memory = memory

    def get_agent_summary(self, agent_type: Optional[str] = None) -> list[AgentMetricsSummary]:
        """Get metrics summary grouped by agent type."""
        query = """
            SELECT
                COALESCE(NULLIF(agent_type, ''), SUBSTR(agent_id, 1, MAX(INSTR(agent_id, '-') - 1, 0))) as agent_type,
                COUNT(*) as total_calls,
                SUM(input_tokens) as total_input,
                SUM(output_tokens) as total_output,
                SUM(cache_creation_tokens) as total_cache_creation,
                SUM(cache_read_tokens) as total_cache_read,
                AVG(latency_ms) as avg_latency
            FROM api_calls
        """
        params = []
        if agent_type:
            query += " WHERE agent_id LIKE ?"
            params.append(f"{agent_type}-%")

        query += " GROUP BY agent_type"

        rows = self.memory.db.execute(query, params).fetchall()
        results = []

        for row in rows:
            total_input = row["total_input"] + row["total_cache_creation"] + row["total_cache_read"]
            cache_hit = row["total_cache_read"] / total_input if total_input > 0 else 0

            # Effective cost: normal=1x, cache_write=1.25x, cache_read=0.1x
            effective = (
                row["total_input"] * 1.0
                + row["total_cache_creation"] * 1.25
                + row["total_cache_read"] * 0.1
                + row["total_output"] * 1.0
            )
            naive = total_input + row["total_output"]
            ratio = effective / naive if naive > 0 else 1.0

            results.append(AgentMetricsSummary(
                agent_type=row["agent_type"] or "unknown",
                total_calls=row["total_calls"],
                total_input_tokens=row["total_input"],
                total_output_tokens=row["total_output"],
                total_cache_creation=row["total_cache_creation"],
                total_cache_read=row["total_cache_read"],
                avg_latency_ms=round(row["avg_latency"], 1),
                cache_hit_rate=round(cache_hit, 3),
                effective_cost_ratio=round(ratio, 3),
            ))

        return results

    def get_system_summary(self, project: str) -> SystemMetricsSummary:
        """Get overall system metrics."""
        row = self.memory.db.execute("""
            SELECT
                COUNT(*) as total_calls,
                COALESCE(SUM(input_tokens + cache_creation_tokens + cache_read_tokens), 0) as total_tokens,
                COALESCE(SUM(cache_read_tokens), 0) as total_cached,
                AVG(latency_ms) as avg_latency
            FROM api_calls
        """).fetchone()

        total_tokens = row["total_tokens"]
        total_cached = row["total_cached"]
        cache_hit = total_cached / total_tokens if total_tokens > 0 else 0

        # Token economics from observation compression
        economics = self.memory.get_token_economics(project)
        compression = (
            economics["discovery_tokens"] / economics["read_tokens"]
            if economics["read_tokens"] > 0
            else 0
        )

        savings_pct = economics["savings_percent"]

        return SystemMetricsSummary(
            total_api_calls=row["total_calls"],
            total_tokens=total_tokens,
            total_cached_tokens=total_cached,
            overall_cache_hit_rate=round(cache_hit, 3),
            overall_savings_pct=savings_pct,
            total_observations=economics["total_observations"],
            compression_ratio=round(compression, 1),
            avg_latency_ms=round(row["avg_latency"] or 0, 1),
        )

    def print_dashboard(self, project: str):
        """Print a human-readable metrics dashboard."""
        system = self.get_system_summary(project)
        agents = self.get_agent_summary()

        print("=" * 60)
        print("MULTI-AGENT SYSTEM METRICS")
        print("=" * 60)
        print(f"Total API calls:      {system.total_api_calls}")
        print(f"Total tokens:         {system.total_tokens:,}")
        print(f"Cached tokens:        {system.total_cached_tokens:,}")
        print(f"Cache hit rate:       {system.overall_cache_hit_rate:.1%}")
        print(f"Compression ratio:    {system.compression_ratio:.1f}:1")
        print(f"Token savings:        {system.overall_savings_pct}%")
        print(f"Observations stored:  {system.total_observations}")
        print(f"Avg latency:          {system.avg_latency_ms:.0f}ms")
        print()

        if agents:
            print("Per-Agent Breakdown:")
            print("-" * 60)
            print(f"{'Type':<15} {'Calls':<8} {'Cache%':<8} {'Cost Ratio':<12} {'Latency':<10}")
            for a in agents:
                print(
                    f"{a.agent_type:<15} {a.total_calls:<8} "
                    f"{a.cache_hit_rate:.1%}   {a.effective_cost_ratio:.2f}x       "
                    f"{a.avg_latency_ms:.0f}ms"
                )
        print("=" * 60)
