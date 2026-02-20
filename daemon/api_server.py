"""
Phase 2: API server for semantic search and indexing endpoints.

Provides REST API for:
- Full-text + semantic search across conversation history
- Manual indexing triggers
- Collection statistics
- Health monitoring
"""

import json
import logging
import os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from typing import Optional

try:
    from daemon.knowledge_db_dual import DualSourceKnowledgeDB
    from daemon.indexer import ConversationIndexer
except ImportError:
    from knowledge_db_dual import DualSourceKnowledgeDB
    from indexer import ConversationIndexer

logger = logging.getLogger(__name__)


def _build_endpoints_list() -> list:
    return ["/search", "/index/trigger", "/stats", "/live", "/ready", "/health", "/health/deep"]


def _collect_disk_stats(persist_dir: str) -> tuple:
    if not os.path.exists(persist_dir):
        return None, 0, 0

    total_size = 0
    hnsw_size = 0
    for root, dirs, files in os.walk(persist_dir):
        for f in files:
            fpath = os.path.join(root, f)
            try:
                size = os.path.getsize(fpath)
                total_size += size
                if f in ("data_level0.bin", "link_lists.bin", "length.bin"):
                    hnsw_size += size
            except OSError:
                pass

    disk = {
        "total_mb": round(total_size / (1024 * 1024), 2),
        "hnsw_mb": round(hnsw_size / (1024 * 1024), 2),
    }
    return disk, total_size, hnsw_size


def _run_deep_check(db) -> dict:
    """On-demand deep check — SQLite stats + disk + search test."""
    health_status = {
        "status": "OK",
        "api": "Agent Genesis Phase 2",
        "endpoints": _build_endpoints_list(),
        "warnings": [],
    }

    try:
        if db:
            _col_stats = db.get_collection_stats()
            alpha_count = _col_stats.get("alpha", {}).get("count", 0)
            beta_count = _col_stats.get("beta", {}).get("count", 0)
            health_status["collections"] = {
                "alpha": alpha_count,
                "beta": beta_count,
                "total": alpha_count + beta_count,
            }

            # Run actual search test
            search_test = db.test_search()
            health_status["query_test"] = search_test.get("status", "UNKNOWN")
            if search_test.get("status") != "PASS":
                health_status["warnings"].append(
                    f"Search test failed: {search_test.get('error', 'unknown')}"
                )

            disk, total_size, hnsw_size = _collect_disk_stats(db.persist_directory)
            if disk:
                health_status["disk"] = disk
                if total_size > 5 * 1024 * 1024 * 1024:
                    health_status["warnings"].append(
                        f"Database is large: {round(total_size / (1024**3), 2)}GB"
                    )
        else:
            health_status["status"] = "UNHEALTHY"
            health_status["error"] = "Database is not initialized"

        if not health_status["warnings"]:
            del health_status["warnings"]
    except Exception as e:
        health_status["status"] = "UNHEALTHY"
        health_status["error"] = str(e)[:200]
        logger.error("Deep health check failed: %s", e)

    return health_status


class APIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Agent Genesis API."""

    db: Optional[DualSourceKnowledgeDB] = None
    indexer: Optional[ConversationIndexer] = None

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/search':
            self.handle_search()
        elif path == '/index/trigger':
            self.handle_index_trigger()
        else:
            self.send_error(404, "Endpoint not found")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/live':
            self.handle_live()
        elif path == '/ready':
            self.handle_ready()
        elif path == '/health':
            self.handle_health()
        elif path == '/health/deep':
            self.handle_health_deep()
        elif path == '/stats':
            self.handle_stats()
        else:
            self.send_error(404, "Endpoint not found")

    def handle_search(self):
        """Handle search request (FTS5 + optional vector search)."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body)
            query_text = data.get('query')
            if not query_text:
                self.send_json_response({"error": "query parameter required"}, status=400)
                return
            n_results = data.get('n_results', data.get('limit', 10))
            project_filter = data.get('project_filter', data.get('project'))
            collections = data.get('collections', ["alpha", "beta"])

            result = self.db.query_unified(
                query_text=query_text,
                n_results=n_results,
                collections=collections,
                project_filter=project_filter,
            )

            # Unpack result dict from query_unified
            results_list = result.get("results", [])
            self.send_json_response({
                "query": query_text,
                "results_count": len(results_list),
                "results": results_list,
                "search_type": result.get("search_type", "unknown"),
            })
        except json.JSONDecodeError:
            self.send_json_response({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.error("Search failed: %s", e)
            self.send_json_response({"error": str(e)}, status=500)

    def handle_index_trigger(self):
        """Handle manual indexing trigger."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 0:
                body = self.rfile.read(content_length).decode('utf-8')
                data = json.loads(body)
            else:
                data = {}
            enable_mkg = data.get('enable_mkg', False)
            indexer = ConversationIndexer(enable_mkg_analysis=enable_mkg)
            stats = indexer.index_all_sources()
            self.send_json_response({
                "status": "complete",
                "stats": {
                    "alpha_indexed": stats["alpha_indexed"],
                    "beta_indexed": stats["beta_indexed"],
                    "total_indexed": stats["total_indexed"],
                    "duration_seconds": stats["duration"],
                },
            })
        except Exception as e:
            logger.error("Indexing trigger failed: %s", e)
            self.send_json_response({"error": str(e)}, status=500)

    def handle_live(self):
        """Ultra-cheap liveness check. No DB calls. Always <1ms."""
        self.send_json_response({"status": "ok", "api": "Agent Genesis Phase 2"})

    def handle_ready(self):
        """Readiness check: fast SQLite stats + disk usage."""
        if self.db is None:
            self.send_json_response(
                {
                    "status": "UNHEALTHY",
                    "api": "Agent Genesis Phase 2",
                    "endpoints": _build_endpoints_list(),
                    "error": "Database is not initialized",
                },
                status=503,
            )
            return

        readiness = {
            "status": "OK",
            "api": "Agent Genesis Phase 2",
            "endpoints": _build_endpoints_list(),
            "warnings": [],
        }

        try:
            _col_stats = self.db.get_collection_stats()
            alpha_count = _col_stats.get("alpha", {}).get("count", 0)
            beta_count = _col_stats.get("beta", {}).get("count", 0)
            readiness["collections"] = {
                "alpha": alpha_count,
                "beta": beta_count,
                "total": alpha_count + beta_count,
            }

            disk, total_size, hnsw_size = _collect_disk_stats(self.db.persist_directory)
            if disk:
                readiness["disk"] = disk
                if total_size > 5 * 1024 * 1024 * 1024:
                    readiness["warnings"].append(
                        f"Database is large: {round(total_size / (1024**3), 2)}GB"
                    )

            if not readiness["warnings"]:
                del readiness["warnings"]

            self.send_json_response(readiness)
        except Exception as e:
            logger.error("Readiness check failed: %s", e)
            self.send_json_response(
                {
                    "status": "UNHEALTHY",
                    "api": "Agent Genesis Phase 2",
                    "endpoints": _build_endpoints_list(),
                    "error": str(e)[:200],
                },
                status=503,
            )

    def handle_health(self):
        """Backwards-compatible /health — same as /ready."""
        self.handle_ready()

    def handle_health_deep(self):
        """Run full deep health check including search test."""
        result = _run_deep_check(self.db)
        status_code = 503 if result.get("status") == "UNHEALTHY" else 200
        self.send_json_response(result, status=status_code)

    def handle_stats(self):
        """Handle collection statistics request."""
        try:
            stats = self.db.get_collection_stats()
            self.send_json_response(stats)
        except Exception as e:
            logger.error("Stats retrieval failed: %s", e)
            self.send_json_response({"error": str(e)}, status=500)

    def send_json_response(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def log_message(self, format, *args):
        logger.info("%s - %s", self.address_string(), format % args)


def start_api_server(host: str = "0.0.0.0", port: int = 8080):
    """Start the API server."""
    APIHandler.db = DualSourceKnowledgeDB()
    logger.info("API server initialized with DualSourceKnowledgeDB")

    server = ThreadingHTTPServer((host, port), APIHandler)
    logger.info("Agent Genesis API server listening on %s:%d", host, port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down API server...")
        if APIHandler.db:
            APIHandler.db.shutdown()
        server.server_close()


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    start_api_server()
