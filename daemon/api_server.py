"""
Phase 2: API server for semantic search and indexing endpoints.

Provides REST API for:
- Semantic search across conversation history
- Manual indexing triggers
- Collection statistics
"""

import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from typing import Optional

try:
    from daemon.knowledge_db_dual import DualSourceKnowledgeDB
    from daemon.indexer import ConversationIndexer
except ImportError:
    from knowledge_db_dual import DualSourceKnowledgeDB
    from indexer import ConversationIndexer

logger = logging.getLogger(__name__)


class APIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Agent Genesis API."""

    # Class variables to store database and indexer
    db: Optional[DualSourceKnowledgeDB] = None
    indexer: Optional[ConversationIndexer] = None

    def do_POST(self):
        """Handle POST requests."""
        path = urlparse(self.path).path

        if path == '/search':
            self.handle_search()
        elif path == '/index/trigger':
            self.handle_index_trigger()
        else:
            self.send_error(404, "Endpoint not found")

    def do_GET(self):
        """Handle GET requests."""
        path = urlparse(self.path).path

        if path == '/health':
            self.handle_health()
        elif path == '/stats':
            self.handle_stats()
        else:
            self.send_error(404, "Endpoint not found")

    def handle_search(self):
        """
        Handle semantic search request.

        POST /search
        {
            "query": "pathfinding algorithms",
            "n_results": 10,
            "project_filter": "empires_edge",  // optional
            "collections": ["alpha", "beta"]    // optional
        }
        """
        try:
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body)

            query_text = data.get('query')
            if not query_text:
                self.send_json_response({"error": "query parameter required"}, status=400)
                return

            n_results = data.get('n_results', 10)
            project_filter = data.get('project_filter')
            collections = data.get('collections', ["alpha", "beta"])

            # Perform unified search
            results = self.db.query_unified(
                query_text=query_text,
                n_results=n_results,
                collections=collections,
                project_filter=project_filter
            )

            self.send_json_response({
                "query": query_text,
                "results_count": len(results),
                "results": results
            })

        except json.JSONDecodeError:
            self.send_json_response({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.error(f"Search failed: {e}")
            self.send_json_response({"error": str(e)}, status=500)

    def handle_index_trigger(self):
        """
        Handle manual indexing trigger.

        POST /index/trigger
        {
            "sources": ["alpha", "beta"],  // optional
            "enable_mkg": false              // optional
        }
        """
        try:
            # Read request body (optional)
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 0:
                body = self.rfile.read(content_length).decode('utf-8')
                data = json.loads(body)
            else:
                data = {}

            enable_mkg = data.get('enable_mkg', False)

            # Create fresh indexer instance
            indexer = ConversationIndexer(enable_mkg_analysis=enable_mkg)
            stats = indexer.index_all_sources()

            self.send_json_response({
                "status": "complete",
                "stats": {
                    "alpha_indexed": stats["alpha_indexed"],
                    "beta_indexed": stats["beta_indexed"],
                    "total_indexed": stats["total_indexed"],
                    "duration_seconds": stats["duration"]
                }
            })

        except Exception as e:
            logger.error(f"Indexing trigger failed: {e}")
            self.send_json_response({"error": str(e)}, status=500)

    def handle_health(self):
        """Handle health check request with deep validation."""
        health_status = {
            "status": "OK",
            "api": "Agent Genesis Phase 2",
            "endpoints": ["/search", "/index/trigger", "/stats", "/health"],
            "warnings": []
        }
        
        try:
            # Deep health check: verify ChromaDB can actually query
            if self.db:
                import os
                
                # Check collection counts
                alpha_count = self.db.alpha_collection.count()
                beta_count = self.db.beta_collection.count()
                health_status["collections"] = {
                    "alpha": alpha_count,
                    "beta": beta_count,
                    "total": alpha_count + beta_count
                }
                
                # Check disk usage
                persist_dir = self.db.persist_directory
                if os.path.exists(persist_dir):
                    total_size = 0
                    hnsw_size = 0
                    for root, dirs, files in os.walk(persist_dir):
                        for f in files:
                            fpath = os.path.join(root, f)
                            try:
                                size = os.path.getsize(fpath)
                                total_size += size
                                if f == "link_lists.bin":
                                    hnsw_size = size
                            except OSError:
                                pass
                    
                    health_status["disk"] = {
                        "total_mb": round(total_size / (1024 * 1024), 2),
                        "hnsw_mb": round(hnsw_size / (1024 * 1024), 2)
                    }
                    
                    # Warning if HNSW index is suspiciously large (>1GB)
                    if hnsw_size > 1024 * 1024 * 1024:
                        health_status["warnings"].append(
                            f"HNSW index is large: {round(hnsw_size / (1024**3), 2)}GB - possible corruption"
                        )
                        health_status["status"] = "DEGRADED"
                    
                    # Warning if total size > 5GB
                    if total_size > 5 * 1024 * 1024 * 1024:
                        health_status["warnings"].append(
                            f"Database is large: {round(total_size / (1024**3), 2)}GB"
                        )
                
                # Test actual query capability (quick sanity check)
                try:
                    test_results = self.db.alpha_collection.query(
                        query_texts=["test"],
                        n_results=1
                    )
                    health_status["query_test"] = "PASS"
                except Exception as e:
                    health_status["query_test"] = "FAIL"
                    health_status["warnings"].append(f"Query test failed: {str(e)[:100]}")
                    health_status["status"] = "UNHEALTHY"
            
            if not health_status["warnings"]:
                del health_status["warnings"]
                
        except Exception as e:
            health_status["status"] = "UNHEALTHY"
            health_status["error"] = str(e)[:200]
            logger.error(f"Health check failed: {e}")
        
        status_code = 200 if health_status["status"] == "OK" else 503 if health_status["status"] == "UNHEALTHY" else 200
        self.send_json_response(health_status, status=status_code)

    def handle_stats(self):
        """Handle collection statistics request."""
        try:
            stats = self.db.get_collection_stats()
            self.send_json_response(stats)
        except Exception as e:
            logger.error(f"Stats retrieval failed: {e}")
            self.send_json_response({"error": str(e)}, status=500)

    def send_json_response(self, data: dict, status: int = 200):
        """Send JSON response."""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def log_message(self, format, *args):
        """Log HTTP requests."""
        logger.info(f"{self.address_string()} - {format % args}")


def start_api_server(host: str = "0.0.0.0", port: int = 8080):
    """
    Start the API server.

    Args:
        host: Host to bind to
        port: Port to listen on
    """
    # Initialize shared resources
    APIHandler.db = DualSourceKnowledgeDB()
    logger.info("API server initialized with DualSourceKnowledgeDB")

    # Create and start server
    server = HTTPServer((host, port), APIHandler)
    logger.info(f"Agent Genesis API server listening on {host}:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down API server...")
        server.server_close()


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    start_api_server()
