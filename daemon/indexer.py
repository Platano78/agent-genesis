"""
Conversation indexing orchestrator.

Coordinates:
1. Parsing conversations (JSON + LevelDB)
2. Generating embeddings (bge-small)
3. Indexing to ChromaDB (dual collections)
4. MKG analysis (optional enrichment)
"""

import logging
from typing import List, Optional
from pathlib import Path
from datetime import datetime

try:
    # Try relative imports first (when run from parent directory)
    from daemon.parser import parse_claude_json, Conversation
    from daemon.jsonl_parser import scan_projects_directory
    from daemon.leveldb_parser import LevelDBParser
    from daemon.knowledge_db_dual import DualSourceKnowledgeDB
    from daemon.embeddings import get_embedding_generator
    from daemon.mkg_client import MKGClient
except ImportError:
    # Fall back to direct imports (when run from daemon directory)
    from parser import parse_claude_json, Conversation
    from jsonl_parser import scan_projects_directory
    from leveldb_parser import LevelDBParser
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

            logger.info(f"✅ Indexed {indexed} Claude Code conversations to Alpha")
            return indexed

        except Exception as e:
            logger.error(f"Claude Code indexing failed: {e}")
            return 0

    def index_claude_desktop_leveldb(self, leveldb_path: str) -> int:
        """
        Index Claude Desktop LevelDB conversations.

        Args:
            leveldb_path: Path to Claude Desktop leveldb directory

        Returns:
            Number of conversations indexed
        """
        logger.info(f"Indexing Claude Desktop conversations from {leveldb_path}")

        try:
            with LevelDBParser(leveldb_path) as parser:
                conversations = parser.parse_all_conversations()

            logger.info(f"Parsed {len(conversations)} Desktop conversations")

            # Index to Collection Beta
            indexed = 0
            for convo in conversations:
                try:
                    self._index_conversation(convo, collection="beta")
                    indexed += 1
                except Exception as e:
                    logger.warning(f"Failed to index conversation {convo.id}: {e}")

            logger.info(f"✅ Indexed {indexed} Desktop conversations to Beta")
            return indexed

        except Exception as e:
            logger.error(f"Desktop indexing failed: {e}")
            return 0

    def index_claude_projects_jsonl(self, projects_dir: str, project_filter: Optional[str] = None) -> int:
        """
        Index Claude Code JSONL conversations from ~/.claude/projects.

        Args:
            projects_dir: Path to ~/.claude/projects directory
            project_filter: Optional project name filter

        Returns:
            Number of conversations indexed
        """
        logger.info(f"Indexing Claude Code JSONL conversations from {projects_dir}")

        try:
            conversations = scan_projects_directory(Path(projects_dir), project_filter)

            logger.info(f"Parsed {len(conversations)} JSONL conversations")

            # Index to Collection Alpha
            indexed = 0
            for convo in conversations:
                try:
                    self._index_conversation(convo, collection="alpha")
                    indexed += 1
                except Exception as e:
                    logger.warning(f"Failed to index conversation {convo.id}: {e}")

            logger.info(f"✅ Indexed {indexed} JSONL conversations to Alpha")
            return indexed

        except Exception as e:
            logger.error(f"JSONL indexing failed: {e}")
            return 0

    def index_all_sources(
        self,
        projects_dir: str = "/app/data/claude-projects",
        leveldb_path: str = "/app/data/claude-desktop-leveldb",
        project_filter: Optional[str] = None
    ) -> dict:
        """
        Index all conversation sources.

        Args:
            projects_dir: Path to Claude Code projects directory (JSONL files)
            leveldb_path: Path to Claude Desktop LevelDB directory
            project_filter: Optional project name filter for JSONL indexing

        Returns:
            Dict with indexing statistics
        """
        stats = {
            "start_time": datetime.now(),
            "alpha_indexed": 0,
            "beta_indexed": 0,
            "total_indexed": 0,
            "errors": []
        }

        # Index Claude Code JSONL (Alpha) - NEW PRIMARY SOURCE
        if Path(projects_dir).exists():
            try:
                stats["alpha_indexed"] = self.index_claude_projects_jsonl(projects_dir, project_filter)
            except Exception as e:
                stats["errors"].append(f"Alpha JSONL indexing: {e}")
        else:
            logger.warning(f"Projects directory not found: {projects_dir}")

        # Index Claude Desktop (Beta)
        if Path(leveldb_path).exists():
            try:
                stats["beta_indexed"] = self.index_claude_desktop_leveldb(leveldb_path)
            except Exception as e:
                stats["errors"].append(f"Beta indexing: {e}")

        stats["total_indexed"] = stats["alpha_indexed"] + stats["beta_indexed"]
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
    print(f"  Beta (Desktop):         {stats['beta_indexed']} conversations")
    print(f"  Total:                  {stats['total_indexed']} conversations")
    print(f"  Duration:               {stats['duration']:.1f}s")

    if stats['errors']:
        print(f"\nErrors encountered:")
        for error in stats['errors']:
            print(f"  ⚠️  {error}")

    # Show collection stats
    try:
        collection_stats = indexer.db.get_collection_stats()
        print("\nCollection Statistics:")
        alpha_count = collection_stats.get('alpha_claude_code', {}).get('count', 0)
        beta_count = collection_stats.get('beta_claude_desktop', {}).get('count', 0)
        print(f"  Alpha: {alpha_count} documents")
        print(f"  Beta:  {beta_count} documents")
    except Exception as e:
        print(f"\n⚠️  Could not retrieve collection stats: {e}")

    print("\n" + "=" * 50)
    print("✅ INITIAL INDEXING COMPLETE")
    print("=" * 50)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    run_initial_indexing()
