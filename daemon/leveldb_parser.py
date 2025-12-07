# FILE: daemon/leveldb_parser.py

import json
import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

import plyvel
try:
    from daemon.parser import Conversation, Message
except ImportError:
    from parser import Conversation, Message

logger = logging.getLogger(__name__)

PROJECT_KEYWORDS = {
    "empires_edge": ["empires_edge", "Empires Edge"],
    "shadow_contracts": ["shadow_contracts", "Shadow Contracts"],
    "agent_genesis": ["agent_genesis", "Agent Genesis"],
    "mcp_development": ["mcp_development", "MCP Development"],
    "deepseek_bridge": ["deepseek_bridge", "DeepSeek Bridge"]
}


class LevelDBParser:
    """
    A parser for Claude Desktop's LevelDB storage.
    
    This class provides functionality to extract conversations and messages
    stored in LevelDB by the Claude Desktop application.
    """

    def __init__(self, db_path):
        """
        Initialize the LevelDB parser.

        Args:
            db_path: Path to the LevelDB directory (str or Path)
        """
        self.db_path = Path(db_path) if isinstance(db_path, str) else db_path
        self.db: Optional[plyvel.DB] = None
        self.temp_db_dir: Optional[tempfile.TemporaryDirectory] = None

    def __enter__(self):
        """Context manager entry."""
        try:
            # Check if the directory is writable
            test_file = self.db_path / ".write_test"
            try:
                test_file.touch()
                test_file.unlink()
                writable = True
                logger.debug(f"LevelDB directory is writable: {self.db_path}")
            except (OSError, PermissionError):
                writable = False
                logger.info(f"LevelDB directory is read-only: {self.db_path}")

            # If read-only, copy to temp location
            if not writable:
                logger.info("Copying LevelDB to temporary writable location...")
                self.temp_db_dir = tempfile.TemporaryDirectory()
                temp_path = Path(self.temp_db_dir.name) / "leveldb"

                # Copy database files, excluding LOCK file (may be in use)
                def ignore_lock_file(directory, files):
                    return ['LOCK'] if 'LOCK' in files else []

                shutil.copytree(self.db_path, temp_path, ignore=ignore_lock_file)
                actual_db_path = temp_path
                logger.info(f"Using temporary LevelDB copy at {actual_db_path}")
            else:
                actual_db_path = self.db_path

            # Open the database
            self.db = plyvel.DB(str(actual_db_path), create_if_missing=False)
            logger.debug(f"Opened LevelDB successfully")
        except Exception as e:
            logger.error(f"Failed to open LevelDB at {self.db_path}: {e}")
            # Clean up temp dir if created
            if self.temp_db_dir:
                self.temp_db_dir.cleanup()
                self.temp_db_dir = None
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        if self.db:
            try:
                self.db.close()
                logger.debug("Closed LevelDB connection")
            except Exception as e:
                logger.warning(f"Error closing LevelDB: {e}")

        # Clean up temporary directory if created
        if self.temp_db_dir:
            try:
                self.temp_db_dir.cleanup()
                logger.debug("Cleaned up temporary LevelDB copy")
            except Exception as e:
                logger.warning(f"Error cleaning up temporary directory: {e}")

    def parse_all_conversations(self) -> List[Conversation]:
        """
        Parse all conversations from LevelDB that match the expected pattern.

        Returns:
            List of Conversation objects extracted from LevelDB
        """
        conversations = []
        if not self.db:
            logger.error("Database not initialized")
            return conversations
            
        try:
            prefix = b"LSS-"
            for key, value in self.db.iterator(prefix=prefix):
                try:
                    if b":conversation:messages" in key:
                        conversation = self._parse_conversation(key, value)
                        if conversation:
                            conversations.append(conversation)
                except Exception as e:
                    logger.warning(f"Skipping malformed conversation entry with key {key}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Error iterating through LevelDB: {e}")
            
        logger.info(f"Parsed {len(conversations)} conversations from LevelDB")
        return conversations

    def _parse_conversation(self, key: bytes, value: bytes) -> Optional[Conversation]:
        """
        Parse a single conversation from a LevelDB key-value pair.

        Args:
            key: The LevelDB key (bytes)
            value: The LevelDB value (bytes)

        Returns:
            Conversation object or None if parsing fails
        """
        try:
            key_str = key.decode('utf-8')
            # Extract UUID from key like LSS-<UUID>:conversation:messages
            uuid_part = key_str.split(":")[0].replace("LSS-", "")
            conversation_id = f"desktop_{uuid_part}"
            
            data = json.loads(value.decode('utf-8'))
            messages_data = data.get("messages", [])
            
            if not isinstance(messages_data, list):
                logger.warning(f"Invalid messages format for conversation {conversation_id}")
                return None
                
            messages = []
            for msg_data in messages_data:
                try:
                    content = self._extract_content(msg_data)
                    timestamp = self._parse_timestamp(msg_data.get("timestamp", ""))
                    
                    message = Message(
                        role=msg_data.get("role", "user"),
                        content=content,
                        timestamp=timestamp
                    )
                    messages.append(message)
                except Exception as e:
                    logger.warning(f"Skipping malformed message in conversation {conversation_id}: {e}")
                    continue
                    
            if not messages:
                logger.debug(f"No valid messages found in conversation {conversation_id}")
                return None
                
            project = self._detect_project(messages_data)
            
            # Use first and last message timestamps for created/updated
            created_at = messages[0].timestamp if messages else datetime.now()
            updated_at = messages[-1].timestamp if messages else datetime.now()
            
            title = data.get("title", "Untitled") or "Untitled"
            
            return Conversation(
                id=conversation_id,
                title=title,
                messages=messages,
                project=project,
                created_at=created_at,
                updated_at=updated_at,
                metadata={
                    "source": "claude_desktop",
                    "leveldb_key": key_str
                }
            )
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in conversation data: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error parsing conversation: {e}")
            return None

    def _extract_content(self, msg_data: Dict[str, Any]) -> str:
        """
        Extract content from message data.

        Args:
            msg_data: Dictionary containing message data

        Returns:
            Extracted content as string
        """
        try:
            content = msg_data.get("content", "")
            
            # If content is already a string, return it
            if isinstance(content, str):
                return content
                
            # If content is a list of content blocks
            if isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if isinstance(text, str):
                            texts.append(text)
                        elif isinstance(text, list):  # Handle array of text segments
                            for segment in text:
                                if isinstance(segment, str):
                                    texts.append(segment)
                                elif isinstance(segment, dict) and "text" in segment:
                                    texts.append(str(segment["text"]))
                return "\n".join(texts)
                
            return str(content)
        except Exception as e:
            logger.warning(f"Error extracting content: {e}")
            return ""

    def _parse_timestamp(self, timestamp_str: str) -> datetime:
        """
        Parse timestamp string into datetime object.

        Args:
            timestamp_str: Timestamp string in ISO format

        Returns:
            Parsed datetime or current time if parsing fails
        """
        if not timestamp_str:
            return datetime.now()
            
        try:
            # Try common ISO formats
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except ValueError:
            try:
                # Fallback to datetime parsing with UTC assumption
                return datetime.fromisoformat(timestamp_str)
            except ValueError:
                logger.warning(f"Could not parse timestamp '{timestamp_str}', using current time")
                return datetime.now()

    def _detect_project(self, messages: List[Dict]) -> Optional[str]:
        """
        Detect project based on keywords in message content.

        Args:
            messages: List of message dictionaries

        Returns:
            Project name or None if not detected
        """
        try:
            for message in messages:
                content = self._extract_content(message).lower()
                for project_key, keywords in PROJECT_KEYWORDS.items():
                    for keyword in keywords:
                        if keyword.lower() in content:
                            return project_key
            return None
        except Exception as e:
            logger.warning(f"Error detecting project: {e}")
            return None
