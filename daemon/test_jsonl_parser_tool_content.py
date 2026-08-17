"""Unit tests for Slice A of the tool-content indexing fix.

Verifies _extract_message_content renders tool_use / tool_result / thinking
blocks into searchable text, with AskUserQuestion specially handled, prose
ordered before tool content, and caps enforced. Synthetic fixtures only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from daemon.jsonl_parser import (
    _extract_message_content,
    TOOL_USE_CAP,
    TOOL_RESULT_CAP,
    THINKING_CAP,
    TRUNCATION_MARKER,
)


def _msg(content):
    return {"message": {"content": content}}


def test_ask_user_question_renders_question_and_all_options():
    content = [
        {
            "type": "tool_use",
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "question": "What should we name the repo?",
                        "header": "Repo name",
                        "options": [
                            {"label": "pitstop", "description": "Racing-themed name"},
                            {"label": "dialtone", "description": "Retro phone name"},
                            {"label": "fleet-thing", "description": "Descriptive fleet name"},
                        ],
                    }
                ]
            },
        }
    ]
    result = _extract_message_content(_msg(content))
    assert "What should we name the repo?" in result
    assert "pitstop" in result and "Racing-themed name" in result
    assert "dialtone" in result and "Retro phone name" in result
    assert "fleet-thing" in result and "Descriptive fleet name" in result
    assert result.startswith("[decision]")


def test_tool_result_string_content():
    content = [{"type": "tool_result", "content": "file written ok"}]
    result = _extract_message_content(_msg(content))
    assert result == "[result] file written ok"


def test_tool_result_list_of_blocks_content():
    content = [
        {
            "type": "tool_result",
            "content": [{"type": "text", "text": "42 tests passed"}],
        }
    ]
    result = _extract_message_content(_msg(content))
    assert result == "[result] 42 tests passed"


def test_tool_result_is_error_prefix():
    content = [{"type": "tool_result", "content": "permission denied", "is_error": True}]
    result = _extract_message_content(_msg(content))
    assert result.startswith("[result:error]")
    assert "permission denied" in result


def test_thinking_is_rendered():
    content = [{"type": "thinking", "thinking": "I should check the config first."}]
    result = _extract_message_content(_msg(content))
    assert result == "[thinking] I should check the config first."


def test_thinking_capped_with_marker():
    long_thought = "x" * (THINKING_CAP + 500)
    content = [{"type": "thinking", "thinking": long_thought}]
    result = _extract_message_content(_msg(content))
    assert len(result) == THINKING_CAP
    assert result.endswith(TRUNCATION_MARKER)


def test_tool_use_capped_with_marker():
    content = [
        {
            "type": "tool_use",
            "name": "Write",
            "input": {"content": "y" * (TOOL_USE_CAP + 500), "file_path": "/tmp/x"},
        }
    ]
    result = _extract_message_content(_msg(content))
    assert len(result) == TOOL_USE_CAP
    assert result.endswith(TRUNCATION_MARKER)


def test_tool_result_capped_with_marker():
    content = [{"type": "tool_result", "content": "z" * (TOOL_RESULT_CAP + 500)}]
    result = _extract_message_content(_msg(content))
    assert len(result) == TOOL_RESULT_CAP
    assert result.endswith(TRUNCATION_MARKER)


def test_unrenderable_content_returns_empty_string():
    content = [
        {"type": "tool_use", "name": "", "input": {}},
        {"type": "tool_result", "content": ""},
        {"type": "thinking", "thinking": "   "},
    ]
    result = _extract_message_content(_msg(content))
    assert result == ""


def test_prose_ordered_before_tool_content():
    content = [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/a"}},
        {"type": "text", "text": "Let me check that file."},
    ]
    result = _extract_message_content(_msg(content))
    assert result.index("Let me check that file.") < result.index("[tool: Read]")


def test_generic_tool_use_renders_flat_input_compactly():
    content = [{"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/a.py"}}]
    result = _extract_message_content(_msg(content))
    assert result == "[tool: Read] file_path: /tmp/a.py"
