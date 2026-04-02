"""
Post Tool Use Hook — Compresses tool call results into memory observations.

Reads JSON from stdin with keys: tool_name, tool_input, tool_output.
Stores a compressed observation via MemoryStore.
Writes nothing to stdout. Exits 0 on success, 1 on error.
"""

import json
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.memory.store import MemoryStore, Observation

# Tool calls that are too noisy or low-value to record
SKIP_TOOLS = {
    "memory_search",
    "memory_store",
    "memory_stats",
    "memory_context",
}

# Estimate tokens from a string (4 chars per token)
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def classify_tool_type(tool_name: str) -> str:
    """Map tool names to observation types."""
    read_tools = {"Read", "Glob", "Grep", "Bash", "WebFetch", "WebSearch"}
    write_tools = {"Edit", "Write", "NotebookEdit"}

    if tool_name in read_tools:
        return "discovery"
    elif tool_name in write_tools:
        return "change"
    else:
        return "discovery"


def compress_tool_output(tool_name: str, tool_input: dict, tool_output: str) -> dict:
    """Compress a tool call into an observation-shaped dict.

    Keeps: what was done, key findings, files involved.
    Drops: full file contents, verbose output.
    """
    title = f"{tool_name} call"
    narrative = ""
    facts = []
    files_read = []
    files_modified = []

    if tool_name == "Read":
        file_path = tool_input.get("file_path", "unknown")
        files_read.append(file_path)
        title = f"Read {Path(file_path).name}"
        # Count lines instead of storing content
        line_count = tool_output.count("\n") + 1 if tool_output else 0
        narrative = f"Read {file_path} ({line_count} lines)"

    elif tool_name in ("Edit", "Write"):
        file_path = tool_input.get("file_path", "unknown")
        files_modified.append(file_path)
        title = f"{'Edited' if tool_name == 'Edit' else 'Wrote'} {Path(file_path).name}"
        if tool_name == "Edit":
            old = tool_input.get("old_string", "")
            new = tool_input.get("new_string", "")
            narrative = f"Modified {file_path}: replaced {len(old)} chars with {len(new)} chars"
        else:
            content = tool_input.get("content", "")
            narrative = f"Wrote {file_path} ({len(content)} chars)"

    elif tool_name == "Grep":
        pattern = tool_input.get("pattern", "")
        path = tool_input.get("path", ".")
        match_count = tool_output.count("\n") if tool_output else 0
        title = f"Searched for '{pattern[:50]}'"
        narrative = f"Grep for '{pattern}' in {path}: {match_count} matches"
        facts.append(f"Pattern: {pattern}")
        if match_count > 0:
            # Store first few matching file paths
            lines = tool_output.strip().split("\n")[:5]
            for line in lines:
                facts.append(f"Match: {line[:120]}")

    elif tool_name == "Glob":
        pattern = tool_input.get("pattern", "")
        match_count = tool_output.count("\n") if tool_output else 0
        title = f"Glob '{pattern}'"
        narrative = f"Found {match_count} files matching '{pattern}'"

    elif tool_name == "Bash":
        command = tool_input.get("command", "")
        title = f"Ran: {command[:60]}"
        # Truncate output for narrative
        out_preview = (tool_output[:200] + "...") if len(tool_output) > 200 else tool_output
        narrative = f"Command: {command}\nOutput: {out_preview}"

    else:
        # Generic compression for unknown tools
        title = f"{tool_name} call"
        input_summary = json.dumps(tool_input)[:150]
        output_summary = (tool_output[:200] + "...") if len(tool_output) > 200 else tool_output
        narrative = f"Input: {input_summary}\nOutput: {output_summary}"

    # Calculate discovery tokens (how many tokens the raw output would cost)
    raw_text = json.dumps(tool_input) + (tool_output or "")
    discovery_tokens = estimate_tokens(raw_text)

    return {
        "title": title,
        "narrative": narrative,
        "facts": facts,
        "obs_type": classify_tool_type(tool_name),
        "files_read": files_read,
        "files_modified": files_modified,
        "discovery_tokens": discovery_tokens,
    }


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)

        data = json.loads(raw)
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})
        tool_output = data.get("tool_output", "")

        # Skip our own memory tools to avoid recursion
        if tool_name in SKIP_TOOLS:
            sys.exit(0)

        compressed = compress_tool_output(tool_name, tool_input, tool_output)

        # Determine project from environment or working directory
        project = os.environ.get("CLAUDE_PROJECT", Path.cwd().name)
        agent_id = os.environ.get("CLAUDE_AGENT_ID", "claude-code")
        session_id = os.environ.get("CLAUDE_SESSION_ID", "hook-session")

        data_dir = PROJECT_ROOT / "data"
        store = MemoryStore(
            sqlite_path=str(data_dir / "memory.db"),
            chroma_path=str(data_dir / "chroma"),
        )

        obs = Observation(
            agent_id=agent_id,
            project=project,
            session_id=session_id,
            obs_type=compressed["obs_type"],
            title=compressed["title"],
            narrative=compressed["narrative"],
            facts=compressed["facts"],
            files_read=compressed["files_read"],
            files_modified=compressed["files_modified"],
            discovery_tokens=compressed["discovery_tokens"],
        )

        store.store_observation(obs)
        store.close()

        sys.exit(0)

    except Exception:
        # Hooks must not break the user's workflow
        sys.exit(1)


if __name__ == "__main__":
    main()
