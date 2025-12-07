"""ChromaDB wrapper for storing conversation knowledge."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)


class KnowledgeDB:
    """ChromaDB wrapper for conversation storage and retrieval."""

    def __init__(self, db_path: Path, collection_name: str = "conversations"):
        """
        Initialize ChromaDB client and collection.

        Args:
            db_path: Path to ChromaDB persistent storage
            collection_name: Name of the collection to use
        """
        self.db_path = db_path
        self.collection_name = collection_name

        # Ensure directory exists
        db_path.mkdir(parents=True, exist_ok=True)

        # Initialize ChromaDB client with persistent storage
        logger.info(f"Initializing ChromaDB at {db_path}")
        self.client = chromadb.Client(Settings(
            chroma_db_impl="duckdb+parquet",
            persist_directory=str(db_path),
            anonymized_telemetry=False
        ))

        # Get or create collection
        try:
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                metadata={"description": "Claude conversation history"}
            )
            logger.info(f"Collection '{collection_name}' initialized with {self.collection.count()} documents")
        except Exception as e:
            logger.error(f"Failed to initialize collection: {e}")
            raise

    def store_conversation(
        self,
        conversation_id: str,
        content: str,
        metadata: Dict[str, Any],
        embedding: Optional[List[float]] = None
    ) -> None:
        """
        Store a conversation in ChromaDB.

        Args:
            conversation_id: Unique conversation identifier
            content: Text content to store (concatenated messages)
            metadata: Metadata dict with conversation details
            embedding: Pre-computed embedding vector (optional for Phase 1)
        """
        try:
            # For Phase 1, we store without embeddings (will add in Phase 2)
            # ChromaDB will auto-generate embeddings if not provided
            self.collection.add(
                ids=[conversation_id],
                documents=[content],
                metadatas=[metadata],
                embeddings=[embedding] if embedding else None
            )

            logger.debug(f"Stored conversation {conversation_id}")

        except Exception as e:
            logger.error(f"Failed to store conversation {conversation_id}: {e}")
            raise

    def get_latest_conversations(self, n: int = 5) -> List[Dict[str, Any]]:
        """
        Retrieve the N most recent conversations.

        Args:
            n: Number of conversations to retrieve

        Returns:
            List of conversation dicts with id, content, metadata
        """
        try:
            # Get all documents (we'll filter by timestamp)
            results = self.collection.get()

            if not results or not results['ids']:
                return []

            # Combine results into dicts
            conversations = []
            for i, conv_id in enumerate(results['ids']):
                conversations.append({
                    'id': conv_id,
                    'content': results['documents'][i] if results['documents'] else '',
                    'metadata': results['metadatas'][i] if results['metadatas'] else {}
                })

            # Sort by timestamp (descending)
            conversations.sort(
                key=lambda x: x['metadata'].get('timestamp', 0),
                reverse=True
            )

            return conversations[:n]

        except Exception as e:
            logger.error(f"Failed to get latest conversations: {e}")
            return []

    def query(
        self,
        query_embedding: List[float],
        n: int = 5,
        filter_metadata: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Query conversations by embedding similarity.

        Args:
            query_embedding: Query vector
            n: Number of results to return
            filter_metadata: Optional metadata filter (e.g., {"project": "my-project"})

        Returns:
            List of matching conversations with similarity scores
        """
        try:
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=n,
                where=filter_metadata
            )

            if not results or not results['ids']:
                return []

            # Format results
            conversations = []
            for i, conv_id in enumerate(results['ids'][0]):  # query returns nested lists
                conversations.append({
                    'id': conv_id,
                    'content': results['documents'][0][i] if results['documents'] else '',
                    'metadata': results['metadatas'][0][i] if results['metadatas'] else {},
                    'distance': results['distances'][0][i] if results['distances'] else None
                })

            return conversations

        except Exception as e:
            logger.error(f"Failed to query conversations: {e}")
            return []

    def conversation_exists(self, conversation_id: str) -> bool:
        """
        Check if a conversation already exists in the database.

        Args:
            conversation_id: ID to check

        Returns:
            True if exists, False otherwise
        """
        try:
            result = self.collection.get(ids=[conversation_id])
            return len(result['ids']) > 0
        except Exception as e:
            logger.error(f"Error checking conversation existence: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """
        Get database statistics.

        Returns:
            Dict with count, last_update, etc.
        """
        try:
            count = self.collection.count()

            # Get latest conversation timestamp
            latest = self.get_latest_conversations(n=1)
            last_update = None
            if latest:
                last_update = latest[0]['metadata'].get('timestamp')
                if last_update:
                    last_update = datetime.fromtimestamp(last_update).isoformat()

            return {
                'total_conversations': count,
                'last_update': last_update,
                'collection_name': self.collection_name
            }

        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {'error': str(e)}

    def delete_conversation(self, conversation_id: str) -> None:
        """
        Delete a conversation from the database.

        Args:
            conversation_id: ID to delete
        """
        try:
            self.collection.delete(ids=[conversation_id])
            logger.debug(f"Deleted conversation {conversation_id}")
        except Exception as e:
            logger.error(f"Failed to delete conversation {conversation_id}: {e}")
            raise

    def reset(self) -> None:
        """Delete all conversations (use with caution!)."""
        try:
            self.client.delete_collection(name=self.collection_name)
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "Claude conversation history"}
            )
            logger.warning("Knowledge database reset")
        except Exception as e:
            logger.error(f"Failed to reset database: {e}")
            raise
