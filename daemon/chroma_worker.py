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


def respond_ok(req_id: str, result) -> None:
    _write({"id": req_id, "result": result})


def respond_err(req_id: str, error: str) -> None:
    _write({"id": req_id, "error": error})


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

        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=embedding_model
        )
        client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )

        # Load beta collection (small, works fine)
        try:
            beta = client.get_or_create_collection(
                "beta_claude_desktop", embedding_function=ef
            )
            # Verify it works with a quick count
            beta_count = beta.count()
            collections["beta"] = beta
            logger.info("beta collection loaded: %d documents", beta_count)
        except Exception as exc:
            logger.error("Failed to load beta collection: %s", exc)

        # Skip alpha collection — 2M+ records cause HNSW segfault.
        # Alpha is searched via FTS5 in the parent process instead.
        logger.info(
            "alpha collection SKIPPED (vector search disabled — "
            "2M+ record HNSW segfaults; using FTS5 in parent)"
        )

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

        try:
            if method == "ping":
                respond_ok(req_id, "pong")
            elif method == "query":
                respond_ok(req_id, _handle_query(params, collections))
            elif method == "index":
                _handle_index(params, collections, client, ef)
                respond_ok(req_id, {"indexed": True})
            else:
                respond_err(req_id, f"Unknown method: {method!r}")
        except Exception as exc:
            logger.error("Error in method %r: %s", method, exc, exc_info=True)
            respond_err(req_id, str(exc))


def _handle_query(params: dict, collections: dict) -> dict:
    query_text = params.get("query_text", "")
    if not query_text.strip():
        raise ValueError("query_text cannot be empty")
    n_results = int(params.get("n_results", 10))
    col_names = params.get("collections", ["beta"])
    project_filter = params.get("project_filter")
    where = {"project": project_filter} if project_filter else None

    results = []
    for col_name in col_names:
        if col_name not in collections:
            logger.debug("Collection %r not available for vector search, skipping", col_name)
            continue
        res = collections[col_name].query(
            query_texts=[query_text],
            n_results=n_results,
            where=where,
        )
        for i in range(len(res["ids"][0])):
            results.append({
                "id": res["ids"][0][i],
                "document": res["documents"][0][i],
                "metadata": res["metadatas"][0][i],
                "distance": res["distances"][0][i],
                "collection": col_name,
            })
    results.sort(key=lambda x: x["distance"])
    return {"results": results[:n_results], "total_matches": len(results)}


def _handle_index(params: dict, collections: dict, client, ef) -> None:
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
        target = client.get_or_create_collection(chroma_name, embedding_function=ef)

    messages = conv.get("messages", [])
    conversation_id = conv.get("id", str(uuid.uuid4()))
    conv_meta = {
        "project": conv.get("project", ""),
        "source": "jsonl",
        "cwd": conv.get("cwd", ""),
        "git_branch": conv.get("git_branch", ""),
    }

    documents, metadatas, ids = [], [], []
    for msg_idx, msg in enumerate(messages):
        content = msg.get("content", "")
        if not content.strip():
            continue
        ts = msg.get("timestamp", datetime.utcnow())
        ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)
        documents.append(content)
        metadatas.append({
            "conversation_id": conversation_id,
            "role": msg.get("role", "unknown"),
            "timestamp": ts_str,
            "project": conv_meta["project"],
            "source": conv_meta["source"],
            "cwd": conv_meta["cwd"],
            "git_branch": conv_meta["git_branch"],
        })
        # Deterministic ID: same conversation + message index = same ID
        # Prevents duplicates on reindex
        doc_id = hashlib.sha256(
            f"{conversation_id}:{msg_idx}:{content[:200]}".encode()
        ).hexdigest()[:36]
        ids.append(doc_id)

    if documents:
        target.upsert(documents=documents, metadatas=metadatas, ids=ids)
        logger.info(
            "Indexed %d messages from %s into %s", len(documents), conversation_id, collection
        )


if __name__ == "__main__":
    main()
