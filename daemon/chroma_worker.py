"""ChromaDB Worker Subprocess.

Runs as a dedicated process that owns all ChromaDB connections.
Communicates via newline-delimited JSON-RPC over stdin/stdout.
All log output goes to stderr (visible in docker logs).

If this process segfaults due to chromadb_rust_bindings, only it dies.
The parent API server stays alive and falls back to FTS search.

Protocol
--------
Request  (parent -> worker, one JSON per line):
    {"id": "<uuid>", "method": "ping|query|index", "params": {...}}

Response (worker -> parent, one JSON per line):
    {"id": "<uuid>", "result": <value>}
    {"id": "<uuid>", "error": "<message>"}

Startup
-------
After successful init, worker writes:
    {"id": "__init__", "result": "ready"}
"""

import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import datetime

# All logging goes to stderr; stdout is reserved for JSON-RPC responses.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("chroma_worker")


def _write(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def respond_ok(req_id: str, result, gen=None) -> None:
    _write({"id": req_id, "result": result, "gen": gen})


def respond_err(req_id: str, error: str, gen=None, error_kind: str = "backend") -> None:
    # error_kind classifies parent-side breaker treatment: "request" (bad
    # params, unknown method) never touches breaker health; "backend"
    # (default, fail-safe) always does. See docs/plans/worker-breaker-redesign.md R4.
    _write({"id": req_id, "error": error, "gen": gen, "error_kind": error_kind})


MANIFEST_VERSION = "1"


def _resolve_manifest(embedding_model: str) -> dict:
    return {
        "ag_embedding_model": embedding_model,
        "ag_query_prefix": os.environ.get("EMBEDDING_QUERY_PREFIX", ""),
        "ag_manifest_version": MANIFEST_VERSION,
    }


def open_verified_collection(client, chroma_name: str, ef, embedding_model: str):
    """Get or create a collection, enforcing the embedding-model manifest.

    On creation, stamps the collection with a manifest recording which model
    produced its vectors. On reopen, a mismatched manifest means the volume
    holds vectors from a different embedding model than the one currently
    resolved -- serving it would silently mix incompatible vector spaces, so
    it is refused (returns None) rather than served wrong. A collection with
    no manifest at all (pre-existing index, indexed before this check existed)
    is served unverified -- we cannot know what produced those vectors, and
    refusing it would break the currently deployed index.
    """
    manifest = _resolve_manifest(embedding_model)
    try:
        col = client.get_collection(chroma_name, embedding_function=ef)
    except Exception:
        col = client.create_collection(chroma_name, embedding_function=ef, metadata=manifest)
        logger.info(
            "Created collection %s with embedding manifest (model=%s)", chroma_name, embedding_model
        )
        return col

    existing_model = (col.metadata or {}).get("ag_embedding_model")
    if existing_model is None:
        logger.warning(
            "Collection %s has no embedding manifest (pre-existing index) -- "
            "proceeding unverified against resolved model=%s", chroma_name, embedding_model
        )
        return col
    if existing_model != embedding_model:
        logger.error(
            "Collection %s embedding manifest mismatch: manifest model=%s, resolved model=%s "
            "-- refusing to serve this collection", chroma_name, existing_model, embedding_model
        )
        return None
    return col


def main() -> None:
    persist_dir = os.environ.get("CHROMA_PERSIST_DIR", "/app/knowledge")
    embedding_model = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    logger.info(
        "ChromaDB worker starting (pid=%d, persist_dir=%s)", os.getpid(), persist_dir
    )

    collections = {}
    try:
        import chromadb
        from chromadb.config import Settings
        from chromadb.utils import embedding_functions

        # chromadb's SentenceTransformerEmbeddingFunction defaults device="cpu",
        # which silently pins embedding to CPU even on a GPU host. Resolve it
        # explicitly and log it -- that ambiguity cost a full reindex once.
        embedding_device = os.environ.get("EMBEDDING_DEVICE")
        if not embedding_device:
            try:
                import torch

                embedding_device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                embedding_device = "cpu"
        logger.info(
            "Embedding device=%s model=%s", embedding_device, embedding_model
        )

        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=embedding_model, device=embedding_device
        )
        client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )

        # Load beta collection (small, works fine)
        try:
            beta = open_verified_collection(client, "beta_claude_desktop", ef, embedding_model)
            if beta is not None:
                # Verify it works with a quick count
                beta_count = beta.count()
                collections["beta"] = beta
                logger.info("beta collection loaded: %d documents", beta_count)
        except Exception as exc:
            logger.error("Failed to load beta collection: %s", exc)

        # Load alpha collection (rebuilt from 2M+ to ~35K docs, segfault fixed)
        try:
            alpha = open_verified_collection(client, "alpha_claude_code", ef, embedding_model)
            if alpha is not None:
                alpha_count = alpha.count()
                collections["alpha"] = alpha
                logger.info("alpha collection loaded: %d documents", alpha_count)
        except Exception as exc:
            logger.warning("Failed to load alpha collection: %s — FTS5 fallback", exc)

        if not collections:
            logger.warning("No collections loaded — worker will only handle indexing")

    except Exception as exc:
        logger.error("Worker init failed: %s", exc, exc_info=True)
        sys.exit(1)

    # Signal ready to the parent process.
    _write({"id": "__init__", "result": "ready"})

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.error("Bad JSON from parent: %s", exc)
            continue

        req_id = req.get("id", "unknown")
        method = req.get("method", "")
        params = req.get("params", {})
        gen = req.get("gen")

        try:
            if method == "ping":
                respond_ok(req_id, "pong", gen)
            elif method == "query":
                respond_ok(req_id, _handle_query(params, collections), gen)
            elif method == "index":
                _handle_index(params, collections, client, ef, embedding_model)
                respond_ok(req_id, {"indexed": True}, gen)
            else:
                respond_err(req_id, f"Unknown method: {method!r}", gen, error_kind="request")
        except ValueError as exc:
            # Malformed client request (e.g. empty query_text) -- must never
            # open a backend-health breaker for everyone else.
            logger.warning("Request error in method %r: %s", method, exc)
            respond_err(req_id, str(exc), gen, error_kind="request")
        except Exception as exc:
            logger.error("Error in method %r: %s", method, exc, exc_info=True)
            respond_err(req_id, str(exc), gen, error_kind="backend")


def _handle_query(params: dict, collections: dict) -> dict:
    query_text = params.get("query_text", "")
    if not query_text.strip():
        raise ValueError("query_text cannot be empty")
    # Asymmetric retrieval models (arctic-embed, bge, e5) prefix the QUERY only;
    # passages are embedded bare. ChromaDB applies one embedding function to
    # both, so the query-side prefix has to be applied here or query and passage
    # vectors land in different regions of the space.
    query_text = os.environ.get("EMBEDDING_QUERY_PREFIX", "") + query_text
    n_results = int(params.get("n_results", 10))
    col_names = params.get("collections", ["beta"])
    project_filter = params.get("project_filter")
    where = {"project": project_filter} if project_filter else None

    results = []
    served_collections = []
    for col_name in col_names:
        if col_name not in collections:
            logger.debug("Collection %r not available for vector search, skipping", col_name)
            continue
        res = collections[col_name].query(
            query_texts=[query_text],
            n_results=n_results,
            where=where,
        )
        served_collections.append(col_name)
        for i in range(len(res["ids"][0])):
            results.append({
                "id": res["ids"][0][i],
                "document": res["documents"][0][i],
                "metadata": res["metadatas"][0][i],
                "distance": res["distances"][0][i],
                "collection": col_name,
            })
    results.sort(key=lambda x: x["distance"])
    # served_collections lets the parent's probe distinguish "genuinely
    # queried, zero hits" from "collection wasn't served at all" (defect 6) --
    # a collection that failed to load looks identical to a real empty result
    # unless this is reported separately.
    return {
        "results": results[:n_results],
        "total_matches": len(results),
        "served_collections": served_collections,
    }


def _handle_index(params: dict, collections: dict, client, ef, embedding_model: str) -> None:
    """Index a conversation into the appropriate collection."""
    conv = params.get("conversation", {})
    collection = params.get("collection", "auto")

    if collection == "auto":
        source = conv.get("metadata", {}).get("source", "")
        col_map = {"json": "alpha", "leveldb": "beta"}
        collection = col_map.get(source.lower(), "")
        if not collection:
            raise ValueError(f"Cannot auto-detect collection from source={source!r}")

    # Get or create the target collection (even if not loaded for search)
    col_name_map = {"alpha": "alpha_claude_code", "beta": "beta_claude_desktop"}
    chroma_name = col_name_map.get(collection)
    if not chroma_name:
        raise ValueError(f"Invalid collection: {collection!r}")

    if collection in collections:
        target = collections[collection]
    else:
        # Create/get collection for indexing even if not used for search
        target = open_verified_collection(client, chroma_name, ef, embedding_model)
        if target is None:
            raise ValueError(
                f"Refusing to index into {chroma_name!r}: embedding manifest mismatch"
            )

    conversation_id = conv.get("id", str(uuid.uuid4()))
    conv_meta = {
        "project": conv.get("project", ""),
        "source": conv.get("source") or "jsonl",
        "cwd": conv.get("cwd", ""),
        "git_branch": conv.get("git_branch", ""),
    }

    from daemon.conversation_chunker import chunk_conversation
    chunks = chunk_conversation(conv, conv_meta)

    if chunks:
        # Batch upserts to avoid timeout on large conversations. 20 starves a
        # GPU (fits in one sentence-transformers internal batch of 32); larger
        # batches amortize worker IPC + chroma upsert + SQLite transaction cost.
        BATCH_SIZE = int(os.environ.get("UPSERT_BATCH_SIZE", "128"))
        for i in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[i:i + BATCH_SIZE]
            documents = [c["document"] for c in batch]
            metadatas = [c["metadata"] for c in batch]
            ids = [c["doc_id"] for c in batch]
            target.upsert(documents=documents, metadatas=metadatas, ids=ids)
        logger.info(
            "Indexed %d chunks from %s into %s", len(chunks), conversation_id, collection
        )


if __name__ == "__main__":
    main()
