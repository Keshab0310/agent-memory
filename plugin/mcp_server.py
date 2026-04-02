"""
MCP Server — Model Context Protocol server for the multi-agent memory system.

Exposes memory search, store, stats, and context tools over stdio transport.
Designed for use as a Claude Code MCP plugin.
"""

import json
import os
import sys
from pathlib import Path

# CLAUDE_PLUGIN_ROOT points to plugin/ dir. src/ is one level above (repo root).
PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parent))
REPO_ROOT = PLUGIN_ROOT.parent
for p in [str(PLUGIN_ROOT), str(REPO_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from src.memory.store import MemoryStore, Observation
from src.memory.context_builder import ContextBuilder

# Shared memory store instance
_store: MemoryStore | None = None


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        data_dir = Path(os.environ.get("AGENT_MEMORY_DATA", os.environ.get("CLAUDE_PLUGIN_DATA", REPO_ROOT / "data")))
        data_dir.mkdir(parents=True, exist_ok=True)
        _store = MemoryStore(
            sqlite_path=str(data_dir / "memory.db"),
            chroma_path=None,  # FTS5 only for plugin — no ChromaDB dependency
        )
    return _store


app = Server("agent-memory")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="memory_search",
            description="Semantic/FTS search across observations in the memory store.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query text"},
                    "project": {"type": "string", "description": "Project identifier"},
                    "limit": {"type": "integer", "description": "Max results to return", "default": 5},
                },
                "required": ["query", "project"],
            },
        ),
        Tool(
            name="memory_store",
            description="Store a compressed observation into the memory system.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Observation title"},
                    "narrative": {"type": "string", "description": "Brief narrative of what happened"},
                    "facts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of factual findings",
                    },
                    "obs_type": {
                        "type": "string",
                        "description": "Type: discovery, bugfix, feature, refactor, change, decision",
                        "default": "discovery",
                    },
                    "project": {"type": "string", "description": "Project identifier"},
                },
                "required": ["title", "narrative", "facts", "project"],
            },
        ),
        Tool(
            name="memory_stats",
            description="Return token economics dashboard: compression ratio, savings %, observation count.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project identifier"},
                },
                "required": ["project"],
            },
        ),
        Tool(
            name="memory_context",
            description="Build and return injected context within token budget for an agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project identifier"},
                    "agent_id": {"type": "string", "description": "Agent identifier"},
                    "task": {"type": "string", "description": "Current task description"},
                },
                "required": ["project", "agent_id", "task"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    store = get_store()

    if name == "memory_search":
        query = arguments["query"]
        project = arguments["project"]
        limit = arguments.get("limit", 5)

        results = store.semantic_search(query=query, project=project, limit=limit)
        output = []
        for obs in results:
            output.append({
                "id": obs.id,
                "type": obs.obs_type,
                "title": obs.title,
                "narrative": obs.narrative,
                "facts": obs.facts,
                "agent_id": obs.agent_id,
            })
        return [TextContent(type="text", text=json.dumps(output, indent=2))]

    elif name == "memory_store":
        obs = Observation(
            agent_id=arguments.get("agent_id", "claude-code"),
            project=arguments["project"],
            session_id=arguments.get("session_id", "mcp-session"),
            obs_type=arguments.get("obs_type", "discovery"),
            title=arguments["title"],
            narrative=arguments["narrative"],
            facts=arguments.get("facts", []),
        )
        obs_id = store.store_observation(obs)
        return [TextContent(type="text", text=json.dumps({"stored": True, "id": obs_id}))]

    elif name == "memory_stats":
        project = arguments["project"]
        economics = store.get_token_economics(project)
        compression_ratio = (
            round(economics["discovery_tokens"] / economics["read_tokens"], 1)
            if economics["read_tokens"] > 0
            else 0
        )
        result = {
            "total_observations": economics["total_observations"],
            "discovery_tokens": economics["discovery_tokens"],
            "read_tokens": economics["read_tokens"],
            "compression_ratio": f"{compression_ratio}:1",
            "savings_tokens": economics["savings"],
            "savings_percent": f"{economics['savings_percent']}%",
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "memory_context":
        project = arguments["project"]
        agent_id = arguments["agent_id"]
        task = arguments["task"]

        builder = ContextBuilder(memory=store)
        context = builder.build(project=project, agent_id=agent_id, task_description=task)
        report = builder.get_token_report(context)

        result = {
            "context": context,
            "tokens_used": report["context_tokens"],
            "budget_total": report["budget_total"],
            "utilization_pct": report["utilization_pct"],
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    else:
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
