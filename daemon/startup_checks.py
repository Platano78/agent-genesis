"""
startup_checks.py — run at daemon startup to validate the environment.

Checks:
  1. chromadb version matches the pinned version (1.3.5).
  2. beta_claude_desktop collection is non-empty; logs a CRITICAL
     warning with import instructions if it is empty.
"""
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

PINNED_CHROMADB_VERSION = "1.5.0"
BETA_COLLECTION_NAME = "beta_claude_desktop"

_SQLITE_COUNT_SQL = """
    SELECT c.name, COUNT(e.id)
    FROM collections c
    JOIN segments s ON s.collection = c.id
    JOIN embeddings e ON e.segment_id = s.id
    GROUP BY c.name
"""


def _get_sqlite_counts(persist_directory: str) -> dict:
    """Return {collection_name: count} from ChromaDB's SQLite file."""
    db_path = os.path.join(persist_directory, "chroma.sqlite3")
    if not os.path.exists(db_path):
        return {}
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(_SQLITE_COUNT_SQL).fetchall()
        conn.close()
        return {name: count for name, count in rows}
    except Exception as exc:
        logger.warning("startup_checks: SQLite query failed: %s", exc)
        return {}


def run_startup_checks(persist_directory: str = "/app/knowledge") -> dict:
    """
    Run all startup checks.

    Returns:
        {
            "version_ok":    bool,
            "beta_populated": bool,
            "warnings":      list[str],
        }
    """
    warnings = []

    # --- 1. chromadb version check ---
    version_ok = False
    try:
        import chromadb
        actual = getattr(chromadb, "__version__", "unknown")
        if actual == PINNED_CHROMADB_VERSION:
            version_ok = True
            logger.info("startup_checks: chromadb==%s OK", actual)
        else:
            msg = (
                f"chromadb version mismatch: expected {PINNED_CHROMADB_VERSION}, "
                f"got {actual}. Client may segfault on list_collections()/.count()."
            )
            logger.warning("startup_checks: %s", msg)
            warnings.append(msg)
    except ImportError:
        msg = "chromadb is not installed"
        logger.error("startup_checks: %s", msg)
        warnings.append(msg)

    # --- 2. beta collection population check ---
    counts = _get_sqlite_counts(persist_directory)
    beta_count = counts.get(BETA_COLLECTION_NAME, 0)
    beta_populated = beta_count > 0

    if beta_populated:
        logger.info(
            "startup_checks: beta collection has %d documents — OK", beta_count
        )
    else:
        msg = (
            f"beta_claude_desktop collection is EMPTY (0 documents). "
            f"Run import_to_container.py to populate it. "
            f"Example: docker exec agent-genesis python import_to_container.py "
            f"data-2025-11-22-16-43-55-batch-0000.zip"
        )
        logger.critical("startup_checks: %s", msg)
        warnings.append(msg)

    return {
        "version_ok": version_ok,
        "beta_populated": beta_populated,
        "warnings": warnings,
    }
