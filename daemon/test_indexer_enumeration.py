"""Regression test for JSONL enumeration fault-isolation.

Covers two indexer defects found while staging a rebuild against the real
corpus: a dangling symlink raised ENOENT during file *enumeration* (before
per-file parsing, which was already isolated) and aborted indexing of every
file in the run; the resulting failure was then swallowed into a `return 0`
that reported success. No network or real corpus used.
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# sentence_transformers only ships in the container image, not this dev
# environment; stub it so importing daemon.indexer doesn't require it.
if "sentence_transformers" not in sys.modules:
    stub = types.ModuleType("sentence_transformers")
    stub.SentenceTransformer = object
    sys.modules["sentence_transformers"] = stub

from daemon.indexer import ConversationIndexer


class _StubDB:
    def __init__(self, persist_directory):
        self.persist_directory = persist_directory

    def index_conversation(self, conversation, collection):
        pass


def _make_indexer(persist_dir):
    indexer = ConversationIndexer.__new__(ConversationIndexer)
    indexer.db = _StubDB(persist_dir)
    indexer.enable_mkg = False
    indexer.mkg_client = None
    return indexer


def test_enumeration_survives_dangling_symlink(tmp_path, monkeypatch):
    projects_dir = tmp_path / "claude-projects"
    project_dir = projects_dir / "-home-platano-project-demo"
    project_dir.mkdir(parents=True)

    good1 = project_dir / "session-one.jsonl"
    good1.write_text('{"type": "user"}\n')
    good2 = project_dir / "session-two.jsonl"
    good2.write_text('{"type": "user"}\n')

    dangling = project_dir / "session-missing.jsonl"
    dangling.symlink_to(tmp_path / "does-not-exist.jsonl")

    seen_files = []

    def fake_parse_jsonl_file(filepath, project_dir=None):
        seen_files.append(Path(filepath).name)
        conversation = type("Conversation", (), {"id": filepath.name, "messages": []})()
        return conversation

    monkeypatch.setattr("daemon.indexer.parse_jsonl_file", fake_parse_jsonl_file)

    persist_dir = tmp_path / "knowledge"
    persist_dir.mkdir()
    indexer = _make_indexer(str(persist_dir))

    indexed = indexer.index_claude_projects_jsonl(str(projects_dir))

    assert indexed == 2
    assert set(seen_files) == {"session-one.jsonl", "session-two.jsonl"}
    assert dangling.name not in seen_files
