"""Conversation indexing orchestrator.

Coordinates:
1. Parsing conversations (JSON + Anthropic export)
2. Generating embeddings (bge-small)
3. Indexing to ChromaDB (dual collections)
4. MKG analysis (optional enrichment)
"""

import glob
import hashlib
import json
import logging
import os
import time
from collections import defaultdict
from typing import List, Optional
from pathlib import Path
from datetime import datetime

try:
    # Try relative imports first (when run from parent directory)
    from daemon.parser import parse_claude_json, Conversation, Message
    from daemon.jsonl_parser import scan_projects_directory, parse_jsonl_file
    from daemon.memory_parser import parse_memory_file, scan_memory_files
    from daemon.claude_web_parser import ClaudeWebParser
    from daemon.knowledge_db_dual import DualSourceKnowledgeDB
    from daemon.embeddings import get_embedding_generator
    from daemon.mkg_client import MKGClient
except ImportError:
    # Fall back to direct imports (when run from daemon directory)
    from parser import parse_claude_json, Conversation, Message
    from jsonl_parser import scan_projects_directory, parse_jsonl_file
    from memory_parser import parse_memory_file, scan_memory_files
    from claude_web_parser import ClaudeWebParser
    from knowledge_db_dual import DualSourceKnowledgeDB
    from embeddings import get_embedding_generator
    from mkg_client import MKGClient

logger = logging.getLogger(__name__)


class ConversationIndexer:
    """Orchestrates conversation indexing pipeline."""

    def __init__(
        self,
        db_path: str = "/app/knowledge",
        enable_mkg_analysis: bool = False
    ):
        """
        Initialize indexer.

        Args:
            db_path: ChromaDB persistence directory
            enable_mkg_analysis: Whether to enrich with MKG analysis
        """
        self.db = DualSourceKnowledgeDB(persist_directory=db_path)
        self.embedding_gen = get_embedding_generator()
        self.mkg_client = MKGClient() if enable_mkg_analysis else None
        self.enable_mkg = enable_mkg_analysis

        logger.info("Indexer initialized")

    def index_claude_code_json(self, json_path: str) -> int:
        """
        Index Claude Code JSON conversations.

        Args:
            json_path: Path to .claude.json file

        Returns:
            Number of conversations indexed
        """
        logger.info(f"Indexing Claude Code conversations from {json_path}")

        try:
            conversations = parse_claude_json(Path(json_path))

            logger.info(f"Parsed {len(conversations)} Claude Code conversations")

            # Index to Collection Alpha
            indexed = 0
            for convo in conversations:
                try:
                    self._index_conversation(convo, collection="alpha")
                    indexed += 1
                except Exception as e:
                    logger.warning(f"Failed to index conversation {convo.id}: {e}")

            logger.info(f"Indexed {indexed} Claude Code conversations to Alpha")
            return indexed

        except Exception as e:
            logger.error(f"Claude Code indexing failed: {e}")
            return 0

    def index_anthropic_export(self, exports_dir: str = "/app/data/exports") -> int:
        """
        Index Claude.ai web conversations from Anthropic data export ZIP.

        Auto-detects newest data-*.zip in exports_dir. Uses MD5 hash to skip
        re-parsing unchanged exports. Self-heals after ChromaDB purges by
        detecting empty Beta collection.

        Args:
            exports_dir: Directory containing data-*.zip export files

        Returns:
            Number of conversations indexed
        """
        state_path = Path(self.db.persist_directory) / "beta_import_state.json"

        # Auto-detect newest export ZIP
        zip_pattern = str(Path(exports_dir) / "data-*.zip")
        zip_files = sorted(glob.glob(zip_pattern))
        if not zip_files:
            logger.warning(f"No data-*.zip files found in {exports_dir}")
            return 0

        zip_path = zip_files[-1]  # newest by filename timestamp sort
        logger.info(f"Found export: {zip_path}")

        # Compute MD5 hash of the ZIP
        current_md5 = self._md5_file(zip_path)

        # Check if Beta collection is empty (self-healing after purge)
        beta_empty = self._is_beta_empty()

        # Hash-based skip: if same file already indexed and Beta not empty, skip
        if state_path.exists() and not beta_empty:
            try:
                with open(state_path, 'r') as f:
                    state = json.load(f)
                if state.get("md5") == current_md5:
                    logger.info(
                        f"Export unchanged (MD5 match), Beta has data. "
                        f"Skipping. Last indexed: {state.get('indexed_at', 'unknown')}"
                    )
                    return 0
            except (json.JSONDecodeError, KeyError):
                pass  # Corrupt state file, proceed with reimport

        if beta_empty:
            logger.info("Beta collection empty \u2014 forcing reimport (self-healing)")

        # Parse the export ZIP
        logger.info(f"Parsing Anthropic export: {zip_path}")
        parser = ClaudeWebParser()
        messages = parser.parse_zip(zip_path)
        metrics = parser.get_metrics()

        logger.info(
            f"Parsed {metrics.total_messages} messages from "
            f"{metrics.total_conversations} conversations "
            f"({metrics.failed_conversations} failed, {metrics.schema_errors} schema errors)"
        )

        # Group messages by conversation_id (pattern from import_to_container.py)
        conversations_dict = defaultdict(list)
        for msg in messages:
            conversations_dict[msg.conversation_id].append(msg)

        # Convert to Conversation objects and index to Beta
        indexed = 0
        total_messages = 0
        for conv_id, conv_messages in conversations_dict.items():
            try:
                # Build Conversation dataclass matching what _index_conversation expects
                msgs = [
                    Message(
                        role=m.role,
                        content=m.content,
                        timestamp=m.timestamp
                    )
                    for m in sorted(conv_messages, key=lambda x: x.timestamp)
                ]
                conversation = Conversation(
                    id=conv_id,
                    timestamp=msgs[0].timestamp if msgs else datetime.now(),
                    messages=msgs,
                    project="claude-web-import"
                )
                self._index_conversation(conversation, collection="beta")
                indexed += 1
                total_messages += len(msgs)
                if indexed % 100 == 0:
                    logger.info(f"  Progress: {indexed}/{len(conversations_dict)} conversations")
            except Exception as e:
                logger.warning(f"Failed to index conversation {conv_id}: {e}")

        logger.info(f"Indexed {indexed} web conversations ({total_messages} messages) to Beta")

        # Write state file for hash-based skip on next run
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(state_path, 'w') as f:
                json.dump({
                    "last_file": Path(zip_path).name,
                    "md5": current_md5,
                    "indexed_at": datetime.now().isoformat(),
                    "conversations": indexed,
                    "messages": total_messages
                }, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to write beta import state: {e}")

        return indexed

    def _md5_file(self, filepath: str) -> str:
        """Compute MD5 hash of a file."""
        md5 = hashlib.md5()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5.update(chunk)
        return md5.hexdigest()

    def _is_beta_empty(self) -> bool:
        """Check if Beta collection has zero documents."""
        try:
            stats = self.db.get_collection_stats()
            beta_stats = stats.get('beta', {})
            return beta_stats.get('count', 0) == 0
        except Exception:
            return True  # If we can't check, assume empty and reimport

    def _load_index_manifest(self) -> dict:
        """Load the manifest tracking which files have been indexed and their mtimes."""
        manifest_path = Path(self.db.persist_directory) / "alpha_index_manifest.json"
        if manifest_path.exists():
            try:
                with open(manifest_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_index_manifest(self, manifest: dict) -> None:
        """Save the index manifest after successful indexing."""
        manifest_path = Path(self.db.persist_directory) / "alpha_index_manifest.json"
        try:
            with open(manifest_path, 'w') as f:
                json.dump(manifest, f)
        except OSError as exc:
            logger.warning("Failed to save index manifest: %s", exc)

    def index_claude_projects_jsonl(self, projects_dir: str, project_filter: Optional[str] = None) -> int:
        """Index Claude Code JSONL conversations, skipping unchanged files."""
        logger.info(f"Indexing Claude Code JSONL conversations from {projects_dir}")
        try:
            manifest = self._load_index_manifest()
            projects_path = Path(projects_dir)
            if not projects_path.exists():
                logger.error(f"Projects directory not found: {projects_dir}")
                return 0

            # Collect JSONL files that are new or modified since last indexing
            files_to_parse = []
            for project_dir in sorted(projects_path.iterdir()):
                if not project_dir.is_dir():
                    continue
                for jsonl_file in project_dir.glob("*.jsonl"):
                    file_key = str(jsonl_file)
                    file_mtime = jsonl_file.stat().st_mtime
                    if file_key in manifest and manifest[file_key] >= file_mtime:
                        continue  # Unchanged since last indexing
                    files_to_parse.append((jsonl_file, file_mtime))

            logger.info(f"Found {len(files_to_parse)} new/modified JSONL files (skipped {len(manifest)} unchanged)")

            if not files_to_parse:
                return 0

            indexed = 0
            for jsonl_file, file_mtime in files_to_parse:
                try:
                    conversation = parse_jsonl_file(jsonl_file)
                    if conversation:
                        self._index_conversation(conversation, collection="alpha")
                        indexed += 1
                        manifest[str(jsonl_file)] = file_mtime
                except Exception as e:
                    logger.warning(f"Failed to index {jsonl_file}: {e}")

            self._save_index_manifest(manifest)
            logger.info(f"Indexed {indexed} JSONL conversations to Alpha")
            return indexed
        except Exception as e:
            logger.error(f"JSONL indexing failed: {e}")
            return 0

    def index_memory_files(self, projects_dir: str) -> int:
        """Index Claude Code memory markdown files, skipping unchanged."""
        logger.info("Indexing memory files from %s", projects_dir)
        try:
            manifest = self._load_index_manifest()
            memory_files = scan_memory_files(projects_dir)
            files_to_parse = [
                (mf, mf.stat().st_mtime) for mf in memory_files
                if str(mf) not in manifest or manifest[str(mf)] < mf.stat().st_mtime
            ]
            logger.info("Found %d new/modified memory files", len(files_to_parse))
            if not files_to_parse:
                return 0
            indexed = 0
            for mf, mtime in files_to_parse:
                try:
                    conv = parse_memory_file(mf)
                    if conv:
                        self._index_conversation(conv, collection="alpha")
                        indexed += 1
                        manifest[str(mf)] = mtime
                except Exception as exc:
                    logger.warning("Failed to index memory file %s: %s", mf, exc)
            self._save_index_manifest(manifest)
            logger.info("Indexed %d memory files to Alpha", indexed)
            return indexed
        except Exception as exc:
            logger.error("Memory file indexing failed: %s", exc)
            return 0

    def index_all_sources(
        self,
        projects_dir: str = "/app/data/claude-projects",
        exports_dir: str = "/app/data/exports",
        project_filter: Optional[str] = None
    ) -> dict:
        """
        Index all conversation sources.

        Args:
            projects_dir: Path to Claude Code projects directory (JSONL files)
            exports_dir: Path to Anthropic data export directory (ZIP files)
            project_filter: Optional project name filter for JSONL indexing

        Returns:
            Dict with indexing statistics
        """
        stats = {
            "start_time": datetime.now(),
            "alpha_indexed": 0,
            "memory_indexed": 0,
            "beta_indexed": 0,
            "total_indexed": 0,
            "errors": []
        }

        # Index Claude Code JSONL (Alpha) - PRIMARY SOURCE
        if Path(projects_dir).exists():
            try:
                stats["alpha_indexed"] = self.index_claude_projects_jsonl(projects_dir, project_filter)
            except Exception as e:
                stats["errors"].append(f"Alpha JSONL indexing: {e}")
        else:
            logger.warning(f"Projects directory not found: {projects_dir}")

        # Index Claude Code memory files (Alpha)
        if Path(projects_dir).exists():
            try:
                stats["memory_indexed"] = self.index_memory_files(projects_dir)
            except Exception as e:
                stats["errors"].append(f"Memory file indexing: {e}")

        # Index Anthropic web export (Beta)
        if Path(exports_dir).exists():
            try:
                stats["beta_indexed"] = self.index_anthropic_export(exports_dir)
            except Exception as e:
                stats["errors"].append(f"Beta export indexing: {e}")
        else:
            logger.warning(f"Exports directory not found: {exports_dir}")

        stats["total_indexed"] = stats["alpha_indexed"] + stats["memory_indexed"] + stats["beta_indexed"]
        stats["end_time"] = datetime.now()
        stats["duration"] = (stats["end_time"] - stats["start_time"]).total_seconds()

        logger.info(f"Indexing complete: {stats['total_indexed']} total conversations")
        return stats

    def _index_conversation(self, conversation: Conversation, collection: str):
        """
        Index single conversation with embeddings and optional MKG analysis.

        Args:
            conversation: Conversation object to index
            collection: Target collection ("alpha" or "beta")
        """
        # Optional: Enrich with MKG analysis
        if self.enable_mkg and len(conversation.messages) > 0:
            try:
                message_texts = [msg.content for msg in conversation.messages]
                full_text = "\n\n".join(message_texts)
                summary = self.mkg_client.generate_summary(full_text, max_length=150)

                # Add to conversation metadata
                if not hasattr(conversation, 'metadata'):
                    conversation.metadata = {}
                conversation.metadata['mkg_summary'] = summary
            except Exception as e:
                logger.debug(f"MKG enrichment skipped: {e}")

        # Index to ChromaDB
        self.db.index_conversation(conversation, collection=collection)


def run_initial_indexing():
    """Run initial full history indexing job."""
    print("=" * 50)
    print("AGENT GENESIS - INITIAL INDEXING")
    print("=" * 50)
    print()

    # Initialize indexer (MKG analysis disabled for speed)
    indexer = ConversationIndexer(enable_mkg_analysis=False)

    # Run indexing
    stats = indexer.index_all_sources()

    # Print results
    print("\nIndexing Results:")
    print(f"  Alpha (Claude Code):    {stats['alpha_indexed']} conversations")
    print(f"  Memory files:           {stats['memory_indexed']} files")
    print(f"  Beta (Web Export):      {stats['beta_indexed']} conversations")
    print(f"  Total:                  {stats['total_indexed']} conversations")
    print(f"  Duration:               {stats['duration']:.1f}s")

    if stats['errors']:
        print(f"\nErrors encountered:")
        for error in stats['errors']:
            print(f"  {error}")

    # Show collection stats
    try:
        collection_stats = indexer.db.get_collection_stats()
        print("\nCollection Statistics:")
        alpha_count = collection_stats.get('alpha', {}).get('count', 0)
        beta_count = collection_stats.get('beta', {}).get('count', 0)
        print(f"  Alpha: {alpha_count} documents")
        print(f"  Beta:  {beta_count} documents")
    except Exception as e:
        print(f"\n  Could not retrieve collection stats: {e}")

    print("\n" + "=" * 50)
    print("INITIAL INDEXING COMPLETE")
    print("=" * 50)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    run_initial_indexing()
