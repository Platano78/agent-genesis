"""Dual-collection knowledge database for multi-source conversation indexing.

This module provides search across two distinct data sources (Claude Code and
Claude export imports)
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
from typing import Any, Dict, List, Optional, Tuple
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

        # Refresh segment map to pick up newly indexed data
        self._load_segment_map()

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

class WorkerRequestError(Exception):
    """A malformed/invalid client request (bad params, unknown method).

    Never touches breaker state — a bad query from one caller must not
    degrade vector search for everyone (defect 3 in a94ad08).
    """


class WorkerBackendError(Exception):
    """The worker/backend itself failed (crash, timeout, EOF, exception).

    Always a circuit-breaker-relevant failure.
    """


class _SupervisorStop(Exception):
    """Internal sentinel: `_supervise_once()` raises this on STOPPED so the
    F3 blanket except in `_supervise()` cannot mistake a clean shutdown for
    a fault to log-and-continue past."""


class ChromaWorkerClient:
    """Manages a ChromaDB worker subprocess for vector search.

    All ChromaDB operations run in a dedicated subprocess. If it segfaults
    due to chromadb_rust_bindings, only the worker dies — the API server
    stays up and falls back to FTS search.

    Circuit breaker: a locked state machine (CLOSED -> OPEN -> RECOVERING ->
    CLOSED, plus terminal STOPPED) owned entirely by one permanent
    Event-driven supervisor thread started in __init__. The request path
    (call()) only ever reads state and records failures; it never starts,
    restarts, or terminates the worker process, and recovery success is
    published via an epoch compare-and-set so a failure that lands mid-probe
    can never be silently overwritten by a stale success (see docs/plans/
    worker-breaker-redesign.md, rulings R1-R10).
    """

    _READY_TIMEOUT = 60
    # One timeout for all three RPC methods was wrong: `index` batches can
    # legitimately run long, while `query`/`ping` hanging that long means
    # something is actually stuck. Per-method, env-overridable.
    _RPC_TIMEOUT_QUERY = int(os.environ.get("RPC_TIMEOUT_QUERY", "20"))
    _RPC_TIMEOUT_INDEX = int(os.environ.get("RPC_TIMEOUT_INDEX", "120"))
    _RPC_TIMEOUT_PING = int(os.environ.get("RPC_TIMEOUT_PING", "10"))

    # R6: how long a query will wait to acquire the RPC pipe lock before
    # degrading to fts5. Sits well above a normal ~250ms query and far below
    # the ~25s probe, so a request can never wait out a probe.
    _QUERY_LOCK_TIMEOUT = float(os.environ.get("VECTOR_QUERY_LOCK_TIMEOUT", "3.0"))

    # F2: the CLOSED park must be bounded -- a dead worker process must self-
    # heal even if nothing ever explicitly recorded the failure that killed
    # it. Cost is one poll() every 30s, which is free.
    _LIVENESS_POLL_S = float(os.environ.get("VECTOR_LIVENESS_POLL_S", "30"))

    # R1 breaker states.
    _CLOSED = "CLOSED"
    _OPEN = "OPEN"
    _RECOVERING = "RECOVERING"
    _STOPPED = "STOPPED"

    _BACKOFF_SCHEDULE = (120, 300, 900, 1800)   # 2min, 5min, 15min, 30min
    _HEALTHY_RESET_AFTER = 600                  # 10min clean before backoff resets

    # R7: alpha holds 676,736 of 677,828 docs. Requiring both would let a
    # beta-only manifest mismatch drop the entire corpus to keyword-only.
    _PROBE_REQUIRED_COLLECTIONS = frozenset(
        c.strip() for c in
        os.environ.get("VECTOR_PROBE_REQUIRED_COLLECTIONS", "alpha").split(",")
        if c.strip()
    )

    def __init__(self, persist_directory: str, embedding_model_name: str) -> None:
        self.persist_directory = persist_directory
        self.embedding_model_name = embedding_model_name
        self._lock = threading.Lock()          # guards the stdin/stdout pipe -- outer lock
        self._state_lock = threading.Lock()    # guards breaker state -- always innermost,
                                                 # never held across an RPC or a _wake.wait()
        self._process: Optional[subprocess.Popen] = None
        # Bumped every time a worker subprocess is (re)started. Tags requests
        # so a late reply from a worker generation we've already abandoned
        # (e.g. after a timeout-triggered restart) is recognized as stale.
        self._generation = 0

        # Breaker state -- all under _state_lock.
        self._epoch = 0                          # bumped on every transition into OPEN
        self._consecutive_failures = 0           # failed recovery probes; drives backoff
        self._closed_since: Optional[float] = None
        self._state_since: float = time.monotonic()  # G2: monitor signal for a wedged state
        self._last_error: Optional[str] = None
        self._last_index_error: Optional[str] = None
        self._served_collections: List[str] = []

        self._wake = threading.Event()

        # B4: a successful start does not prove the HNSW index can answer --
        # only a probe does. RECOVERING withholds CLOSED until the
        # supervisor's first (immediate, failures=0) probe passes. A failed
        # start skips straight to OPEN and lets that same supervisor own
        # retry/backoff -- R5: __init__ never restarts on its own.
        try:
            self._start_worker()
            self._set_state(self._RECOVERING)
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("ChromaDB worker failed to start: %s — vector search disabled", exc)
            self._set_state(self._OPEN)

        # R2: exactly one permanent supervisor for the object's life. Never
        # _ensure_supervisor(), never a one-shot thread.
        self._supervisor = threading.Thread(
            target=self._supervise, name="chroma-worker-supervisor", daemon=True
        )
        self._supervisor.start()

    def _timeout_for(self, method: str) -> int:
        return {
            "query": self._RPC_TIMEOUT_QUERY,
            "index": self._RPC_TIMEOUT_INDEX,
            "ping": self._RPC_TIMEOUT_PING,
        }.get(method, self._RPC_TIMEOUT_QUERY)

    def _start_worker(self) -> None:
        """Spawn the worker and block until it signals ready.

        G1: `readline()` is an unbounded blocking read -- a SIGSTOPped worker,
        or one wedged mmapping a multi-GB HNSW index, never writes a line and
        never exits, so the old `while time.monotonic() < deadline:` guard
        (only checked *between* reads) never got back to firing. That left
        the supervisor parked inside this call forever, holding `_lock`, with
        state stuck RECOVERING -- the same "stranded with nothing watching"
        invariant violation as F1/F2, just reachable from a different state.
        Bounding the wait with `select` (mirroring `_rpc`) and terminating a
        worker that times out closes it.
        """
        import select

        self._generation += 1
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
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate()
                raise WorkerBackendError(
                    f"ChromaDB worker did not become ready within {self._READY_TIMEOUT}s"
                )
            if self._process.poll() is not None:
                raise WorkerBackendError(
                    f"ChromaDB worker exited during startup (rc={self._process.returncode})"
                )

            ready, _, _ = select.select([self._process.stdout], [], [], remaining)
            if not ready:
                self._terminate()
                raise WorkerBackendError(
                    f"ChromaDB worker did not become ready within {self._READY_TIMEOUT}s"
                )

            line = self._process.stdout.readline()
            if not line:
                raise WorkerBackendError("ChromaDB worker closed stdout before signalling ready")
            try:
                msg = json.loads(line)
                if msg.get("result") == "ready":
                    logger.info("ChromaDB worker ready (pid=%d)", self._process.pid)
                    return
            except json.JSONDecodeError:
                pass

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
        """Send one request and block for its matching response.

        Caller must hold `_lock`. Raises WorkerRequestError for a malformed
        client request (never breaker-relevant) and WorkerBackendError for
        every other failure mode -- timeout, EOF, malformed JSON, or a worker
        exception with error_kind != "request" (missing error_kind maps to
        backend too: fail-safe, matches pre-redesign behaviour).
        """
        import select

        if self._process is None:
            raise WorkerBackendError("Worker process is not running")

        req_id = str(uuid.uuid4())
        gen = self._generation
        self._process.stdin.write(
            json.dumps({"id": req_id, "method": method, "params": params, "gen": gen}) + "\n"
        )
        self._process.stdin.flush()

        timeout = self._timeout_for(method)
        deadline = time.monotonic() + timeout

        # Read responses until one matches this request's id AND generation,
        # discarding stragglers (e.g. a late reply from a previous timeout)
        # instead of returning them or letting them desync the stream.
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkerBackendError(f"Worker RPC timeout after {timeout}s")

            ready, _, _ = select.select([self._process.stdout], [], [], remaining)
            if not ready:
                raise WorkerBackendError(f"Worker RPC timeout after {timeout}s")

            line = self._process.stdout.readline()
            if not line:
                raise WorkerBackendError("Worker process died (stdout EOF)")

            try:
                resp = json.loads(line)
            except json.JSONDecodeError as exc:
                raise WorkerBackendError(f"Malformed worker response: {exc}") from exc

            if resp.get("id") != req_id or resp.get("gen") != gen:
                logger.warning(
                    "Discarding stale worker response (id=%s gen=%s, expected id=%s gen=%s)",
                    resp.get("id"), resp.get("gen"), req_id, gen,
                )
                continue

            if "error" in resp:
                if resp.get("error_kind") == "request":
                    raise WorkerRequestError(resp["error"])
                raise WorkerBackendError(resp["error"])
            return resp["result"]

    @property
    def is_available(self) -> bool:
        # Reflects the breaker, not just liveness: a live worker whose queries
        # are failing is NOT available for serving, and /health/vector must say so.
        with self._state_lock:
            state = self._state
        return state == self._CLOSED and self._is_alive()

    def _set_state(self, new_state: str) -> None:
        """Caller must already hold `_state_lock` (or, in __init__, be before
        the object is published to the supervisor thread). G2: stamps every
        transition so `breaker_status()` can expose `state_age_s` -- the
        monitor signal for "stuck RECOVERING/OPEN with nothing happening",
        the exact shape of the wedged-`_start_worker` bug this fixes."""
        self._state = new_state
        self._state_since = time.monotonic()

    # --- circuit breaker -------------------------------------------------
    #
    # 2026-08-17: a nightly INDEX batch blew the RPC deadline, and the old code
    # responded by latching `_available = False` forever. Hybrid search served
    # keyword-only for 5 hours behind an HTTP 200. This redesign's invariant:
    # the system can never reach a state where serving is off and nothing is
    # scheduled to turn it back on (R1-R3). Two rules survive from the original
    # fix:
    #
    #   1. An INDEX failure must never disable QUERYING. Conflating them was
    #      the actual bug -- a slow batch write killed reading.
    #   2. A request must never pay for recovery. Degrade now, heal in the
    #      background -- never restart or block on the request path (R5, R6).

    def _backoff_base_delay(self, failures: int) -> float:
        if failures <= 0:
            return 0
        # failures=1 -> first backoff rung (120s), not the second
        return self._BACKOFF_SCHEDULE[min(failures - 1, len(self._BACKOFF_SCHEDULE) - 1)]

    def _backoff_delay(self, failures: int) -> float:
        base = self._backoff_base_delay(failures)
        if base == 0:
            return 0
        # jitter so repeated failures don't synchronise into a thundering herd
        return base * (0.8 + 0.4 * (uuid.uuid4().int % 1000) / 1000.0)

    def _record_backend_failure(self, exc: Exception) -> None:
        """A backend-health failure: transition into OPEN and wake the supervisor.

        Never called for WorkerRequestError, and never called for index
        failures (see _call_index) -- both are deliberate exclusions, not
        oversights.
        """
        with self._state_lock:
            if self._state == self._STOPPED:
                return
            self._last_error = str(exc)
            # R10: fold the 600s clean-streak reset into the transition itself
            # -- single read of closed_since, under the same lock, instead of
            # a free-floating optional float read twice on the request path.
            if (self._state == self._CLOSED and self._closed_since is not None
                    and time.monotonic() - self._closed_since > self._HEALTHY_RESET_AFTER):
                self._consecutive_failures = 0
            self._epoch += 1
            self._set_state(self._OPEN)
            self._closed_since = None
        # Wakes a supervisor parked in CLOSED's indefinite wait, or one
        # already backing off in OPEN (harmless no-op there).
        self._wake.set()

    def _supervise(self) -> None:
        """The one permanent recovery loop. Owns all process lifecycle calls.

        F3: the loop body is wrapped in try/except -- this is the ONLY
        supervisor, and an unhandled exception anywhere inside it would end
        recovery permanently and silently, which is the exact invariant this
        redesign exists to guarantee. STOPPED still returns cleanly: a
        `return` is not an exception, so it is unaffected by the except.
        """
        while True:
            try:
                self._supervise_once()
            except _SupervisorStop:
                return
            except Exception:
                logger.exception("Unexpected error in worker supervisor loop -- continuing")
                time.sleep(1)  # avoid a busy-loop if the fault is persistent

    def _supervise_once(self) -> None:
        with self._state_lock:
            state = self._state
        if state == self._STOPPED:
            raise _SupervisorStop()

        if state == self._CLOSED:
            # Clear before check, not after: the reverse order is the lost
            # wakeup this whole redesign exists to kill. A failure landing
            # between an "after" clear and the wait would be swallowed.
            self._wake.clear()
            with self._state_lock:
                state = self._state
            if state == self._CLOSED:
                # F2: bounded park. A dead worker process must self-heal even
                # if nothing ever explicitly recorded the failure that killed
                # it (e.g. discovered only via a since-fixed index code path).
                self._wake.wait(timeout=self._LIVENESS_POLL_S)
                with self._state_lock:
                    still_closed = self._state == self._CLOSED
                if still_closed and not self._is_alive():
                    self._record_backend_failure(WorkerBackendError("worker process is not alive"))
            return

        # OPEN or RECOVERING (recovering only transiently at startup):
        # wait out backoff, or wake immediately on shutdown.
        with self._state_lock:
            failures = self._consecutive_failures
        delay = self._backoff_delay(failures)
        if delay:
            logger.info("Vector recovery backing off %.0fs (failure #%d)", delay, failures)
            self._wake.clear()
            self._wake.wait(timeout=delay)

        with self._state_lock:
            if self._state == self._STOPPED:
                raise _SupervisorStop()
            self._set_state(self._RECOVERING)
            epoch_snapshot = self._epoch

        success = self._probe()

        with self._state_lock:
            if self._state == self._STOPPED:
                raise _SupervisorStop()
            # R3: publish success only via compare-and-set. A failure
            # recorded during the probe bumped _epoch; the CAS fails, the
            # stale success is discarded, and we stay OPEN and loop again.
            if success and self._epoch == epoch_snapshot:
                self._set_state(self._CLOSED)
                self._consecutive_failures = 0
                self._closed_since = time.monotonic()
                logger.info("Vector search recovered")
            else:
                if success:
                    logger.warning(
                        "Probe succeeded but a failure landed during it "
                        "(epoch %d -> %d) -- discarding, staying OPEN",
                        epoch_snapshot, self._epoch,
                    )
                else:
                    self._consecutive_failures += 1
                self._set_state(self._OPEN)

    def _probe(self) -> bool:
        """Half-open probe: restart, ping, then a REAL query with a per-collection
        sentinel (R7, defect 6).

        A ping only proves the stdin loop is alive. It does NOT prove the HNSW
        index can answer -- and neither does a query that merely didn't raise:
        the worker silently skips collections it failed to load, so a
        zero-hit-but-successful response can hide a dead alpha. The probe
        instead asserts every required collection actually appears in
        `served_collections`.
        """
        try:
            with self._lock:
                self._terminate()
                self._start_worker()
                self._rpc("ping", {})
                result = self._rpc("query", {
                    "query_text": "canary", "n_results": 1,
                    "collections": ["alpha", "beta"],
                })
            served = set((result or {}).get("served_collections") or [])
            with self._state_lock:
                self._served_collections = sorted(served)
            missing = self._PROBE_REQUIRED_COLLECTIONS - served
            if missing:
                raise WorkerBackendError(f"Probe missing required collection(s): {sorted(missing)}")
            return True
        except Exception as exc:
            with self._state_lock:
                self._last_error = str(exc)
            logger.warning("Vector recovery probe failed: %s", exc)
            return False

    def _call_query(self, method: str, params: dict) -> Any:
        """R6: bounded-wait admission. `_lock` guards the whole RPC, so a
        non-blocking acquire would drop every concurrent query the moment two
        requests overlap -- worse than the bug being fixed. Instead: admit
        only when CLOSED, wait briefly (default 3s, well under the ~25s
        probe) for the pipe, then re-check nothing moved while queued.
        """
        with self._state_lock:
            if self._state != self._CLOSED:
                return None
            epoch_snapshot = self._epoch
            gen_snapshot = self._generation

        if not self._lock.acquire(timeout=self._QUERY_LOCK_TIMEOUT):
            logger.warning(
                "Vector query admission timed out after %.1fs waiting for the worker "
                "lock -- degrading to fts5 for this request", self._QUERY_LOCK_TIMEOUT,
            )
            return None
        try:
            with self._state_lock:
                if (self._state != self._CLOSED
                        or self._epoch != epoch_snapshot
                        or self._generation != gen_snapshot):
                    return None  # the world moved while we queued for the lock
            try:
                result = self._rpc(method, params)
            except WorkerRequestError as exc:
                logger.warning("Worker request error (%s): %s -- not a backend failure", method, exc)
                return None
            except Exception as exc:
                logger.error("Worker query call failed (%s) — degrading to keyword-only, "
                             "recovering in background", exc)
                self._record_backend_failure(exc)
                return None
        finally:
            self._lock.release()

        if isinstance(result, dict) and result.get("served_collections") is not None:
            with self._state_lock:
                self._served_collections = list(result["served_collections"])
        return result

    def _call_index(self, params: dict) -> Any:
        """Indexing keeps a blocking acquire -- an index job has no latency
        contract -- and a slow-but-live index batch must never disable
        querying: that conflation was the actual 2026-08-17 incident. So a
        timeout/error against a STILL-ALIVE worker only ever touches
        `_last_index_error`, never breaker state.

        But a worker process that is dead -- whether found dead on entry, or
        found dead right after an RPC blew up -- is a backend-health fact
        that the index path merely happened to observe first, not an index
        outcome. That must open the breaker too, or a dead worker discovered
        only via indexing strands serving CLOSED with nothing watching it
        (see F1 in the redesign follow-up). R5: never starts a worker here.
        """
        with self._lock:
            if not self._is_alive():
                self._last_index_error = "Worker is not running"
                logger.error("Worker index call failed: worker not running — "
                             "indexing degraded, serving unaffected")
                self._record_backend_failure(WorkerBackendError("worker process is not alive"))
                return None
            try:
                result = self._rpc("index", params)
            except WorkerRequestError as exc:
                logger.warning("Worker index request error: %s", exc)
                self._last_index_error = str(exc)
                return None
            except Exception as exc:
                self._last_index_error = str(exc)
                logger.error("Worker index call failed (%s) — indexing degraded, "
                             "serving unaffected", exc)
                if not self._is_alive():
                    self._record_backend_failure(exc)
                return None
        return result

    def call(self, method: str, params: dict) -> Any:
        if method == "index":
            return self._call_index(params)
        return self._call_query(method, params)

    def breaker_status(self) -> Dict[str, Any]:
        """R8: plain-dict snapshot for /health/vector."""
        with self._state_lock:
            state = self._state
            failures = self._consecutive_failures
            return {
                "state": state,
                "epoch": self._epoch,
                "generation": self._generation,
                "consecutive_failures": failures,
                "next_probe_in_s": self._backoff_base_delay(failures) if state == self._OPEN else None,
                # G2: a monitor's alert on "RECOVERING/OPEN for > 5min" is the
                # signature of a wedged supervisor -- the wedged-readline bug
                # left no other observable trace (no exception, no log spam).
                "state_age_s": time.monotonic() - self._state_since,
                "last_error": self._last_error,
                "last_index_error": self._last_index_error,
                "served_collections": list(self._served_collections),
            }

    def shutdown(self) -> None:
        with self._state_lock:
            self._set_state(self._STOPPED)
        self._wake.set()
        if self._supervisor is not None:
            self._supervisor.join(timeout=30)
            if self._supervisor.is_alive():
                logger.warning("Worker supervisor did not exit within shutdown timeout")
        with self._lock:
            self._terminate()


# ---------------------------------------------------------------------------
# Result ranking — Reciprocal Rank Fusion
#
# FTS results carry `distance = abs(bm25_rank)` where LARGER is better.
# Vector results carry a cosine `distance` where SMALLER is better. RRF is
# rank-based, not score-based, so fusing on rank position sidesteps having to
# calibrate the two incompatible scales.
# ---------------------------------------------------------------------------

RRF_K = 60

# Equal-weight RRF (1.0/1.0) was measured to regress recall@5 (12/17 -> 11/17 on the
# committed eval set): the vector arm has no relevance floor and surfaces ~0.67-cosine
# "matches" for tokens absent from the corpus entirely, and equal weighting promotes
# that noise to parity with FTS's top results, bumping a real hit out of the top 5.
# 1.0/0.3 recovers 12/17 while still letting a genuinely strong semantic match surface.
RRF_WEIGHT_FTS = 1.0
RRF_WEIGHT_VECTOR = 0.3


def _rrf_scores(
    weighted_ranked_lists: List[Tuple[List[Dict[str, Any]], float]], k: int = RRF_K
) -> Dict[Any, float]:
    """Sum weight/(k + rank) contributions per result id across ranked (best-first) lists."""
    scores: Dict[Any, float] = {}
    for lst, weight in weighted_ranked_lists:
        for idx, r in enumerate(lst, start=1):
            rid = r.get("id")
            scores[rid] = scores.get(rid, 0.0) + weight / (k + idx)
    return scores


def _dedupe_by_conversation(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep one result per conversation_id — the one with the highest relevance."""
    seen: Dict[Any, Dict[str, Any]] = {}
    for r in results:
        meta = r.get("metadata", r.get("metadatas", {}))
        if isinstance(meta, list):
            meta = meta[0] if meta else {}
        conv_id = meta.get("conversation_id", r.get("id"))
        if conv_id not in seen or r.get("relevance", 0.0) > seen[conv_id].get("relevance", 0.0):
            seen[conv_id] = r
    return list(seen.values())


def _normalize_and_sort(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize relevance into 0..1 (by max) and sort descending in place."""
    max_rel = max((r.get("relevance", 0.0) for r in results), default=0.0)
    if max_rel > 0:
        for r in results:
            r["relevance"] = r["relevance"] / max_rel
    results.sort(key=lambda r: r.get("relevance", 0.0), reverse=True)
    return results


def merge_ranked_results(
    fts_results: Optional[List[Dict[str, Any]]] = None,
    vector_results: Optional[List[Dict[str, Any]]] = None,
    k: int = RRF_K,
    fts_weight: float = RRF_WEIGHT_FTS,
    vector_weight: float = RRF_WEIGHT_VECTOR,
) -> List[Dict[str, Any]]:
    """Merge FTS + vector result lists via weighted Reciprocal Rank Fusion.

    Each list is sorted into its own correct best-first order before ranks
    are assigned (never assumed to already be ordered). Adds a `relevance`
    float (0..1, higher = better) to every result while leaving the existing
    `distance` field untouched. Dedupes by conversation_id, keeping the
    chunk with the highest relevance. Returns the merged list sorted by
    relevance descending (not truncated — callers slice to n_results).

    Either results argument may be omitted/empty to rank a single source alone.

    The default weights (1.0 FTS / 0.3 vector) were chosen from a recall@5
    simulation against the committed eval set (see RRF_WEIGHT_VECTOR comment
    above). That simulation reconstructed the FTS/vector source lists from an
    already-deduped, truncated hybrid API response, so the ranks fed into it
    were approximate — the weighting direction is sound but the specific
    12/17 recall figure is NOT a verified post-change number. Re-measure with
    docs/eval/run_eval.py against the live API after deploy.
    """
    fts_sorted = sorted(fts_results or [], key=lambda r: r.get("distance", 0), reverse=True)
    vector_sorted = sorted(vector_results or [], key=lambda r: r.get("distance", float("inf")))

    scores = _rrf_scores(
        [(lst, w) for lst, w in ((fts_sorted, fts_weight), (vector_sorted, vector_weight)) if lst],
        k=k,
    )

    by_id: Dict[Any, Dict[str, Any]] = {}
    for r in fts_sorted:
        by_id[r["id"]] = r
    for r in vector_sorted:
        if r["id"] not in by_id:
            by_id[r["id"]] = r

    scored = []
    for rid, r in by_id.items():
        r = dict(r)
        r["relevance"] = scores.get(rid, 0.0)
        scored.append(r)

    deduped = _dedupe_by_conversation(scored)
    return _normalize_and_sort(deduped)


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
        # Default from the environment so EMBEDDING_MODEL set on the container
        # actually reaches the worker; _start_worker overwrites env["EMBEDDING_MODEL"]
        # with this value, so a hardcoded default here silently wins over the env.
        embedding_model_name: str = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
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

        # Secondary: Vector search (alpha rebuilt from ~2M to ~35K docs, segfault fixed)
        vector_results = None
        if self._worker.is_available:
            try:
                vector_results = self._worker.call("query", {
                    "query_text": query_text,
                    "n_results": n_results,
                    "collections": collections,
                    "project_filter": project_filter,
                })
            except Exception as exc:
                logger.warning("Vector search failed: %s", exc)

        # Merge results — every path returns a `relevance`-ranked, sorted list.
        if fts_results and vector_results:
            return self._merge_results(fts_results, vector_results, n_results)
        elif fts_results:
            merged = merge_ranked_results(fts_results=fts_results.get("results", []))
            return {
                "results": merged[:n_results],
                "total_matches": len(merged),
                "search_type": fts_results.get("search_type", "fts5"),
            }
        elif vector_results:
            merged = merge_ranked_results(vector_results=vector_results.get("results", []))
            return {
                "results": merged[:n_results],
                "total_matches": len(merged),
                "search_type": vector_results.get("search_type", "vector"),
            }
        else:
            raise RuntimeError("All search backends failed")

    def _merge_results(
        self, fts: Dict, vector: Dict, n_results: int
    ) -> Dict[str, Any]:
        """Merge FTS5 and vector search results via RRF, deduplicating by conversation."""
        merged = merge_ranked_results(fts.get("results", []), vector.get("results", []))

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
        from datetime import datetime
        import json

        if not self._worker.is_available:
            raise RuntimeError("ChromaDB worker is not available for indexing")

        conv_dict = asdict(conversation) if is_dataclass(conversation) else conversation

        # Convert datetime objects to ISO strings for JSON serialization
        def _sanitize(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            if isinstance(obj, dict):
                return {k: _sanitize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_sanitize(v) for v in obj]
            return obj

        conv_dict = _sanitize(conv_dict)

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
            conn = sqlite3.connect(sqlite_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA read_uncommitted=ON")
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

            # Populate distinct projects and sources per collection
            for meta_key, stats_key in [("project", "projects"), ("source", "sources")]:
                cursor.execute("""
                    SELECT c.name, em.string_value
                    FROM embedding_metadata em
                    JOIN embeddings e ON e.id = em.id
                    JOIN segments s ON s.id = e.segment_id
                    JOIN collections c ON c.id = s.collection
                    WHERE em.key = ? AND s.scope = 'METADATA'
                      AND em.string_value IS NOT NULL AND em.string_value != ''
                    GROUP BY c.name, em.string_value
                """, (meta_key,))
                for chroma_name, value in cursor.fetchall():
                    simple_name = collection_name_map.get(chroma_name)
                    if simple_name and value not in stats[simple_name][stats_key]:
                        stats[simple_name][stats_key].append(value)

            conn.close()

            all_projects = sorted(set(stats["alpha"]["projects"] + stats["beta"]["projects"]))
            all_sources = sorted(set(stats["alpha"]["sources"] + stats["beta"]["sources"]))
            stats["total"] = {"count": total_count, "sources": all_sources, "projects": all_projects}
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
