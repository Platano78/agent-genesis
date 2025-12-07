"""File watcher for monitoring Claude conversation history changes."""

import logging
import time
from pathlib import Path
from typing import Callable, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent

logger = logging.getLogger(__name__)


class ClaudeHistoryWatcher:
    """Watches Claude JSON file for modifications with debouncing."""

    def __init__(
        self,
        filepath: Path,
        callback: Callable[[], None],
        debounce_seconds: float = 2.0
    ):
        """
        Initialize file watcher.

        Args:
            filepath: Path to Claude JSON file to watch
            callback: Function to call when file is modified
            debounce_seconds: Wait time after last write before triggering callback
        """
        self.filepath = filepath.resolve()
        self.callback = callback
        self.debounce_seconds = debounce_seconds

        self._observer: Optional[Observer] = None
        self._last_modified_time = 0.0
        self._pending_callback = False

        logger.info(f"Initialized watcher for {self.filepath}")

    def start(self) -> None:
        """Start watching the file."""
        if not self.filepath.parent.exists():
            raise FileNotFoundError(f"Parent directory does not exist: {self.filepath.parent}")

        event_handler = _FileModifiedHandler(self.filepath, self._on_file_modified)

        self._observer = Observer()
        self._observer.schedule(event_handler, str(self.filepath.parent), recursive=False)
        self._observer.start()

        logger.info("File watcher started")

    def stop(self) -> None:
        """Stop watching the file."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            logger.info("File watcher stopped")

    def _on_file_modified(self) -> None:
        """Handle file modification with debouncing."""
        current_time = time.time()
        self._last_modified_time = current_time
        self._pending_callback = True

        logger.debug(f"File modified at {current_time}, starting debounce timer")

    def check_debounce(self) -> None:
        """
        Check if debounce period has elapsed and trigger callback if needed.

        Should be called periodically from main loop.
        """
        if not self._pending_callback:
            return

        current_time = time.time()
        time_since_last_modification = current_time - self._last_modified_time

        if time_since_last_modification >= self.debounce_seconds:
            logger.info("Debounce period elapsed, triggering callback")
            self._pending_callback = False
            try:
                self.callback()
            except Exception as e:
                logger.error(f"Error in file modification callback: {e}", exc_info=True)


class _FileModifiedHandler(FileSystemEventHandler):
    """Internal handler for file system events."""

    def __init__(self, target_path: Path, callback: Callable[[], None]):
        self.target_path = target_path
        self.callback = callback

    def on_modified(self, event: FileModifiedEvent) -> None:
        """Handle file modification events."""
        if event.is_directory:
            return

        event_path = Path(event.src_path).resolve()

        # Only trigger for our target file
        if event_path == self.target_path:
            self.callback()


class CheckpointManager:
    """Manages checkpoint file for tracking last processed conversation."""

    def __init__(self, checkpoint_path: Path):
        """
        Initialize checkpoint manager.

        Args:
            checkpoint_path: Path to checkpoint file
        """
        self.checkpoint_path = checkpoint_path
        logger.info(f"Checkpoint file: {self.checkpoint_path}")

    def get_last_conversation_id(self) -> Optional[str]:
        """
        Read last processed conversation ID from checkpoint.

        Returns:
            Last conversation ID, or None if no checkpoint exists
        """
        if not self.checkpoint_path.exists():
            logger.info("No checkpoint file found, starting from beginning")
            return None

        try:
            with open(self.checkpoint_path, 'r') as f:
                conversation_id = f.read().strip()
                logger.info(f"Loaded checkpoint: {conversation_id}")
                return conversation_id if conversation_id else None
        except Exception as e:
            logger.error(f"Error reading checkpoint: {e}")
            return None

    def save_conversation_id(self, conversation_id: str) -> None:
        """
        Save last processed conversation ID to checkpoint.

        Args:
            conversation_id: ID to save
        """
        try:
            # Ensure parent directory exists
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.checkpoint_path, 'w') as f:
                f.write(conversation_id)

            logger.debug(f"Saved checkpoint: {conversation_id}")
        except Exception as e:
            logger.error(f"Error writing checkpoint: {e}")
