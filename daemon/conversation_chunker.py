"""Conversation Chunker — turn-pair chunking for ChromaDB indexing.

Converts a conversation dict (with messages list) into embeddable chunks:
  - Anchor chunk: first user message + metadata prefix, carries full_text
  - Turn-pair chunks: user question paired with assistant response

Designed for all-MiniLM-L6-v2 (256 token max sequence, ~920 chars).
"""

import hashlib
from datetime import datetime
from typing import List, Tuple

MAX_CHUNK_CHARS = 920
MAX_FULL_TEXT_CHARS = 10_000
MAX_PREVIEW_CHARS = 500
MAX_TURN_PARTS = 6  # bound the split-instead-of-truncate explosion per turn
TRUNCATION_MARKER = "…[truncated]"


def _split_turn_doc(full_doc: str, turn_idx: int, user_text: str) -> List[str]:
    """Split an oversized turn document into <= MAX_TURN_PARTS chunk-sized parts.

    Each part repeats a short context header (turn index + a topic snippet
    from the user's message) so a fragment retrieved on its own still means
    something — see the v1.3.0 design note on contextless fragments.
    """
    topic = user_text.strip().replace("\n", " ")[:80]
    if len(user_text.strip()) > 80:
        topic += "…"
    header = f"[Turn {turn_idx} continued — topic: {topic}]\n"
    budget = MAX_CHUNK_CHARS - len(header)
    if budget <= 0:
        header = ""
        budget = MAX_CHUNK_CHARS

    parts = []
    pos = 0
    doc_len = len(full_doc)
    while pos < doc_len and len(parts) < MAX_TURN_PARTS:
        parts.append(header + full_doc[pos:pos + budget])
        pos += budget

    if pos < doc_len:
        # More content remains beyond the part cap — mark the last emitted
        # part as truncated rather than silently dropping the marker.
        available = MAX_CHUNK_CHARS - len(TRUNCATION_MARKER)
        last = parts[-1][:available] if len(parts[-1]) > available else parts[-1]
        parts[-1] = last + TRUNCATION_MARKER

    return parts


def _group_turn_pairs(messages: list) -> List[Tuple[str, str]]:
    """Group filtered messages into (user_text, assistant_text) tuples.

    Consecutive same-role messages are concatenated.
    """
    results = []
    current_user = ""
    current_assistant = ""

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user":
            if current_user and current_assistant:
                results.append((current_user, current_assistant))
                current_user = ""
                current_assistant = ""
            if current_user:
                current_user += "\n" + content
            else:
                current_user = content
        elif role == "assistant":
            if current_assistant:
                current_assistant += "\n" + content
            else:
                current_assistant = content

    if current_user and current_assistant:
        results.append((current_user, current_assistant))

    return results


def _format_timestamp(ts) -> str:
    """Convert timestamp to ISO string, handling both datetime and str."""
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts) if ts else ""


def chunk_conversation(conv: dict, conv_meta: dict) -> List[dict]:
    """Split a conversation into anchor + turn-pair chunks for ChromaDB.

    Args:
        conv: Conversation dict with keys: id, messages, project, etc.
        conv_meta: Dict with project, source, cwd, git_branch.

    Returns:
        List of {"doc_id": str, "document": str, "metadata": dict}
    """
    chunks = []
    messages = conv.get("messages", [])
    conversation_id = conv.get("id", "")

    if not messages:
        return []

    filtered = [
        msg for msg in messages
        if msg.get("role", "") in ("user", "assistant")
        and msg.get("content", "").strip()
    ]
    if not filtered:
        return []

    # Build full_text for the anchor
    full_text_parts = []
    for msg in filtered:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        full_text_parts.append(f"[{role}] {content}")
    full_text = "\n".join(full_text_parts)
    if len(full_text) > MAX_FULL_TEXT_CHARS:
        full_text = full_text[:MAX_FULL_TEXT_CHARS] + "\n... [truncated]"

    ts_str = _format_timestamp(filtered[0].get("timestamp", ""))

    # Find first user message for anchor
    first_user_content = ""
    for msg in filtered:
        if msg.get("role") == "user":
            first_user_content = msg.get("content", "")
            break
    if not first_user_content:
        first_user_content = filtered[0].get("content", "")

    # is_sidechain/parent_session_id/agent_file live on the Conversation
    # dataclass itself (conv), not conv_meta — conv_meta is assembled
    # upstream in chroma_worker.py from a fixed key whitelist that doesn't
    # include them, so pulling from conv keeps chroma_worker.py untouched.
    base_meta = {
        "conversation_id": conversation_id,
        "conversation_timestamp": ts_str,
        "project": conv_meta.get("project", ""),
        "source": conv_meta.get("source", "jsonl"),
        "cwd": conv_meta.get("cwd", ""),
        "git_branch": conv_meta.get("git_branch", ""),
        "message_count": len(filtered),
        "is_sidechain": int(conv.get("is_sidechain") or 0),
        "parent_session_id": conv.get("parent_session_id") or conversation_id,
        "agent_file": conv.get("agent_file") or "",
    }

    # --- Anchor chunk ---
    project = conv_meta.get("project", "")
    branch = conv_meta.get("git_branch", "")
    header = f"Project: {project}" if project else ""
    if branch:
        header += f" | Branch: {branch}" if header else f"Branch: {branch}"
    header += f" | Messages: {len(filtered)}"

    anchor_text = f"{header}\n\n{first_user_content[:800]}"
    anchor_id = hashlib.sha256(
        f"{conversation_id}:anchor".encode()
    ).hexdigest()[:36]

    anchor_meta = dict(base_meta)
    anchor_meta["chunk_type"] = "anchor"
    anchor_meta["chunk_index"] = 0
    anchor_meta["full_text"] = full_text
    anchor_meta["user_message_preview"] = first_user_content[:MAX_PREVIEW_CHARS]

    chunks.append({
        "doc_id": anchor_id,
        "document": anchor_text,
        "metadata": anchor_meta,
    })

    # --- Turn-pair chunks ---
    turn_pairs = _group_turn_pairs(filtered)
    for turn_idx, (user_text, assistant_text) in enumerate(turn_pairs):
        full_turn_doc = f"User: {user_text}\nAssistant: {assistant_text}"
        if len(full_turn_doc) <= MAX_CHUNK_CHARS:
            turn_doc_parts = [full_turn_doc]
        else:
            # Split rather than truncate: truncation would silently evict
            # whichever content lands past the cap (tool output ballooned
            # by Slice A) instead of losing only the declared overflow.
            turn_doc_parts = _split_turn_doc(full_turn_doc, turn_idx, user_text)

        for part_idx, turn_doc in enumerate(turn_doc_parts):
            if part_idx == 0:
                turn_id = hashlib.sha256(
                    f"{conversation_id}:turn:{turn_idx}".encode()
                ).hexdigest()[:36]
            else:
                turn_id = hashlib.sha256(
                    f"{conversation_id}:turn:{turn_idx}:{part_idx}".encode()
                ).hexdigest()[:36]

            turn_meta = dict(base_meta)
            turn_meta["chunk_type"] = "turn"
            turn_meta["chunk_index"] = turn_idx + 1
            turn_meta["turn_index"] = turn_idx
            turn_meta["part_index"] = part_idx

            chunks.append({
                "doc_id": turn_id,
                "document": turn_doc,
                "metadata": turn_meta,
            })

    return chunks
