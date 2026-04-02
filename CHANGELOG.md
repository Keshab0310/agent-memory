# Changelog

## [0.1.0-alpha] - 2026-04-01

### Added
- Core memory SDK: SQLite + FTS5 + optional ChromaDB dual-layer storage
- Token-budgeted context injection (8000 token default, configurable)
- Observation compression pipeline (66-94% savings measured)
- Prompt caching wrapper with 3-tier cache breakpoints
- Token bucket rate limiter (RPM + TPM)
- Periodic condensation pipeline with graceful error handling
- Thread-safe memory store (WAL mode, per-thread connections, write lock)
- Multi-agent shared memory bus with cross-agent semantic search
- DAG-based orchestrator with concurrent execution and failure propagation
- Local LLM support (Ollama, LM Studio via OpenAI-compatible API)
- Metrics tracker with token economics dashboard
- Claude Code plugin infrastructure (MCP server + hooks)
- Dry-run validation suite (no API key needed)
- 14 unit tests covering memory, orchestrator, cache, and metrics
- Full documentation: README, API reference, architecture doc
