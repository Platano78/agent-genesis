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
        """Handle health check request."""
        self.send_json_response({
            "status": "OK",
            "api": "Agent Genesis Phase 2",
            "endpoints": ["/search", "/index/trigger", "/stats", "/health"]
        })

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
