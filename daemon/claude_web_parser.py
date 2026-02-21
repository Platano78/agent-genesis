import json
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import hashlib
import logging

logger = logging.getLogger(__name__)

@dataclass
class ParsedMessage:
    id: str
    role: str
    content: str
    timestamp: datetime
    conversation_id: str
    message_hash: str

@dataclass
class ParseMetrics:
    total_conversations: int = 0
    total_messages: int = 0
    failed_conversations: int = 0
    schema_errors: int = 0

class ClaudeWebParser:
    def __init__(self):
        self.metrics = ParseMetrics()

    def parse_zip(self, zip_path: str) -> List[ParsedMessage]:
        """Parse Claude.ai export ZIP file into structured messages"""
        if not Path(zip_path).exists():
            raise FileNotFoundError(f"ZIP file not found: {zip_path}")

        messages = []
        seen_message_ids = set()

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_file:
                # Look for conversations.json in the ZIP
                if 'conversations.json' in zip_file.namelist():
                    with zip_file.open('conversations.json') as f:
                        conversations_data = json.load(f)
                        self.metrics.total_conversations = len(conversations_data)

                        for conv_data in conversations_data:
                            try:
                                parsed_msgs = self._parse_conversation(conv_data, seen_message_ids)
                                messages.extend(parsed_msgs)
                            except Exception as e:
                                logger.warning(f"Failed to parse conversation {conv_data.get('uuid', 'unknown')}: {e}")
                                self.metrics.failed_conversations += 1
                else:
                    raise ValueError("No conversations.json found in ZIP file")

        except zipfile.BadZipFile:
            raise ValueError("Invalid ZIP file format")

        return messages

    def _parse_conversation(self, data: Dict[str, Any], seen_ids: set) -> List[ParsedMessage]:
        """Parse conversation from Claude.ai official export format"""
        messages = []

        # Extract conversation ID
        conv_id = data.get('uuid', 'unknown_conv')

        # Get chat messages array
        chat_messages = data.get('chat_messages', [])

        if not chat_messages:
            self.metrics.schema_errors += 1
            return []

        for raw_msg in chat_messages:
            try:
                parsed = self._parse_message(raw_msg, conv_id, seen_ids)
                if parsed and parsed.id not in seen_ids:
                    messages.append(parsed)
                    seen_ids.add(parsed.id)
                    self.metrics.total_messages += 1
            except Exception as e:
                logger.debug(f"Skipping malformed message: {e}")
                continue

        return messages

    def _parse_message(self, raw_msg: Dict[str, Any], conv_id: str, seen_ids: set) -> Optional[ParsedMessage]:
        """Extract message fields from Claude.ai format"""
        msg_id = raw_msg.get('uuid', 'unknown_msg')
        if msg_id in seen_ids:
            return None

        # Extract role (sender)
        role = raw_msg.get('sender', 'unknown')

        # Extract content - can be in 'text' or 'content' array
        content = raw_msg.get('text', '')

        # If content is empty, try the content array
        if not content and 'content' in raw_msg:
            content_array = raw_msg.get('content', [])
            if content_array and isinstance(content_array, list):
                # Join all text from content blocks
                content = ' '.join([
                    block.get('text', '')
                    for block in content_array
                    if isinstance(block, dict) and block.get('text')
                ])

        # Parse timestamp
        timestamp_str = raw_msg.get('created_at', '')
        timestamp = self._parse_timestamp(timestamp_str)

        # Validate required fields
        if not self._validate_message(content, role):
            return None

        # Generate message hash for deduplication
        message_hash = self._generate_message_hash(content, timestamp, conv_id)

        return ParsedMessage(
            id=msg_id,
            role=role.lower(),
            content=content,
            timestamp=timestamp,
            conversation_id=conv_id,
            message_hash=message_hash
        )

    def _parse_timestamp(self, timestamp_str: str) -> datetime:
        """Parse timestamp with multiple format support"""
        if not timestamp_str:
            return datetime(1970, 1, 1)

        formats = [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d"
        ]

        for fmt in formats:
            try:
                return datetime.strptime(timestamp_str, fmt)
            except ValueError:
                continue

        return datetime(1970, 1, 1)

    def _validate_message(self, content: str, role: str) -> bool:
        """Validate message has required data"""
        return bool(content.strip()) and role.lower() in ['human', 'assistant', 'user', 'ai']

    def _generate_message_hash(self, content: str, timestamp: datetime, conv_id: str) -> str:
        """Generate unique hash for message deduplication"""
        hash_input = f"{content}:{timestamp.isoformat()}:{conv_id}"
        return hashlib.sha256(hash_input.encode()).hexdigest()

    def get_metrics(self) -> ParseMetrics:
        """Return parsing metrics"""
        return self.metrics
