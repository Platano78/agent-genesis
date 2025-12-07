"""
JSONL Parser for Claude Code Conversation Files
Parses JSONL files from ~/.claude/projects/[encoded-path]/[session-id].jsonl
"""

import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime

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
    id: str  # sessionId
    timestamp: datetime
    messages: List[Message]
    project: Optional[str] = None
    cwd: Optional[str] = None
    git_branch: Optional[str] = None

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


def _decode_project_path(encoded_path: str) -> str:
    """
    Decode the project name from encoded path.

    Args:
        encoded_path: Encoded path like "-home-user-project-myproject"

    Returns:
        Decoded project name like "myproject"
    """
    try:
        # Split by dashes and remove empty parts
        parts = [part for part in encoded_path.split('-') if part]

        # Common patterns: look for "project" or take last 2 parts
        if "project" in parts:
            # Find index of "project" and take everything after
            project_index = parts.index("project")
            if project_index + 1 < len(parts):
                # Return everything after "project" joined with dashes
                return '-'.join(parts[project_index + 1:])

        # Fallback: take the last part or last 2 parts joined
        if len(parts) >= 2:
            return '-'.join(parts[-2:])
        elif parts:
            return parts[-1]
        else:
            return encoded_path

    except Exception as e:
        logger.warning(f"Error decoding project path '{encoded_path}': {e}")
        return encoded_path


def _extract_message_content(json_obj: dict) -> str:
    """
    Extract text content from message data.
    Handles both string and list[dict] formats.

    Args:
        json_obj: Message JSON object

    Returns:
        Extracted text content
    """
    try:
        # Get the message field
        message_data = json_obj.get('message', {})

        if not message_data:
            return ""

        content = message_data.get('content', '')

        # Case 1: Content is a string
        if isinstance(content, str):
            return content

        # Case 2: Content is a list of dicts (Claude format)
        elif isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get('type') == 'text':
                        text_parts.append(item.get('text', ''))
                    elif 'text' in item:
                        text_parts.append(item.get('text', ''))
            return '\n'.join(text_parts) if text_parts else ''

        return str(content) if content else ''

    except Exception as e:
        logger.warning(f"Error extracting message content: {e}")
        return ''


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
        # Try ISO format first (2025-10-27T06:04:50.915Z)
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


def parse_jsonl_file(filepath: Path) -> Optional[Conversation]:
    """
    Parse a single JSONL conversation file.

    Args:
        filepath: Path to .jsonl file

    Returns:
        Parsed Conversation object or None if parsing fails
    """
    logger.info(f"Parsing JSONL file: {filepath}")

    if not filepath.exists():
        logger.error(f"File not found: {filepath}")
        return None

    try:
        # Extract project name from parent directory path
        encoded_project_path = filepath.parent.name
        project_name = _decode_project_path(encoded_project_path)

        # Read and parse JSONL file
        messages = []
        session_id = None
        cwd = None
        git_branch = None
        first_timestamp = None

        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    line = line.strip()
                    if not line:
                        continue

                    json_obj = json.loads(line)

                    # Only process user and assistant messages
                    msg_type = json_obj.get('type')
                    if msg_type not in ('user', 'assistant'):
                        continue

                    # Extract session metadata from first valid message
                    if session_id is None:
                        session_id = json_obj.get('sessionId')
                        cwd = json_obj.get('cwd')
                        git_branch = json_obj.get('gitBranch', '')

                    # Extract message content
                    content = _extract_message_content(json_obj)
                    if not content:
                        logger.debug(f"Skipping message with no content on line {line_num}")
                        continue

                    # Parse timestamp
                    timestamp = _parse_timestamp(json_obj.get('timestamp'))
                    if first_timestamp is None:
                        first_timestamp = timestamp

                    # Create message object
                    message = Message(
                        role=msg_type,
                        content=content,
                        timestamp=timestamp
                    )
                    messages.append(message)

                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON on line {line_num} in {filepath}: {e}")
                    continue
                except Exception as e:
                    logger.warning(f"Error processing line {line_num} in {filepath}: {e}")
                    continue

        if not messages:
            logger.warning(f"No valid messages found in {filepath}")
            return None

        # Use session ID or filename as conversation ID
        conversation_id = session_id or filepath.stem

        conversation = Conversation(
            id=conversation_id,
            timestamp=first_timestamp or datetime.now(),
            messages=messages,
            project=project_name,
            cwd=cwd,
            git_branch=git_branch
        )

        logger.info(f"Successfully parsed conversation {conversation_id} with {len(messages)} messages")
        return conversation

    except Exception as e:
        logger.error(f"Failed to parse JSONL file {filepath}: {e}")
        return None


def scan_projects_directory(
    projects_dir: Path,
    project_filter: Optional[str] = None
) -> List[Conversation]:
    """
    Scan all JSONL files in the Claude projects directory.

    Args:
        projects_dir: Path to ~/.claude/projects directory
        project_filter: Optional project name to filter by

    Returns:
        List of parsed Conversation objects
    """
    logger.info(f"Scanning projects directory: {projects_dir}")

    if not projects_dir.exists():
        logger.error(f"Projects directory not found: {projects_dir}")
        return []

    conversations = []

    # Iterate through all encoded project directories
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue

        # Decode project name
        project_name = _decode_project_path(project_dir.name)

        # Apply project filter if specified
        if project_filter and project_filter.lower() not in project_name.lower():
            logger.debug(f"Skipping project {project_name} (filter: {project_filter})")
            continue

        logger.info(f"Scanning project: {project_name} ({project_dir.name})")

        # Find all JSONL files in this project directory
        jsonl_files = list(project_dir.glob("*.jsonl"))
        logger.info(f"Found {len(jsonl_files)} JSONL files in {project_name}")

        for jsonl_file in jsonl_files:
            conversation = parse_jsonl_file(jsonl_file)
            if conversation:
                conversations.append(conversation)

    logger.info(f"Successfully parsed {len(conversations)} conversations from {projects_dir}")
    return conversations


def get_new_conversations(
    projects_dir: Path,
    last_conversation_id: Optional[str] = None,
    project_filter: Optional[str] = None
) -> List[Conversation]:
    """
    Get conversations newer than the last processed ID.

    Args:
        projects_dir: Path to Claude projects directory
        last_conversation_id: ID of last processed conversation
        project_filter: Optional project name filter

    Returns:
        List of new conversations
    """
    all_conversations = scan_projects_directory(projects_dir, project_filter)

    if not last_conversation_id:
        return all_conversations

    # Find index of last processed conversation
    last_idx = -1
    for idx, conv in enumerate(all_conversations):
        if conv.id == last_conversation_id:
            last_idx = idx
            break

    if last_idx == -1:
        # Last ID not found, return all (directory may have changed)
        logger.warning(f"Last conversation ID {last_conversation_id} not found, returning all")
        return all_conversations

    # Return conversations after last processed
    new_conversations = all_conversations[last_idx + 1:]
    logger.info(f"Found {len(new_conversations)} new conversations since {last_conversation_id}")

    return new_conversations
