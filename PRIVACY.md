# Privacy Policy

**agent-memory** v0.1.0

Last updated: April 1, 2026

---

## Summary

agent-memory stores all data locally on your machine. No data is sent to external servers. No telemetry is collected. No personal information is gathered.

## Data Storage

- All observations, summaries, and metrics are stored in a local SQLite database on your filesystem
- The default location is `./data/memory.db` relative to your project, or the path specified by `CLAUDE_PLUGIN_DATA`
- Optional ChromaDB vector embeddings are stored locally at `./data/chroma/`
- No data is replicated, synced, or transmitted to any remote server

## Data Collected

- **Tool call metadata**: Tool name, input parameters, and compressed output summaries (stored locally only)
- **Token usage metrics**: Input/output token counts, cache hit rates, latency (stored locally only)
- **Project identifiers**: Derived from your working directory name (stored locally only)

## Data NOT Collected

- No personal information (name, email, IP address)
- No API keys or credentials
- No telemetry or usage analytics
- No crash reports sent externally
- No cookies or tracking identifiers

## Third-Party Services

- **Anthropic API**: When using Claude models, your prompts are sent to Anthropic's API per their own [privacy policy](https://www.anthropic.com/privacy). agent-memory does not add any additional data to these API calls beyond what you configure.
- **Ollama / Local LLMs**: When using local models, all data stays entirely on your machine with zero external network calls.

## Data Deletion

Delete all agent-memory data by removing the `data/` directory in your project:

```bash
rm -rf ./data/
```

Or delete the SQLite database directly:

```bash
rm ./data/memory.db
```

## Contact

For privacy questions, open an issue at: https://github.com/Keshab0310/agent-memory/issues
