---
name: memory-search
description: Search past work and observations across sessions
user-invocable: true
---

# Memory Search

Search your project's memory for past observations, discoveries, and changes.

## Instructions

When the user asks to search memory, recall past work, or find previous observations:

1. Use the `memory_search` MCP tool to find relevant observations
2. Present results with their type, title, and key facts
3. If the user asks about token savings, use `memory_stats` instead

## Examples

User: "What did we discover about the API yesterday?"
Action: Call `memory_search` with query="API" and the current project name

User: "How much have we saved on tokens?"
Action: Call `memory_stats` with the current project name
