"""Unit tests for Slice B of the tool-content indexing fix.

Verifies chunk_conversation splits oversized turns into multiple parts
instead of truncating, preserving part 0's doc_id and capping part count.
"""

import sys
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from daemon.conversation_chunker import chunk_conversation, MAX_CHUNK_CHARS, MAX_TURN_PARTS


def _conv(messages, conv_id="conv1"):
    return {"id": conv_id, "messages": messages}, {
        "project": "p", "source": "jsonl", "cwd": "/tmp", "git_branch": "main"
    }


def test_oversized_turn_splits_into_multiple_parts():
    long_assistant = "a" * (MAX_CHUNK_CHARS * 4)
    messages = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": long_assistant},
    ]
    conv, meta = _conv(messages)
    chunks = chunk_conversation(conv, meta)
    turn_chunks = [c for c in chunks if c["metadata"]["chunk_type"] == "turn"]
    assert len(turn_chunks) > 1
    for c in turn_chunks:
        assert len(c["document"]) <= MAX_CHUNK_CHARS
        assert c["metadata"]["turn_index"] == 0


def test_part_zero_doc_id_matches_pre_change_scheme():
    long_assistant = "b" * (MAX_CHUNK_CHARS * 3)
    messages = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": long_assistant},
    ]
    conv, meta = _conv(messages, conv_id="convX")
    chunks = chunk_conversation(conv, meta)
    turn_chunks = [c for c in chunks if c["metadata"]["chunk_type"] == "turn"]
    part0 = next(c for c in turn_chunks if c["metadata"]["part_index"] == 0)
    expected_id = hashlib.sha256("convX:turn:0".encode()).hexdigest()[:36]
    assert part0["doc_id"] == expected_id

    part1 = next(c for c in turn_chunks if c["metadata"]["part_index"] == 1)
    expected_id1 = hashlib.sha256("convX:turn:0:1".encode()).hexdigest()[:36]
    assert part1["doc_id"] == expected_id1


def test_part_count_capped_at_six():
    huge_assistant = "c" * (MAX_CHUNK_CHARS * 20)
    messages = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": huge_assistant},
    ]
    conv, meta = _conv(messages)
    chunks = chunk_conversation(conv, meta)
    turn_chunks = [c for c in chunks if c["metadata"]["chunk_type"] == "turn"]
    assert len(turn_chunks) == MAX_TURN_PARTS
    assert turn_chunks[-1]["document"].endswith("…[truncated]")


def test_normal_sized_turn_not_split():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello there"},
    ]
    conv, meta = _conv(messages)
    chunks = chunk_conversation(conv, meta)
    turn_chunks = [c for c in chunks if c["metadata"]["chunk_type"] == "turn"]
    assert len(turn_chunks) == 1
    assert turn_chunks[0]["metadata"]["part_index"] == 0
