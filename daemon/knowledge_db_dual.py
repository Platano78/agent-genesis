"""Dual-collection knowledge database for multi-source conversation indexing.

This module provides a ChromaDB-based knowledge database that supports two distinct
data sources (JSON and LevelDB) with unified semantic search capabilities.
"""

import os
import json
import uuid
from typing import List, Dict, Any, Optional, Union
from datetime import datetime
import logging

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
from sentence_transformers import SentenceTransformer

# Set up logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


class DualSourceKnowledgeDB:
    """
    A dual-collection knowledge base system supporting JSON and LevelDB sources,
    indexed in ChromaDB with semantic querying and metadata filtering.
    
    The database maintains two separate collections:
    - alpha_claude_code: For JSON-based conversation sources
    - beta_claude_desktop: For LevelDB-based conversation sources
    
    Features:
    - Automatic source detection from metadata
    - Unified semantic search across both collections
    - Per-message indexing with rich metadata
    - Collection statistics and monitoring
    - Distance-based result ranking
    """

    def __init__(
        self,
        persist_directory: str = "/app/knowledge",
        embedding_model_name: str = "all-MiniLM-L6-v2"
    ) -> None:
        """
        Initialize the knowledge database with two collections:
        - alpha_claude_code (JSON)
        - beta_claude_desktop (LevelDB)

        Args:
            persist_directory: Path where ChromaDB will store its data.
            embedding_model_name: Name or path to a sentence-transformers model.
                                 Default is 'all-MiniLM-L6-v2' for speed/size balance.
        
        Raises:
            RuntimeError: If embedding model cannot be loaded or ChromaDB initialization fails.
        """
        self.persist_directory = persist_directory
        self.embedding_model_name = embedding_model_name
        
        try:
            # Initialize embedding model
            logger.info(f"Loading embedding model: {embedding_model_name}")
            self.embedder = SentenceTransformer(embedding_model_name)
            
            # Ensure persist directory exists
            os.makedirs(persist_directory, exist_ok=True)
            
            # Configure Chroma client
            logger.info(f"Initializing ChromaDB at {persist_directory}")
            self.client = chromadb.PersistentClient(
                path=persist_directory,
                settings=Settings(anonymized_telemetry=False)
            )

            # Create or get collections
            self.alpha_collection = self.client.get_or_create_collection(
                name="alpha_claude_code",
                embedding_function=self._get_embedding_function()
            )
            self.beta_collection = self.client.get_or_create_collection(
                name="beta_claude_desktop",
                embedding_function=self._get_embedding_function()
            )
            
            logger.info("DualSourceKnowledgeDB initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize DualSourceKnowledgeDB: {e}")
            raise RuntimeError(f"Database initialization failed: {e}") from e

    def _get_embedding_function(self) -> embedding_functions.SentenceTransformerEmbeddingFunction:
        """
        Return the embedding function compatible with ChromaDB.
        
        Returns:
            SentenceTransformerEmbeddingFunction configured with the model.
        """
        return embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=self.embedding_model_name
        )

    def index_conversation(
        self, 
        conversation: Dict[str, Any], 
        collection: str = "auto"
    ) -> None:
        """
        Index each message in a conversation into the appropriate collection.
        
        The conversation dictionary should have the following structure:
        {
            "id": "conversation_id",
            "messages": [
                {
                    "role": "user" or "assistant",
                    "content": "message text",
                    "timestamp": "ISO 8601 timestamp"
                },
                ...
            ],
            "metadata": {
                "source": "json" or "leveldb",
                "project": "project_name"
            }
        }

        Args:
            conversation: Dictionary containing messages and metadata.
            collection: Target collection ("alpha", "beta", or "auto").
                       If "auto", detects from metadata.source field.
                       "json" source -> alpha collection
                       "leveldb" source -> beta collection
        
        Raises:
            ValueError: If auto detection fails or invalid collection specified.
            RuntimeError: If indexing operation fails.
        """
        try:
            # Handle both dataclass and dict conversation objects
            from dataclasses import is_dataclass, asdict

            if is_dataclass(conversation):
                # Convert dataclass to dict for easier access
                conv_dict = asdict(conversation)
            else:
                conv_dict = conversation

            # Auto-detect collection from metadata.source
            if collection == "auto":
                source = conv_dict.get("metadata", {}).get("source")
                if not source:
                    raise ValueError(
                        "Missing 'source' field in metadata required for auto-detection. "
                        "Expected metadata.source to be 'json' or 'leveldb'."
                    )

                collection_map = {
                    "json": "alpha",
                    "leveldb": "beta"
                }
                detected_collection = collection_map.get(source.lower())

                if not detected_collection:
                    raise ValueError(
                        f"Unknown source '{source}' for auto-detection. "
                        f"Expected 'json' or 'leveldb'."
                    )

                collection = detected_collection
                logger.debug(f"Auto-detected collection: {collection} from source: {source}")

            # Validate collection name
            if collection not in ["alpha", "beta"]:
                raise ValueError(
                    f"Invalid collection '{collection}'. Must be 'alpha', 'beta', or 'auto'."
                )

            # Select target collection
            target_collection = (
                self.alpha_collection if collection == "alpha"
                else self.beta_collection
            )

            # Extract messages and prepare for indexing
            messages = conv_dict.get("messages", [])
            if not messages:
                logger.warning("No messages found in conversation, skipping indexing")
                return

            metadatas: List[Dict[str, Any]] = []
            documents: List[str] = []
            ids: List[str] = []

            conversation_id = conv_dict.get("id", str(uuid.uuid4()))
            # Build metadata from conversation attributes
            conversation_metadata = {
                "project": conv_dict.get("project", ""),
                "source": "jsonl",  # Mark as coming from JSONL files
                "cwd": conv_dict.get("cwd", ""),
                "git_branch": conv_dict.get("git_branch", "")
            }

            for msg in messages:
                # Handle both dataclass Message and dict
                if is_dataclass(msg):
                    msg_dict = asdict(msg)
                else:
                    msg_dict = msg

                content = msg_dict.get("content", "")
                if not content or not content.strip():
                    logger.debug("Skipping empty message")
                    continue  # Skip empty messages

                # Handle timestamp - convert datetime to ISO string if needed
                timestamp = msg_dict.get("timestamp", datetime.utcnow())
                if isinstance(timestamp, datetime):
                    timestamp_str = timestamp.isoformat()
                else:
                    timestamp_str = str(timestamp)

                # Build metadata for each message
                metadata = {
                    "conversation_id": conversation_id,
                    "role": msg_dict.get("role", "unknown"),
                    "timestamp": timestamp_str,
                    "project": conversation_metadata.get("project", ""),
                    "source": conversation_metadata.get("source", ""),
                    "cwd": conversation_metadata.get("cwd", ""),
                    "git_branch": conversation_metadata.get("git_branch", "")
                }

                documents.append(content)
                metadatas.append(metadata)
                ids.append(str(uuid.uuid4()))

            # Index all messages in batch
            if documents:
                target_collection.add(
                    documents=documents, 
                    metadatas=metadatas, 
                    ids=ids
                )
                logger.info(
                    f"Indexed {len(documents)} messages from conversation {conversation_id} "
                    f"into {collection} collection"
                )
            else:
                logger.warning(
                    f"No valid messages to index in conversation {conversation_id}"
                )

        except ValueError as e:
            logger.error(f"Validation error during conversation indexing: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to index conversation: {e}")
            raise RuntimeError(f"Indexing operation failed: {e}") from e

    def query_unified(
        self,
        query_text: str,
        n_results: int = 10,
        collections: List[str] = ["alpha", "beta"],
        project_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Query both collections and merge results sorted by distance (similarity).
        
        This method performs semantic search across the specified collections,
        merges results, and returns them sorted by distance (lower = more similar).

        Args:
            query_text: Text to search for semantically similar entries.
            n_results: Number of top results to return after merging and sorting.
            collections: Which collections to include in the query.
                        Options: ["alpha", "beta"] or any subset.
            project_filter: Optional filter for project field in metadata.
                           Only returns results matching this project.

        Returns:
            Dictionary with structure:
            {
                "results": [
                    {
                        "id": "uuid",
                        "document": "message content",
                        "metadata": {
                            "conversation_id": "...",
                            "role": "...",
                            "timestamp": "...",
                            "project": "...",
                            "source": "..."
                        },
                        "distance": 0.123,
                        "collection": "alpha" or "beta"
                    },
                    ...
                ],
                "total_matches": 42
            }
        
        Raises:
            ValueError: If query_text is empty.
            RuntimeError: If query operation fails.
        """
        try:
            if not query_text or not query_text.strip():
                raise ValueError("query_text cannot be empty")
            
            results: List[Dict[str, Any]] = []
            
            # Build metadata filter
            filters: Dict[str, Any] = {}
            if project_filter:
                filters["project"] = project_filter
                logger.debug(f"Applying project filter: {project_filter}")

            # Map collection names to objects
            valid_collections = {
                "alpha": self.alpha_collection, 
                "beta": self.beta_collection
            }

            # Query each requested collection
            for col_name in collections:
                if col_name not in valid_collections:
                    logger.warning(
                        f"Ignoring unknown collection '{col_name}'. "
                        f"Valid options: {list(valid_collections.keys())}"
                    )
                    continue

                collection = valid_collections[col_name]
                
                logger.debug(f"Querying collection: {col_name}")
                res = collection.query(
                    query_texts=[query_text],
                    n_results=n_results,
                    where=filters if filters else None
                )

                # Unpack results and add collection name
                for i in range(len(res['ids'][0])):
                    results.append({
                        "id": res['ids'][0][i],
                        "document": res['documents'][0][i],
                        "metadata": res['metadatas'][0][i],
                        "distance": res['distances'][0][i],
                        "collection": col_name
                    })

            # Sort all results by distance ascending (lower distance = more similar)
            results.sort(key=lambda x: x["distance"])
            
            total_matches = len(results)
            top_results = results[:n_results]
            
            logger.info(
                f"Query returned {total_matches} total matches, "
                f"returning top {len(top_results)}"
            )

            return {
                "results": top_results,
                "total_matches": total_matches
            }
            
        except ValueError as e:
            logger.error(f"Validation error during query: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to execute unified query: {e}")
            raise RuntimeError(f"Query operation failed: {e}") from e

    def get_collection_stats(self) -> Dict[str, Any]:
        """
        Get detailed statistics about both collections.
        
        Provides information about:
        - Total message count per collection
        - Unique sources per collection
        - Unique projects per collection

        Returns:
            Dictionary mapping collection names to their stats:
            {
                "alpha": {
                    "count": 42,
                    "sources": ["json"],
                    "projects": ["project1", "project2"]
                },
                "beta": {
                    "count": 128,
                    "sources": ["leveldb"],
                    "projects": ["project3"]
                },
                "total": {
                    "count": 170,
                    "sources": ["json", "leveldb"],
                    "projects": ["project1", "project2", "project3"]
                }
            }
        
        Raises:
            RuntimeError: If stats collection fails.
        """
        try:
            stats: Dict[str, Any] = {}
            all_sources = set()
            all_projects = set()
            total_count = 0

            # Collect stats for each collection
            for name, collection in [
                ("alpha", self.alpha_collection), 
                ("beta", self.beta_collection)
            ]:
                logger.debug(f"Collecting stats for {name} collection")
                
                count = collection.count()
                total_count += count
                
                if count > 0:
                    # Get all metadata to extract unique values
                    all_metadata = collection.get(include=["metadatas"])["metadatas"]
                    
                    sources = list(set(
                        md.get("source") 
                        for md in all_metadata 
                        if md.get("source")
                    ))
                    
                    projects = list(set(
                        md.get("project") 
                        for md in all_metadata 
                        if md.get("project")
                    ))
                    
                    all_sources.update(sources)
                    all_projects.update(projects)
                else:
                    sources = []
                    projects = []

                stats[name] = {
                    "count": count,
                    "sources": sorted(sources),
                    "projects": sorted(projects)
                }

            # Add total stats
            stats["total"] = {
                "count": total_count,
                "sources": sorted(list(all_sources)),
                "projects": sorted(list(all_projects))
            }
            
            logger.info(f"Retrieved stats for all collections: {total_count} total messages")
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get collection stats: {e}")
            raise RuntimeError(f"Stats collection failed: {e}") from e

    def get_stats(self) -> Dict[str, Any]:
        """
        Backward-compatible alias for get_collection_stats().

        Returns the same structure as get_collection_stats() for compatibility
        with existing code that expects the old KnowledgeDB API.

        Returns:
            Dictionary with collection statistics.
        """
        return self.get_collection_stats()


# Example usage and testing
if __name__ == "__main__":
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    
    # Initialize database
    db = DualSourceKnowledgeDB(persist_directory="./knowledge_db_test")
    
    # Example: Index JSON-based conversation
    conversation_json = {
        "id": "conv_001",
        "messages": [
            {
                "role": "user", 
                "content": "How do I deploy this application?", 
                "timestamp": "2025-04-05T10:00:00Z"
            },
            {
                "role": "assistant", 
                "content": "You can deploy using Docker containers. First, build the image, then run it.", 
                "timestamp": "2025-04-05T10:01:00Z"
            }
        ],
        "metadata": {
            "source": "json",
            "project": "deploy-tools"
        }
    }
    
    # Example: Index LevelDB-based conversation
    conversation_leveldb = {
        "id": "conv_002",
        "messages": [
            {
                "role": "user", 
                "content": "What's my desktop configuration?", 
                "timestamp": "2025-04-05T10:05:00Z"
            },
            {
                "role": "assistant", 
                "content": "Your desktop config is stored locally at ~/.config/desktop.", 
                "timestamp": "2025-04-05T10:06:00Z"
            }
        ],
        "metadata": {
            "source": "leveldb",
            "project": "desktop-agent"
        }
    }
    
    # Index conversations
    print("\n=== Indexing Conversations ===")
    db.index_conversation(conversation_json, collection="auto")
    db.index_conversation(conversation_leveldb, collection="auto")
    
    # Get stats
    print("\n=== Collection Statistics ===")
    stats = db.get_collection_stats()
    print(json.dumps(stats, indent=2))
    
    # Perform unified query
    print("\n=== Unified Query ===")
    results = db.query_unified(
        query_text="how can I deploy applications?",
        n_results=5,
        collections=["alpha", "beta"]
    )
    print(json.dumps(results, indent=2))
    
    # Query with project filter
    print("\n=== Query with Project Filter ===")
    results_filtered = db.query_unified(
        query_text="configuration settings",
        n_results=3,
        collections=["beta"],
        project_filter="desktop-agent"
    )
    print(json.dumps(results_filtered, indent=2))
