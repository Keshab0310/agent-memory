"""
Session Start Hook — Injects recent memory context at session startup.

Queries MemoryStore for the project's recent observations and outputs
them as JSON with a 'context' field to stdout for Claude Code to inject.
"""

import json
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.memory.store import MemoryStore
from src.memory.context_builder import ContextBuilder


def main():
    try:
        project = os.environ.get("CLAUDE_PROJECT", Path.cwd().name)
        agent_id = os.environ.get("CLAUDE_AGENT_ID", "claude-code")

        data_dir = PROJECT_ROOT / "data"
        db_path = data_dir / "memory.db"

        # If no database exists yet, nothing to inject
        if not db_path.exists():
            print(json.dumps({"context": ""}))
            sys.exit(0)

        store = MemoryStore(
            sqlite_path=str(db_path),
            chroma_path=str(data_dir / "chroma"),
        )

        # Check if there are any observations for this project
        recent = store.get_recent_observations(project=project, limit=1)
        if not recent:
            store.close()
            print(json.dumps({"context": ""}))
            sys.exit(0)

        # Build context using ContextBuilder with a startup task description
        builder = ContextBuilder(memory=store)
        context = builder.build(
            project=project,
            agent_id=agent_id,
            task_description="Starting new session. Here is relevant context from previous work.",
        )

        # Also include token economics as a quick status line
        economics = store.get_token_economics(project)
        if economics["total_observations"] > 0:
            compression_ratio = (
                round(economics["discovery_tokens"] / economics["read_tokens"], 1)
                if economics["read_tokens"] > 0
                else 0
            )
            status = (
                f"\n\n## Memory Status\n"
                f"Observations: {economics['total_observations']} | "
                f"Compression: {compression_ratio}:1 | "
                f"Token savings: {economics['savings_percent']}%"
            )
            context += status

        store.close()

        # Output JSON with context field for Claude Code to inject
        print(json.dumps({"context": context}))
        sys.exit(0)

    except Exception:
        # On error, output empty context rather than breaking the session
        print(json.dumps({"context": ""}))
        sys.exit(0)


if __name__ == "__main__":
    main()
