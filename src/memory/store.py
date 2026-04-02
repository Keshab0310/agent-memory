"""
Memory Store — SQLite + ChromaDB dual-layer memory system.

Adapted from claude-mem's SessionStore + ChromaSync pattern:
- SQLite for structured metadata queries (type, concept, agent, time)
- ChromaDB for semantic vector search (optional — falls back to FTS5)
- Shared memory bus for cross-agent observation access

Thread-safety: Uses WAL mode + per-thread connections + write lock.
"""

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ChromaDB is optional — fall back to SQLite FTS5 if unavailable
try:
    import chromadb
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False


@dataclass
class Observation:
    """Compressed observation — the core unit of agent memory."""
    id: Optional[int] = None
    agent_id: str = ""
    project: str = ""
    session_id: str = ""
    obs_type: str = "discovery"  # discovery, bugfix, feature, refactor, change, decision
    title: str = ""
    subtitle: str = ""
    facts: list[str] = field(default_factory=list)
    narrative: str = ""
    concepts: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    discovery_tokens: int = 0  # Tokens consumed to produce this observation
    created_at_epoch: float = 0.0
    condensed: bool = False  # True if this observation has been rolled into a summary


@dataclass
class Summary:
    """Session summary — produced by the condensation pipeline."""
    id: Optional[int] = None
    agent_id: str = ""
    project: str = ""
    session_id: str = ""
    request: str = ""
    investigated: str = ""
    learned: str = ""
    completed: str = ""
    next_steps: str = ""
    observation_count: int = 0  # How many observations this summary covers
    created_at_epoch: float = 0.0


class MemoryStore:
    """Dual-layer memory: SQLite (structured) + optional ChromaDB (semantic).

    Thread-safety guarantees:
    - SQLite uses WAL mode for concurrent reads + serialized writes
    - Per-thread connections via threading.local()
    - Write lock serializes all mutations (SQLite + ChromaDB)
    - ChromaDB falls back to SQLite FTS5 if unavailable
    """

    def __init__(self, sqlite_path: str = "./data/memory.db", chroma_path: Optional[str] = "./data/chroma"):
        self.sqlite_path = sqlite_path
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)

        # Thread-safe connection management
        self._local = threading.local()
        self._write_lock = threading.Lock()

        # Initialize schema on main thread
        self._init_schema()

        # ChromaDB for vector search (optional)
        self.collection = None
        if HAS_CHROMADB and chroma_path:
            try:
                self.chroma_client = chromadb.PersistentClient(path=chroma_path)
                self.collection = self.chroma_client.get_or_create_collection(
                    name="observations",
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception as e:
                logger.warning(f"ChromaDB unavailable, using FTS5 fallback: {e}")
                self.collection = None

    @property
    def db(self) -> sqlite3.Connection:
        """Thread-local SQLite connection with WAL mode."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                project TEXT NOT NULL,
                session_id TEXT NOT NULL,
                obs_type TEXT NOT NULL DEFAULT 'discovery',
                title TEXT,
                subtitle TEXT,
                facts TEXT,  -- JSON array
                narrative TEXT,
                concepts TEXT,  -- JSON array
                files_read TEXT,  -- JSON array
                files_modified TEXT,  -- JSON array
                discovery_tokens INTEGER DEFAULT 0,
                condensed INTEGER DEFAULT 0,
                created_at_epoch REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_obs_project ON observations(project);
            CREATE INDEX IF NOT EXISTS idx_obs_agent ON observations(agent_id);
            CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id);
            CREATE INDEX IF NOT EXISTS idx_obs_epoch ON observations(created_at_epoch DESC);
            CREATE INDEX IF NOT EXISTS idx_obs_type ON observations(obs_type);

            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                project TEXT NOT NULL,
                session_id TEXT NOT NULL,
                request TEXT,
                investigated TEXT,
                learned TEXT,
                completed TEXT,
                next_steps TEXT,
                observation_count INTEGER DEFAULT 0,
                created_at_epoch REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sum_project ON summaries(project);
            CREATE INDEX IF NOT EXISTS idx_sum_agent ON summaries(agent_id);

            CREATE TABLE IF NOT EXISTS api_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                agent_type TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                latency_ms INTEGER DEFAULT 0,
                memory_injected INTEGER DEFAULT 0,
                memory_created INTEGER DEFAULT 0,
                created_at_epoch REAL NOT NULL
            );
        """)

        # FTS5 full-text search — works without ChromaDB
        try:
            self.db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
                    title, narrative, facts, concepts,
                    content=observations, content_rowid=id
                )
            """)
        except Exception:
            pass  # FTS5 may not be available on all SQLite builds

        self.db.commit()

    # ── Observation CRUD ──

    def store_observation(self, obs: Observation) -> int:
        """Store observation in SQLite + sync to ChromaDB. Thread-safe."""
        if obs.created_at_epoch == 0:
            obs.created_at_epoch = time.time()

        with self._write_lock:
            cursor = self.db.execute(
                """INSERT INTO observations
                   (agent_id, project, session_id, obs_type, title, subtitle,
                    facts, narrative, concepts, files_read, files_modified,
                    discovery_tokens, condensed, created_at_epoch)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    obs.agent_id, obs.project, obs.session_id, obs.obs_type,
                    obs.title, obs.subtitle,
                    json.dumps(obs.facts), obs.narrative, json.dumps(obs.concepts),
                    json.dumps(obs.files_read), json.dumps(obs.files_modified),
                    obs.discovery_tokens, int(obs.condensed), obs.created_at_epoch,
                ),
            )
            self.db.commit()
            obs_id = cursor.lastrowid

            # Sync FTS5 index
            try:
                self.db.execute(
                    "INSERT INTO observations_fts(rowid, title, narrative, facts, concepts) VALUES (?, ?, ?, ?, ?)",
                    (obs_id, obs.title, obs.narrative, json.dumps(obs.facts), json.dumps(obs.concepts)),
                )
                self.db.commit()
            except Exception:
                pass  # FTS5 not available

            # Sync to ChromaDB (if available)
            if self.collection is not None:
                try:
                    docs, ids, metadatas = [], [], []
                    base_meta = {
                        "sqlite_id": obs_id,
                        "agent_id": obs.agent_id,
                        "project": obs.project,
                        "obs_type": obs.obs_type,
                        "created_at_epoch": obs.created_at_epoch,
                    }

                    if obs.narrative:
                        docs.append(obs.narrative)
                        ids.append(f"obs_{obs_id}_narrative")
                        metadatas.append({**base_meta, "field": "narrative"})

                    for i, fact in enumerate(obs.facts):
                        docs.append(fact)
                        ids.append(f"obs_{obs_id}_fact_{i}")
                        metadatas.append({**base_meta, "field": "fact"})

                    if docs:
                        self.collection.add(documents=docs, ids=ids, metadatas=metadatas)
                except Exception as e:
                    logger.warning(f"ChromaDB sync failed (observation still in SQLite): {e}")

        return obs_id

    def get_recent_observations(
        self,
        project: str,
        agent_id: Optional[str] = None,
        limit: int = 10,
        include_condensed: bool = False,
    ) -> list[Observation]:
        """Get recent observations, optionally filtered by agent."""
        query = "SELECT * FROM observations WHERE project = ?"
        params: list = [project]

        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)

        if not include_condensed:
            query += " AND condensed = 0"

        query += " ORDER BY created_at_epoch DESC LIMIT ?"
        params.append(limit)

        rows = self.db.execute(query, params).fetchall()
        return [self._row_to_observation(r) for r in rows]

    def semantic_search(
        self,
        query: str,
        project: str,
        limit: int = 5,
        exclude_agent: Optional[str] = None,
    ) -> list[Observation]:
        """Hybrid search: ChromaDB semantic ranking → SQLite hydration.
        Falls back to FTS5 if ChromaDB is unavailable."""

        # Try ChromaDB first
        if self.collection is not None:
            try:
                return self._chroma_search(query, project, limit, exclude_agent)
            except Exception as e:
                logger.warning(f"ChromaDB search failed, falling back to FTS5: {e}")

        # Fallback: SQLite FTS5
        return self._fts5_search(query, project, limit, exclude_agent)

    def _chroma_search(
        self, query: str, project: str, limit: int, exclude_agent: Optional[str]
    ) -> list[Observation]:
        """ChromaDB vector search with SQLite hydration."""
        if exclude_agent:
            where_filter = {
                "$and": [
                    {"project": project},
                    {"agent_id": {"$ne": exclude_agent}},
                ]
            }
        else:
            where_filter = {"project": project}

        results = self.collection.query(
            query_texts=[query],
            n_results=limit * 3,
            where=where_filter,
            include=["metadatas", "distances"],
        )

        seen = set()
        sqlite_ids = []
        for meta in (results["metadatas"][0] if results["metadatas"] else []):
            sid = meta.get("sqlite_id")
            if sid and sid not in seen:
                seen.add(sid)
                sqlite_ids.append(sid)
                if len(sqlite_ids) >= limit:
                    break

        if not sqlite_ids:
            return []

        placeholders = ",".join("?" * len(sqlite_ids))
        rows = self.db.execute(
            f"SELECT * FROM observations WHERE id IN ({placeholders})", sqlite_ids
        ).fetchall()

        row_map = {r["id"]: r for r in rows}
        return [self._row_to_observation(row_map[sid]) for sid in sqlite_ids if sid in row_map]

    def _fts5_search(
        self, query: str, project: str, limit: int, exclude_agent: Optional[str]
    ) -> list[Observation]:
        """SQLite FTS5 full-text search fallback."""
        try:
            sql = """
                SELECT o.* FROM observations o
                JOIN observations_fts fts ON o.id = fts.rowid
                WHERE observations_fts MATCH ? AND o.project = ?
            """
            params: list = [query, project]
            if exclude_agent:
                sql += " AND o.agent_id != ?"
                params.append(exclude_agent)
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)

            rows = self.db.execute(sql, params).fetchall()
            return [self._row_to_observation(r) for r in rows]
        except Exception:
            # FTS5 not available — fall back to LIKE search
            sql = """
                SELECT * FROM observations
                WHERE project = ? AND (title LIKE ? OR narrative LIKE ?)
            """
            pattern = f"%{query}%"
            params = [project, pattern, pattern]
            if exclude_agent:
                sql += " AND agent_id != ?"
                params.append(exclude_agent)
            sql += " ORDER BY created_at_epoch DESC LIMIT ?"
            params.append(limit)

            rows = self.db.execute(sql, params).fetchall()
            return [self._row_to_observation(r) for r in rows]

    # ── Summary CRUD ──

    def store_summary(self, summary: Summary) -> int:
        if summary.created_at_epoch == 0:
            summary.created_at_epoch = time.time()

        with self._write_lock:
            cursor = self.db.execute(
                """INSERT INTO summaries
                   (agent_id, project, session_id, request, investigated, learned,
                    completed, next_steps, observation_count, created_at_epoch)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    summary.agent_id, summary.project, summary.session_id,
                    summary.request, summary.investigated, summary.learned,
                    summary.completed, summary.next_steps, summary.observation_count,
                    summary.created_at_epoch,
                ),
            )
            self.db.commit()
            return cursor.lastrowid

    def mark_observations_condensed(self, observation_ids: list[int]):
        """Mark observations as condensed after summarization."""
        if not observation_ids:
            return
        with self._write_lock:
            placeholders = ",".join("?" * len(observation_ids))
            self.db.execute(
                f"UPDATE observations SET condensed = 1 WHERE id IN ({placeholders})",
                observation_ids,
            )
            self.db.commit()

    # ── Metrics ──

    def log_api_call(self, agent_id: str, session_id: str, model: str,
                     input_tokens: int, output_tokens: int,
                     cache_creation_tokens: int, cache_read_tokens: int,
                     latency_ms: int, memory_injected: int, memory_created: int):
        # Extract agent_type from agent_id (e.g., "researcher-abc123" → "researcher")
        agent_type = agent_id.rsplit("-", 1)[0] if "-" in agent_id else agent_id

        with self._write_lock:
            self.db.execute(
                """INSERT INTO api_calls
                   (agent_id, agent_type, session_id, model, input_tokens, output_tokens,
                    cache_creation_tokens, cache_read_tokens, latency_ms,
                    memory_injected, memory_created, created_at_epoch)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (agent_id, agent_type, session_id, model, input_tokens, output_tokens,
                 cache_creation_tokens, cache_read_tokens, latency_ms,
                 memory_injected, memory_created, time.time()),
            )
            self.db.commit()

    # ── Token Economics ──

    def get_token_economics(self, project: str) -> dict:
        """Calculate compression ratio like claude-mem's TokenCalculator."""
        row = self.db.execute(
            """SELECT
                COUNT(*) as total_observations,
                COALESCE(SUM(discovery_tokens), 0) as total_discovery_tokens,
                COALESCE(SUM(LENGTH(COALESCE(title,'')) + LENGTH(COALESCE(narrative,''))
                             + LENGTH(COALESCE(facts,''))), 0) as total_read_chars
               FROM observations WHERE project = ?""",
            (project,),
        ).fetchone()

        read_tokens = row["total_read_chars"] // 4
        discovery = row["total_discovery_tokens"]
        savings = discovery - read_tokens
        pct = round((savings / discovery) * 100) if discovery > 0 else 0

        return {
            "total_observations": row["total_observations"],
            "read_tokens": read_tokens,
            "discovery_tokens": discovery,
            "savings": savings,
            "savings_percent": pct,
        }

    # ── Helpers ──

    def _row_to_observation(self, row) -> Observation:
        return Observation(
            id=row["id"],
            agent_id=row["agent_id"],
            project=row["project"],
            session_id=row["session_id"],
            obs_type=row["obs_type"],
            title=row["title"] or "",
            subtitle=row["subtitle"] or "",
            facts=json.loads(row["facts"]) if row["facts"] else [],
            narrative=row["narrative"] or "",
            concepts=json.loads(row["concepts"]) if row["concepts"] else [],
            files_read=json.loads(row["files_read"]) if row["files_read"] else [],
            files_modified=json.loads(row["files_modified"]) if row["files_modified"] else [],
            discovery_tokens=row["discovery_tokens"],
            condensed=bool(row["condensed"]),
            created_at_epoch=row["created_at_epoch"],
        )

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
