"""Unit tests for the RRF result-ranking fix in knowledge_db_dual.py.

Synthetic inputs only — no production API calls.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from daemon.knowledge_db_dual import merge_ranked_results


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


def test_fts_only_gets_relevance_and_sorts():
    # FTS: larger distance = better. Deliberately out of order.
    fts = [_r("a", 5.0, "c-a"), _r("b", 20.0, "c-b"), _r("c", 10.0, "c-c")]
    merged = merge_ranked_results(fts_results=fts)
    assert [r["id"] for r in merged] == ["b", "c", "a"]
    assert _non_increasing([r["relevance"] for r in merged])
    assert merged[0]["relevance"] == pytest.approx(1.0)
    assert merged[0]["distance"] == 20.0  # distance preserved, unchanged


def test_vector_only_gets_relevance_and_sorts():
    # Vector: smaller distance = better. Deliberately out of order.
    vector = [_r("x", 0.9, "c-x"), _r("y", 0.1, "c-y"), _r("z", 0.5, "c-z")]
    merged = merge_ranked_results(vector_results=vector)
    assert [r["id"] for r in merged] == ["y", "z", "x"]
    assert _non_increasing([r["relevance"] for r in merged])


def test_hybrid_merges_incompatible_scales_correctly():
    # Reproduces the live-evidence bug: FTS (large=better) and vector
    # (small=better) must fuse into ONE correctly ordered list, each arm's
    # internal rank order preserved.
    fts = [_r("f1", 14.71, "c-f1"), _r("f2", 12.73, "c-f2"), _r("f3", 12.52, "c-f3")]
    vector = [_r("v1", 0.55, "c-v1"), _r("v2", 0.59, "c-v2")]
    merged = merge_ranked_results(fts_results=fts, vector_results=vector)
    assert len(merged) == 5
    assert _non_increasing([r["relevance"] for r in merged])
    by_id = {r["id"]: r["relevance"] for r in merged}
    assert by_id["f1"] > by_id["f2"] > by_id["f3"]
    assert by_id["v1"] > by_id["v2"]


def test_vector_weight_demotes_vector_arm_relative_to_fts():
    # Tripwire against silently reverting RRF_WEIGHT_VECTOR back to parity
    # with FTS. Under EQUAL weighting, a rank-1 vector result would outrank
    # a rank-3 FTS result here; under the measured 1.0/0.3 weighting it must
    # not, because the vector arm has no relevance floor (see module docstring
    # for the measured recall regression this weighting fixes).
    fts = [_r("f1", 14.71, "c-f1"), _r("f2", 12.73, "c-f2"), _r("f3", 12.52, "c-f3")]
    vector = [_r("v1", 0.55, "c-v1")]

    equal_weight = merge_ranked_results(fts_results=fts, vector_results=vector,
                                         fts_weight=1.0, vector_weight=1.0)
    by_id_equal = {r["id"]: r["relevance"] for r in equal_weight}
    assert by_id_equal["v1"] > by_id_equal["f3"]  # sanity: equal weighting DOES promote it

    weighted = merge_ranked_results(fts_results=fts, vector_results=vector)  # default weights
    by_id_weighted = {r["id"]: r["relevance"] for r in weighted}
    assert by_id_weighted["f3"] > by_id_weighted["v1"]


def test_hybrid_shared_id_not_duplicated():
    fts = [_r("shared", 10.0, "c-shared")]
    vector = [_r("shared", 0.2, "c-shared")]
    merged = merge_ranked_results(fts_results=fts, vector_results=vector)
    assert len(merged) == 1 and merged[0]["id"] == "shared"


def test_dedup_keeps_highest_relevance_chunk_per_conversation():
    # Same conversation, two chunk ids; the better-combined-rank chunk wins.
    fts = [_r("weak", 1.0, "conv-1"), _r("strong", 50.0, "conv-1")]
    vector = [_r("strong", 0.1, "conv-1")]
    merged = merge_ranked_results(fts_results=fts, vector_results=vector)
    assert len(merged) == 1 and merged[0]["id"] == "strong"


def test_dedup_keeps_unrelated_conversations():
    fts = [_r("a", 5.0, "c-a"), _r("b", 5.0, "c-b")]
    merged = merge_ranked_results(fts_results=fts)
    assert {r["id"] for r in merged} == {"a", "b"}


def test_ordering_invariant_large_hybrid_set():
    fts = [_r(f"f{i}", float(i), f"cf{i}") for i in range(10)]
    vector = [_r(f"v{i}", 1.0 / (i + 1), f"cv{i}") for i in range(10)]
    merged = merge_ranked_results(fts_results=fts, vector_results=vector)
    assert len(merged) == 20
    assert _non_increasing([r["relevance"] for r in merged])


def test_empty_inputs_do_not_crash():
    assert merge_ranked_results(fts_results=[], vector_results=[]) == []
