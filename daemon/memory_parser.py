"""Parser for Claude Code per-project memory files.

Parses markdown files from ~/.claude/projects/*/memory/*.md into
Conversation objects for indexing into ChromaDB.
"""

import hashlib
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime

try:
    from daemon.jsonl_parser import Conversation, Message, _decode_project_path
except ImportError:
    from jsonl_parser import Conversation, Message, _decode_project_path

logger = logging.getLogger(__name__)


def parse_memory_file(filepath: Path) -> Optional[Conversation]:
    """Parse a single markdown memory file into a Conversation.

    Args:
        filepath: Path to a .md file under */memory/

    Returns:
        Conversation object or None if parsing fails
    """
    if not filepath.exists() or filepath.stat().st_size == 0:
        return None

    try:
        content = filepath.read_text(encoding="utf-8").strip()
        if not content:
            return None

        # Project name from grandparent: <projects_dir>/<encoded-project>/memory/file.md
        encoded_project = filepath.parent.parent.name
        project_name = _decode_project_path(encoded_project)

        # Stable conversation ID from file path hash
        conv_id = "memory-" + hashlib.md5(str(filepath).encode()).hexdigest()[:12]

        file_mtime = datetime.fromtimestamp(filepath.stat().st_mtime)

        message = Message(
            role="assistant",
            content=content,
            timestamp=file_mtime,
        )

        return Conversation(
            id=conv_id,
            timestamp=file_mtime,
            messages=[message],
            project=project_name,
            source="memory",
        )
    except Exception as exc:
        logger.warning("Failed to parse memory file %s: %s", filepath, exc)
        return None


def scan_memory_files(projects_dir: str) -> List[Path]:
    """Find all memory markdown files under a projects directory.

    Args:
        projects_dir: Root projects directory containing encoded project dirs

    Returns:
        List of Path objects for each memory .md file found
    """
    root = Path(projects_dir)
    if not root.exists():
        return []
    return sorted(root.glob("*/memory/*.md"))
