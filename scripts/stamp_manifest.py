"""Stamp an existing ChromaDB volume with its embedding-model manifest.

The manifest check in daemon/chroma_worker.py fails OPEN for collections that
carry no manifest at all (it cannot know what produced those vectors, and
refusing would have broken the deployed index). That means every index created
before the check existed is still silently swappable: two 384-dim volumes
holding vectors from DIFFERENT models load without error and return confidently
ranked noise.

This stamps them retroactively, so a wrong-model swap refuses to serve instead.

Read/write, metadata only -- no re-embedding, no document writes. NOTHING else
may hold this path open while it runs: chroma persistence is SQLite plus HNSW
binaries that WAL does not govern.

Usage: stamp_manifest.py <persist_dir> <embedding_model> [--query-prefix P] [--dry-run]
"""
import sys

MANIFEST_VERSION = "1"
COLLECTIONS = ["alpha_claude_code", "beta_claude_desktop"]


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if len(args) < 2:
        print(__doc__)
        return 2
    persist_dir, model = args[0], args[1]
    prefix = ""
    if "--query-prefix" in sys.argv:
        prefix = sys.argv[sys.argv.index("--query-prefix") + 1]

    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=persist_dir, settings=Settings(anonymized_telemetry=False)
    )

    manifest = {
        "ag_embedding_model": model,
        "ag_query_prefix": prefix,
        "ag_manifest_version": MANIFEST_VERSION,
    }

    rc = 0
    for name in COLLECTIONS:
        try:
            col = client.get_collection(name)
        except Exception as exc:
            print(f"  {name}: NOT PRESENT ({str(exc)[:80]}) -- skipped")
            continue

        existing = (col.metadata or {}).get("ag_embedding_model")
        if existing == model:
            print(f"  {name}: already stamped {existing!r} -- no change")
            continue
        if existing is not None and existing != model:
            # Refuse rather than relabel: the vectors were produced by a
            # different model and restamping would launder exactly the error
            # the manifest exists to catch.
            print(f"  {name}: REFUSING -- already stamped {existing!r}, asked for {model!r}")
            rc = 1
            continue

        n = col.count()
        if dry:
            print(f"  {name}: WOULD stamp model={model!r} prefix={prefix!r} ({n} docs)")
            continue
        merged = dict(col.metadata or {})
        merged.update(manifest)
        col.modify(metadata=merged)
        print(f"  {name}: stamped model={model!r} ({n} docs)")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
