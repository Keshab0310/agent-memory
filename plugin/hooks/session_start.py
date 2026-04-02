#!/usr/bin/env python3
"""
Session Start Hook — Injects recent memory context at session startup.

Queries MemoryStore for the project's recent observations and outputs
them as JSON with a 'context' field to stdout for Claude Code to inject.
"""

import json
import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parent.parent))
REPO_ROOT = PLUGIN_ROOT.parent
for p in [str(PLUGIN_ROOT), str(REPO_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from src.memory.store import MemoryStore
from src.memory.context_builder import ContextBuilder

DATA_DIR = Path(os.environ.get("AGENT_MEMORY_DATA", os.environ.get("CLAUDE_PLUGIN_DATA", PLUGIN_ROOT / "data")))


def main():
    try:
        project = os.environ.get("CLAUDE_PROJECT", Path.cwd().name)
        agent_id = os.environ.get("CLAUDE_AGENT_ID", "claude-code")

        db_path = DATA_DIR / "memory.db"

        if not db_path.exists():
            print(json.dumps({"context": ""}))
            sys.exit(0)

        store = MemoryStore(
            sqlite_path=str(db_path),
            chroma_path=None,  # FTS5 only for fast startup
        )

        recent = store.get_recent_observations(project=project, limit=1)
        if not recent:
            store.close()
            print(json.dumps({"context": ""}))
            sys.exit(0)

        # Auto-detect model profile from environment (ANTHROPIC_MODEL, CLAUDE_MODEL, etc.)
        from src.profiles import detect_profile
        profile = detect_profile()
        builder = ContextBuilder.from_profile(store, profile)
        context = builder.build(
            project=project,
            agent_id=agent_id,
            task_description="Starting new session. Here is relevant context from previous work.",
        )

        economics = store.get_token_economics(project)
        if economics["total_observations"] > 0:
            compression_ratio = (
                round(economics["discovery_tokens"] / economics["read_tokens"], 1)
                if economics["read_tokens"] > 0
                else 0
            )
            context += (
                f"\n\n## Memory Status\n"
                f"Observations: {economics['total_observations']} | "
                f"Compression: {compression_ratio}:1 | "
                f"Token savings: {economics['savings_percent']}%"
            )

        store.close()
        print(json.dumps({"context": context}))
        sys.exit(0)

    except Exception:
        print(json.dumps({"context": ""}))
        sys.exit(0)


if __name__ == "__main__":
    main()
