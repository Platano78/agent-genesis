"""Main entry point for Agent Genesis daemon."""

import json
import logging
import signal
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Optional

import yaml

from daemon.parser import parse_claude_json, get_new_conversations, Conversation
from daemon.watcher import ClaudeHistoryWatcher, CheckpointManager
from daemon.knowledge_db_dual import DualSourceKnowledgeDB as KnowledgeDB

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class HealthCheckHandler(BaseHTTPRequestHandler):
    """HTTP handler for health check endpoint."""

    # Class variable to store stats
    stats: dict = {}

    def do_GET(self):
        """Handle GET requests."""
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()

            response = {
                'status': 'OK',
                'stats': self.stats
            }

            self.wfile.write(json.dumps(response).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass


class AgentGenesis:
    """Main daemon orchestrator."""

    def __init__(self, config_path: Path):
        """
        Initialize Agent Genesis daemon.

        Args:
            config_path: Path to configuration YAML
        """
        logger.info("Initializing Agent Genesis...")

        # Load configuration
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        # Initialize components
        self.claude_file = Path(self.config['file_watcher']['path'])
        self.checkpoint_file = Path(self.config['file_watcher']['checkpoint_file'])
        self.check_interval = self.config['file_watcher']['check_interval']

        # Initialize knowledge database
        db_path = Path(self.config['chromadb']['path'])
        # Note: DualSourceKnowledgeDB creates alpha_claude_code and beta_claude_desktop collections
        self.knowledge_db = KnowledgeDB(persist_directory=str(db_path))

        # Initialize checkpoint manager
        self.checkpoint_manager = CheckpointManager(self.checkpoint_file)

        # Initialize file watcher
        self.watcher = ClaudeHistoryWatcher(
            filepath=self.claude_file,
            callback=self._on_file_changed,
            debounce_seconds=self.check_interval
        )

        # Health check server
        self.health_server: Optional[HTTPServer] = None
        self.health_thread: Optional[Thread] = None

        # Stats tracking
        self.stats = {
            'conversations_processed': 0,
            'last_update': None,
            'started_at': time.time()
        }

        # Running flag
        self.running = False

        logger.info("Agent Genesis initialized")

    def _on_file_changed(self) -> None:
        """Callback when Claude JSON file is modified."""
        logger.info("Claude history file changed, processing new conversations...")

        try:
            # Get last processed conversation ID
            last_id = self.checkpoint_manager.get_last_conversation_id()

            # Get new conversations
            new_conversations = get_new_conversations(self.claude_file, last_id)

            if not new_conversations:
                logger.info("No new conversations to process")
                return

            # Process each new conversation
            for conv in new_conversations:
                self._process_conversation(conv)

            # Update checkpoint
            if new_conversations:
                latest_id = new_conversations[-1].id
                self.checkpoint_manager.save_conversation_id(latest_id)

                # Update stats
                self.stats['conversations_processed'] += len(new_conversations)
                self.stats['last_update'] = time.time()

            logger.info(f"Processed {len(new_conversations)} new conversations")

        except Exception as e:
            logger.error(f"Error processing file changes: {e}", exc_info=True)

    def _process_conversation(self, conversation: Conversation) -> None:
        """
        Process a single conversation and store in knowledge DB.

        Args:
            conversation: Parsed conversation object
        """
        # Skip if already exists
        if self.knowledge_db.conversation_exists(conversation.id):
            logger.debug(f"Conversation {conversation.id} already exists, skipping")
            return

        # Concatenate all messages for storage
        content = "\n\n".join([
            f"[{msg.role.upper()}]: {msg.content}"
            for msg in conversation.messages
        ])

        # Prepare metadata
        metadata = {
            "conversation_id": conversation.id,
            "timestamp": int(conversation.timestamp.timestamp()),
            "project": conversation.project or "unknown",
            "has_decisions": conversation.has_decisions(),
            "message_count": conversation.message_count()
        }

        # Store in knowledge DB (without embeddings for Phase 1)
        self.knowledge_db.store_conversation(
            conversation_id=conversation.id,
            content=content,
            metadata=metadata,
            embedding=None  # Will add embedding generation in Phase 2
        )

        logger.debug(f"Stored conversation {conversation.id} ({conversation.message_count()} messages)")

    def _start_health_server(self) -> None:
        """Start health check HTTP server in background thread."""
        host = self.config['health']['host']
        port = self.config['health']['port']

        # Update handler with current stats
        HealthCheckHandler.stats = self._get_health_stats()

        self.health_server = HTTPServer((host, port), HealthCheckHandler)

        def serve():
            logger.info(f"Health check server listening on {host}:{port}")
            while self.running:
                # Update stats before handling request
                HealthCheckHandler.stats = self._get_health_stats()
                self.health_server.handle_request()

        self.health_thread = Thread(target=serve, daemon=True)
        self.health_thread.start()

    def _get_health_stats(self) -> dict:
        """Get current health statistics."""
        db_stats = self.knowledge_db.get_stats()

        return {
            'conversations_processed': self.stats['conversations_processed'],
            'last_update': self.stats['last_update'],
            'uptime_seconds': int(time.time() - self.stats['started_at']),
            'db_stats': db_stats
        }

    def _initial_sync(self) -> None:
        """Perform initial synchronization of existing conversations."""
        logger.info("Performing initial synchronization...")

        try:
            last_id = self.checkpoint_manager.get_last_conversation_id()
            new_conversations = get_new_conversations(self.claude_file, last_id)

            logger.info(f"Found {len(new_conversations)} conversations to sync")

            for conv in new_conversations:
                self._process_conversation(conv)

            if new_conversations:
                latest_id = new_conversations[-1].id
                self.checkpoint_manager.save_conversation_id(latest_id)
                self.stats['conversations_processed'] += len(new_conversations)

            logger.info("Initial synchronization complete")

        except Exception as e:
            logger.error(f"Error during initial sync: {e}", exc_info=True)

    def start(self) -> None:
        """Start the daemon."""
        logger.info("Starting Agent Genesis daemon...")

        self.running = True

        # Perform initial sync
        self._initial_sync()

        # Start file watcher
        self.watcher.start()

        # Start health check server
        self._start_health_server()

        logger.info("Agent Genesis daemon started")

        # Main loop
        try:
            while self.running:
                # Check debounce status
                self.watcher.check_debounce()

                # Sleep to avoid busy waiting
                time.sleep(0.5)

        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
            self.stop()

    def stop(self) -> None:
        """Stop the daemon."""
        logger.info("Stopping Agent Genesis daemon...")

        self.running = False

        # Stop file watcher
        self.watcher.stop()

        # Stop health server
        if self.health_server:
            self.health_server.server_close()

        logger.info("Agent Genesis daemon stopped")


def main():
    """Main entry point."""
    # Handle signals for graceful shutdown
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"

    daemon = AgentGenesis(config_path)

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}")
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    daemon.start()


if __name__ == "__main__":
    main()
