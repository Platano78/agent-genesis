"""
Embedding generation using bge-small-en-v1.5 (already cached in Docker).

Leverages existing sentence-transformers model for zero-cost embeddings.
"""

import logging
from typing import List, Optional
import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


class EmbeddingGenerator:
    """Generate embeddings using bge-small-en-v1.5."""

    _instance: Optional['EmbeddingGenerator'] = None
    _model: Optional[SentenceTransformer] = None

    def __new__(cls):
        """Singleton pattern - load model once."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize embedding model (cached, loads once)."""
        if self._model is None:
            logger.info("Loading bge-small-en-v1.5 embedding model...")
            try:
                self._model = SentenceTransformer('BAAI/bge-small-en-v1.5')
                logger.info("✅ Embedding model loaded")
            except Exception as e:
                logger.error(f"Failed to load embedding model: {e}")
                raise

    def generate(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for list of texts.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors (384 dimensions each)
        """
        if not texts:
            return []

        try:
            # Generate embeddings (batch processing)
            embeddings = self._model.encode(
                texts,
                normalize_embeddings=True,  # Better for cosine similarity
                show_progress_bar=False
            )

            # Convert to list of lists for ChromaDB
            return embeddings.tolist()

        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            raise

    def generate_single(self, text: str) -> List[float]:
        """Generate embedding for single text."""
        return self.generate([text])[0]

    def get_dimension(self) -> int:
        """Get embedding dimension (384 for bge-small)."""
        return 384


# Global singleton instance
_embedding_generator = None


def get_embedding_generator() -> EmbeddingGenerator:
    """Get global embedding generator instance."""
    global _embedding_generator
    if _embedding_generator is None:
        _embedding_generator = EmbeddingGenerator()
    return _embedding_generator


def test_embeddings():
    """Test embedding generation."""
    gen = get_embedding_generator()

    test_texts = [
        "Empire's Edge uses A* pathfinding",
        "Agent Genesis indexes Claude conversations",
        "MCP servers provide tool integration"
    ]

    print(f"Generating embeddings for {len(test_texts)} texts...")
    embeddings = gen.generate(test_texts)

    print(f"✅ Generated {len(embeddings)} embeddings")
    print(f"   Dimension: {len(embeddings[0])}")
    print(f"   Sample: {embeddings[0][:5]}...")

    return True


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    test_embeddings()
