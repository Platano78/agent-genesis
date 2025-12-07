"""Conversation parser for Claude JSON history."""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """Individual message in a conversation."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime


@dataclass
class Conversation:
    """Parsed conversation from Claude history."""
    id: str
    timestamp: datetime
    messages: List[Message] = field(default_factory=list)
    project: Optional[str] = None

    def message_count(self) -> int:
        """Get total message count."""
        return len(self.messages)

    def has_decisions(self) -> bool:
        """Heuristic: Check if conversation contains decision-making keywords."""
        decision_keywords = [
            "decided", "chose", "selected", "opted for",
            "because", "rationale", "reasoning", "approach"
        ]
        combined_text = " ".join(msg.content.lower() for msg in self.messages)
        return any(keyword in combined_text for keyword in decision_keywords)


def _detect_project(messages: List[Message]) -> Optional[str]:
    """
    Attempt to detect project context from file paths in conversation.

    Args:
        messages: List of messages to analyze

    Returns:
        Project name if detected, None otherwise
    """
    import re

    # Look for common path patterns in messages
    path_pattern = r'/(?:home|mnt|app)/[^/]+/(?:project|workspace)/([^/\s]+)'

    for msg in messages:
        matches = re.findall(path_pattern, msg.content)
        if matches:
            # Return first detected project directory
            return matches[0]

    return None


def parse_claude_json(filepath: Path) -> List[Conversation]:
    """
    Parse Claude conversation history JSON file.

    Args:
        filepath: Path to ~/.claude.json file

    Returns:
        List of parsed Conversation objects

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If file contains invalid JSON
    """
    logger.info(f"Parsing Claude JSON from {filepath}")

    if not filepath.exists():
        raise FileNotFoundError(f"Claude history file not found: {filepath}")

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON: {e}")
        raise

    conversations = []

    # Handle different possible JSON structures
    # Assuming format: {"conversations": [{"id": ..., "messages": [...]}]}
    # Adjust based on actual ~/.claude.json structure

    conv_list = raw_data if isinstance(raw_data, list) else raw_data.get("conversations", [])

    for conv_data in conv_list:
        try:
            # Skip incomplete conversations (missing required fields)
            if not conv_data.get("id") or not conv_data.get("messages"):
                logger.debug(f"Skipping incomplete conversation: {conv_data.get('id', 'unknown')}")
                continue

            messages = []
            for msg_data in conv_data.get("messages", []):
                # Parse timestamp - handle different formats
                timestamp = _parse_timestamp(msg_data.get("timestamp"))

                message = Message(
                    role=msg_data.get("role", "unknown"),
                    content=msg_data.get("content", ""),
                    timestamp=timestamp
                )
                messages.append(message)

            if not messages:
                logger.debug(f"Skipping conversation with no messages: {conv_data['id']}")
                continue

            # Use first message timestamp as conversation timestamp
            conv_timestamp = messages[0].timestamp

            # Detect project context
            project = conv_data.get("project") or _detect_project(messages)

            conversation = Conversation(
                id=conv_data["id"],
                timestamp=conv_timestamp,
                messages=messages,
                project=project
            )

            conversations.append(conversation)

        except Exception as e:
            logger.warning(f"Failed to parse conversation {conv_data.get('id', 'unknown')}: {e}")
            continue

    logger.info(f"Successfully parsed {len(conversations)} conversations")
    return conversations


def _parse_timestamp(timestamp_str: Optional[str]) -> datetime:
    """
    Parse timestamp from various formats.

    Args:
        timestamp_str: Timestamp string (ISO format, epoch, etc.)

    Returns:
        Parsed datetime object, defaults to current time if parsing fails
    """
    if not timestamp_str:
        return datetime.now()

    try:
        # Try ISO format first
        return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        pass

    try:
        # Try epoch timestamp
        return datetime.fromtimestamp(float(timestamp_str))
    except (ValueError, TypeError):
        pass

    # Fallback to current time
    logger.warning(f"Failed to parse timestamp '{timestamp_str}', using current time")
    return datetime.now()


def get_new_conversations(
    filepath: Path,
    last_conversation_id: Optional[str] = None
) -> List[Conversation]:
    """
    Get conversations newer than the last processed ID.

    Args:
        filepath: Path to Claude JSON file
        last_conversation_id: ID of last processed conversation

    Returns:
        List of new conversations
    """
    all_conversations = parse_claude_json(filepath)

    if not last_conversation_id:
        return all_conversations

    # Find index of last processed conversation
    last_idx = -1
    for idx, conv in enumerate(all_conversations):
        if conv.id == last_conversation_id:
            last_idx = idx
            break

    if last_idx == -1:
        # Last ID not found, return all (file may have been regenerated)
        logger.warning(f"Last conversation ID {last_conversation_id} not found, returning all")
        return all_conversations

    # Return conversations after last processed
    new_conversations = all_conversations[last_idx + 1:]
    logger.info(f"Found {len(new_conversations)} new conversations since {last_conversation_id}")

    return new_conversations
