"""Dual-collection knowledge database for multi-source conversation indexing.

This module provides search across two distinct data sources (JSON and LevelDB)
using a hybrid approach:
  - SQLite FTS5 for fast full-text search across all collections (primary)
  - ChromaDB vector search for semantic search on small collections (secondary)

The ChromaDB worker subprocess isolates Rust binding crashes from the API server.
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# FTS5 Search Engine — primary search path, no ChromaDB dependency
# ---------------------------------------------------------------------------

class FTSSearchEngine:
    """Full-text search over ChromaDB's SQLite database using FTS5.

    ChromaDB stores documents in SQLite with an FTS5 index. We query it
    directly for fast, reliable text search without touching the HNSW
    index (which segfaults on large collections).
    """

    _SEARCH_SQL = """
        SELECT
            e.id          AS int_id,
            e.embedding_id AS uuid_id,
            fts_content.c0 AS document,
            e.segment_id,
            rank
        FROM embedding_fulltext_search fts
        JOIN embedding_fulltext_search_content fts_content
            ON fts_content.id = fts.rowid
        JOIN embeddings e
            ON e.id = fts.rowid
        WHERE embedding_fulltext_search MATCH ?
        ORDER BY rank
        LIMIT ?
    """

    _METADATA_SQL = """
        SELECT em.id, em.key, em.string_value, em.int_value, em.float_value
        FROM embedding_metadata em
        WHERE em.id IN ({placeholders})
    """

    _SEGMENT_COLLECTION_SQL = """
        SELECT s.id AS segment_id, c.name AS collection_name
        FROM segments s
        JOIN collections c ON c.id = s.collection
    """

    def __init__(self, persist_directory: str) -> None:
        self.persist_directory = persist_directory
        self._db_path = os.path.join(persist_directory, "chroma.sqlite3")
        self._segment_to_collection: Dict[str, str] = {}
        self._collection_name_map = {
            "alpha_claude_code": "alpha",
            "beta_claude_desktop": "beta",
        }
        self._load_segment_map()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA query_only=ON")
        return conn

    def _load_segment_map(self) -> None:
        try:
            conn = self._get_conn()
            try:
                rows = conn.execute(self._SEGMENT_COLLECTION_SQL).fetchall()
                for seg_id, col_name in rows:
                    simple = self._collection_name_map.get(col_name, col_name)
                    self._segment_to_collection[seg_id] = simple
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("FTS: failed to load segment map: %s", exc)

    def search(
        self,
        query_text: str,
        n_results: int = 10,
        collections: Optional[List[str]] = None,
        project_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search using FTS5 full-text search."""
        if not query_text or not query_text.strip():
            raise ValueError("query_text cannot be empty")

        fts_query = self._build_fts_query(query_text)
        fetch_limit = n_results * 5

        conn = self._get_conn()
        try:
            rows = conn.execute(self._SEARCH_SQL, (fts_query, fetch_limit)).fetchall()
            if not rows:
                return {"results": [], "total_matches": 0, "search_type": "fts5"}

            # Build result list with collection filtering
            int_ids = []
            doc_map = {}
            for int_id, uuid_id, document, segment_id, rank in rows:
                col = self._segment_to_collection.get(segment_id, "unknown")
                if collections and col not in collections:
                    continue
                int_ids.append(int_id)
                doc_map[int_id] = {
                    "id": uuid_id,
                    "document": document,
                    "collection": col,
                    "rank": rank,
                }

            if not int_ids:
                return {"results": [], "total_matches": 0, "search_type": "fts5"}

            metadata_map = self._fetch_metadata(conn, int_ids)

            results = []
            for int_id in int_ids:
                info = doc_map[int_id]
                meta = metadata_map.get(int_id, {})

                if project_filter and meta.get("project") != project_filter:
                    continue

                # Remove chroma:document from metadata (it's the document itself)
                meta.pop("chroma:document", None)

                results.append({
                    "id": info["id"],
                    "document": info["document"],
                    "metadata": meta,
                    "distance": abs(info["rank"]),
                    "collection": info["collection"],
                })

                if len(results) >= n_results:
                    break

            return {
                "results": results,
                "total_matches": len(results),
                "search_type": "fts5",
            }
        except Exception as exc:
            logger.error("FTS search failed: %s", exc)
            raise RuntimeError(f"FTS search failed: {exc}") from exc
        finally:
            conn.close()

    def _build_fts_query(self, query_text: str) -> str:
        """Convert user query to FTS5 query syntax."""
        special = set('*"(){}[]^~:+-')
        cleaned = "".join(c if c not in special else " " for c in query_text)
        tokens = cleaned.split()
        if not tokens:
            return query_text
        quoted = [f'"{t}"' for t in tokens if t.strip()]
        return " OR ".join(quoted)

    def _fetch_metadata(
        self, conn: sqlite3.Connection, int_ids: List[int]
    ) -> Dict[int, Dict[str, Any]]:
        """Fetch metadata for a list of embedding integer IDs."""
        if not int_ids:
            return {}
        placeholders = ",".join("?" for _ in int_ids)
        sql = self._METADATA_SQL.format(placeholders=placeholders)
        rows = conn.execute(sql, int_ids).fetchall()

        meta_map: Dict[int, Dict[str, Any]] = {}
        for emb_id, key, str_val, int_val, float_val in rows:
            if emb_id not in meta_map:
                meta_map[emb_id] = {}
            value = str_val if str_val is not None else (int_val if int_val is not None else float_val)
            meta_map[emb_id][key] = value
        return meta_map

    def is_available(self) -> bool:
        """Check if the FTS index is usable (fast: no COUNT, just LIMIT 1)."""
        try:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT 1 FROM embedding_fulltext_search_data LIMIT 1"
                ).fetchone()
                return row is not None
            finally:
                conn.close()
        except Exception:
            return False


# ---------------------------------------------------------------------------
# ChromaDB Worker Client — secondary search path for vector/semantic search
# ---------------------------------------------------------------------------

class ChromaWorkerClient:
    """Manages a ChromaDB worker subprocess for vector search.

    All ChromaDB operations run in a dedicated subprocess. If it segfaults
    due to chromadb_rust_bindings, only the worker dies — the API server
    stays up and falls back to FTS search.
    """

    _READY_TIMEOUT = 60
    _RPC_TIMEOUT = 30

    def __init__(self, persist_directory: str, embedding_model_name: str) -> None:
        self.persist_directory = persist_directory
        self.embedding_model_name = embedding_model_name
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._available = False
        try:
            self._start_worker()
            self._available = True
        except Exception as exc:
            logger.warning("ChromaDB worker failed to start: %s — vector search disabled", exc)

    def _start_worker(self) -> None:
        env = os.environ.copy()
        env["CHROMA_PERSIST_DIR"] = self.persist_directory
        env["EMBEDDING_MODEL"] = self.embedding_model_name

        logger.info("Starting ChromaDB worker subprocess...")
        self._process = subprocess.Popen(
            [sys.executable, "-m", "daemon.chroma_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=env,
            text=True,
            bufsize=1,
        )

        deadline = time.monotonic() + self._READY_TIMEOUT
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"ChromaDB worker exited during startup (rc={self._process.returncode})"
                )
            line = self._process.stdout.readline()
            if not line:
                raise RuntimeError("ChromaDB worker closed stdout before signalling ready")
            try:
                msg = json.loads(line)
                if msg.get("result") == "ready":
                    logger.info("ChromaDB worker ready (pid=%d)", self._process.pid)
                    return
            except json.JSONDecodeError:
                pass

        raise RuntimeError(f"ChromaDB worker did not become ready within {self._READY_TIMEOUT}s")

    def _is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _terminate(self) -> None:
        if self._process:
            try:
                self._process.stdin.close()
            except Exception:
                pass
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                    self._process.wait(timeout=2)
                except Exception:
                    pass
            self._process = None

    def _rpc(self, method: str, params: dict) -> Any:
        import select

        req_id = str(uuid.uuid4())
        self._process.stdin.write(
            json.dumps({"id": req_id, "method": method, "params": params}) + "\n"
        )
        self._process.stdin.flush()

        ready, _, _ = select.select([self._process.stdout], [], [], self._RPC_TIMEOUT)
        if not ready:
            raise RuntimeError(f"Worker RPC timeout after {self._RPC_TIMEOUT}s")

        line = self._process.stdout.readline()
        if not line:
            raise RuntimeError("Worker process died (stdout EOF)")

        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(f"Worker error: {resp['error']}")
        return resp["result"]

    @property
    def is_available(self) -> bool:
        return self._available and self._is_alive()

    def call(self, method: str, params: dict) -> Any:
        if not self._available:
            return None
        with self._lock:
            if not self._is_alive():
                logger.warning("ChromaDB worker died — restarting...")
                try:
                    self._start_worker()
                except Exception as exc:
                    logger.error("Worker restart failed: %s — disabling vector search", exc)
                    self._available = False
                    return None
            try:
                return self._rpc(method, params)
            except RuntimeError as exc:
                logger.error("Worker call failed (%s) — restarting once", exc)
                self._terminate()
                try:
                    self._start_worker()
                    return self._rpc(method, params)
                except Exception as exc2:
                    logger.error("Worker retry failed: %s — disabling vector search", exc2)
                    self._available = False
                    return None

    def shutdown(self) -> None:
        with self._lock:
            self._terminate()
            self._available = False


# ---------------------------------------------------------------------------
# Main Database Class
# ---------------------------------------------------------------------------

class DualSourceKnowledgeDB:
    """Dual-collection knowledge base with hybrid FTS + vector search.

    Primary search: SQLite FTS5 (fast, reliable, works on all collections).
    Secondary search: ChromaDB vector search (beta collection only — alpha
    segfaults due to 2M+ record HNSW index).
    """

    def __init__(
        self,
        persist_directory: str = "/app/knowledge",
        embedding_model_name: str = "all-MiniLM-L6-v2",
    ) -> None:
        self.persist_directory = persist_directory
        self.embedding_model_name = embedding_model_name
        os.makedirs(persist_directory, exist_ok=True)

        # Primary: FTS5 search (always available if SQLite DB exists)
        self._fts = FTSSearchEngine(persist_directory)
        fts_ok = self._fts.is_available()
        logger.info("FTS5 search engine: %s", "available" if fts_ok else "unavailable")

        # Secondary: ChromaDB worker for vector search (may fail gracefully)
        self._worker = ChromaWorkerClient(persist_directory, embedding_model_name)
        logger.info(
            "ChromaDB vector search: %s",
            "available" if self._worker.is_available else "disabled",
        )

        if not fts_ok and not self._worker.is_available:
            raise RuntimeError("No search backend available (FTS5 and ChromaDB both failed)")

        logger.info("DualSourceKnowledgeDB initialized successfully")

    def query_unified(
        self,
        query_text: str,
        n_results: int = 10,
        collections: Optional[List[str]] = None,
        project_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search across collections using FTS5 (primary) + optional vector search."""
        if collections is None:
            collections = ["alpha", "beta"]

        if not query_text or not query_text.strip():
            raise ValueError("query_text cannot be empty")

        # Primary: FTS5 search
        fts_results = None
        try:
            fts_results = self._fts.search(
                query_text=query_text,
                n_results=n_results,
                collections=collections,
                project_filter=project_filter,
            )
        except Exception as exc:
            logger.warning("FTS5 search failed: %s — trying vector search", exc)

        # Secondary: Vector search on beta only (alpha segfaults)
        vector_results = None
        if self._worker.is_available and "beta" in collections:
            try:
                vector_results = self._worker.call("query", {
                    "query_text": query_text,
                    "n_results": n_results,
                    "collections": ["beta"],
                    "project_filter": project_filter,
                })
            except Exception as exc:
                logger.warning("Vector search failed: %s", exc)

        # Merge results
        if fts_results and vector_results:
            return self._merge_results(fts_results, vector_results, n_results)
        elif fts_results:
            return fts_results
        elif vector_results:
            return vector_results
        else:
            raise RuntimeError("All search backends failed")

    def _merge_results(
        self, fts: Dict, vector: Dict, n_results: int
    ) -> Dict[str, Any]:
        """Merge FTS5 and vector search results, deduplicating by ID."""
        seen_ids = set()
        merged = []

        # FTS results first (more relevant for text search)
        for r in fts.get("results", []):
            seen_ids.add(r["id"])
            merged.append(r)

        # Add unique vector results
        for r in vector.get("results", []):
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                merged.append(r)

        return {
            "results": merged[:n_results],
            "total_matches": len(merged),
            "search_type": "hybrid",
        }

    def index_conversation(
        self,
        conversation: Dict[str, Any],
        collection: str = "auto",
    ) -> None:
        from dataclasses import asdict, is_dataclass

        if not self._worker.is_available:
            raise RuntimeError("ChromaDB worker is not available for indexing")

        conv_dict = asdict(conversation) if is_dataclass(conversation) else conversation
        result = self._worker.call("index", {
            "conversation": conv_dict,
            "collection": collection,
        })
        if result is None:
            raise RuntimeError("Indexing failed: worker unavailable")

    def get_collection_stats(self) -> Dict[str, Any]:
        """Get document counts per collection via SQLite (safe, no ChromaDB client calls).

        Works with both ChromaDB 1.3.x and 1.5.x schemas. The 1.5.x migration
        added a METADATA/VECTOR segment split; we filter to METADATA segments only
        to avoid double-counting.

        Returns:
            {"alpha": {"count": N}, "beta": {"count": N}, "total": {"count": N}}
        """
        try:
            import sqlite3

            collection_name_map = {
                "alpha_claude_code": "alpha",
                "beta_claude_desktop": "beta",
            }
            stats: Dict[str, Any] = {
                "alpha": {"count": 0, "sources": [], "projects": []},
                "beta": {"count": 0, "sources": [], "projects": []},
            }

            sqlite_path = os.path.join(self.persist_directory, "chroma.sqlite3")
            conn = sqlite3.connect(sqlite_path)
            cursor = conn.cursor()

            # ChromaDB 1.5+ splits segments into METADATA and VECTOR scopes.
            # Embeddings are stored under METADATA segments; filter to avoid
            # double-counting when both segment types join to the same embeddings.
            cursor.execute(
                """
                SELECT c.name, COUNT(e.id)
                FROM collections c
                JOIN segments s ON s.collection = c.id
                JOIN embeddings e ON e.segment_id = s.id
                WHERE s.scope = 'METADATA'
                GROUP BY c.name
                """
            )

            total_count = 0
            for chroma_name, count in cursor.fetchall():
                simple_name = collection_name_map.get(chroma_name)
                if simple_name:
                    stats[simple_name]["count"] = count
                    total_count += count

            conn.close()

            stats["total"] = {"count": total_count, "sources": [], "projects": []}
            logger.info(
                f"Retrieved stats for all collections: {total_count} total messages"
            )
            return stats

        except Exception as e:
            logger.error(f"Failed to get collection stats: {e}")
            raise RuntimeError(f"Stats collection failed: {e}") from e

    def get_stats(self) -> Dict[str, Any]:
        return self.get_collection_stats()

    def test_search(self) -> Dict[str, Any]:
        """Run a quick search to validate the worker and HNSW index are functional."""
        try:
            result = self._worker.call("query", {
                "query_text": "test",
                "n_results": 1,
                "collections": ["alpha", "beta"],
                "project_filter": None,
            })
            return {
                "status": "PASS",
                "total_matches": result.get("total_matches", 0),
            }
        except Exception as exc:
            return {"status": "FAIL", "error": str(exc)[:200]}

    def shutdown(self) -> None:
        if self._worker:
            self._worker.shutdown()
