"""query_unified single-backend-only paths must also be relevance-ranked.

Synthetic inputs only — no production API calls.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from daemon.knowledge_db_dual import DualSourceKnowledgeDB


def _r(id_, distance, conv_id):
    return {
        "id": id_,
        "document": f"doc-{id_}",
        "metadata": {"conversation_id": conv_id},
        "distance": distance,
        "collection": "alpha",
    }


def _non_increasing(values):
    return all(a >= b for a, b in zip(values, values[1:]))


class _FakeFTS:
    def __init__(self, payload):
        self._payload = payload

    def search(self, **kwargs):
        return self._payload


class _UnavailableWorker:
    is_available = False

    def call(self, *args, **kwargs):
        return None


def test_fts_only_path_is_ranked_not_raw():
    db = DualSourceKnowledgeDB.__new__(DualSourceKnowledgeDB)
    db._fts = _FakeFTS({
        "results": [_r("a", 5.0, "c-a"), _r("b", 20.0, "c-b")],
        "total_matches": 2,
        "search_type": "fts5",
    })
    db._worker = _UnavailableWorker()

    result = db.query_unified("query", n_results=10)

    assert [r["id"] for r in result["results"]] == ["b", "a"]
    assert all("relevance" in r for r in result["results"])
    assert _non_increasing([r["relevance"] for r in result["results"]])
