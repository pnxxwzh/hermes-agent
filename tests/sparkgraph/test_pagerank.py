"""
Phase 1 tests: PPR algorithm correctness + recall integration
"""

import time

import pytest

from agent.sparkgraph.pagerank import (
    _GraphCache,
    compute_global_pagerank,
    invalidate_graph_cache,
    personalized_pagerank,
)
from agent.sparkgraph.recaller import RecallConfig, recall_nodes
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType


def _insert_active(
    store: SparkGraphStore,
    summary: str,
    canonical_key: str,
    *,
    confidence: float = 0.8,
) -> str:
    return store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary=summary,
            canonical_key=canonical_key,
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=confidence,
        )
    )


# ─── _GraphCache unit tests ────────────────────────────────────


class TestGraphCache:
    def test_cache_fresh_within_ttl(self):
        cache = _GraphCache(
            adj={},
            node_ids=set(),
            N=0,
            cached_at_ms=int(time.time() * 1000),
        )
        assert cache.is_fresh() is True

    def test_cache_expired_after_ttl(self):
        old_ts = int((time.time() - 31) * 1000)  # 31 seconds ago
        cache = _GraphCache(adj={}, node_ids=set(), N=0, cached_at_ms=old_ts)
        assert cache.is_fresh() is False

    def test_invalidate_clears_global(self):
        import agent.sparkgraph.pagerank as pg_mod

        pg_mod._graph_cache = _GraphCache(
            {}, set(), 0, int(time.time() * 1000)
        )
        invalidate_graph_cache()
        assert pg_mod._graph_cache is None

    def test_cache_isolated_by_database_path(self, tmp_path):
        """Fresh cache from one db must not be reused for another db."""
        store_one = SparkGraphStore(tmp_path / "one.db")
        store_two = SparkGraphStore(tmp_path / "two.db")

        first_one = _insert_active(store_one, "alpha", "fact:alpha")
        _insert_active(store_one, "beta", "fact:beta")
        second_two = _insert_active(store_two, "xray", "fact:xray")

        invalidate_graph_cache()
        scores_one = compute_global_pagerank(store_one)
        scores_two = compute_global_pagerank(store_two)

        assert first_one in scores_one
        assert second_two in scores_two
        assert first_one not in scores_two


# ─── personalized_pagerank unit tests ─────────────────────────


class TestPersonalizedPagerank:
    def test_empty_seeds_returns_empty(self, tmp_path):
        store = SparkGraphStore(tmp_path / "sg.db")
        result = personalized_pagerank(store, seed_ids=[], candidate_ids=["n1"])
        assert result == {}

    def test_empty_candidates_returns_empty(self, tmp_path):
        nid = _insert_active(store := SparkGraphStore(tmp_path / "sg.db"), "A", "fact:a")
        result = personalized_pagerank(store, seed_ids=[nid], candidate_ids=[])
        assert result == {}

    def test_single_node_self_score(self, tmp_path):
        """Single-node graph: seed=target, PPR score should concentrate on self (>0.5)."""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid = _insert_active(store, "solo", "fact:solo")
        scores = personalized_pagerank(store, seed_ids=[nid], candidate_ids=[nid])
        assert nid in scores
        assert scores[nid] > 0.5

    def test_two_node_chain_seed_first(self, tmp_path):
        """A → B (A is seed), A's PPR > B's."""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "A", "fact:a")
        nid_b = _insert_active(store, "B", "fact:b")
        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)

        scores = personalized_pagerank(
            store, seed_ids=[nid_a], candidate_ids=[nid_a, nid_b]
        )
        assert scores[nid_a] > scores[nid_b]

    def test_two_node_chain_seed_second(self, tmp_path):
        """A → B (B is seed), B's PPR > A's."""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "A", "fact:a")
        nid_b = _insert_active(store, "B", "fact:b")
        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)

        scores = personalized_pagerank(
            store, seed_ids=[nid_b], candidate_ids=[nid_a, nid_b]
        )
        assert scores[nid_b] > scores[nid_a]

    def test_disconnected_node_isolated(self, tmp_path):
        """Isolated node PPR should be close to 0."""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "A", "fact:a")
        nid_b = _insert_active(store, "B", "fact:b")
        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)
        nid_c = _insert_active(store, "C", "fact:c")  # isolated — no edges

        scores = personalized_pagerank(
            store, seed_ids=[nid_a], candidate_ids=[nid_a, nid_b, nid_c]
        )
        assert scores[nid_c] < 0.01

    def test_triangle_balanced_scores(self, tmp_path):
        """Triangle (A↔B↔C↔A), 3 seeds, scores should be close (diff < 0.1)."""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "A", "fact:a")
        nid_b = _insert_active(store, "B", "fact:b")
        nid_c = _insert_active(store, "C", "fact:c")
        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)
        store.insert_edge(from_id=nid_b, to_id=nid_c, edge_type=EdgeType.RELATED_TO)
        store.insert_edge(from_id=nid_c, to_id=nid_a, edge_type=EdgeType.RELATED_TO)

        scores = personalized_pagerank(
            store,
            seed_ids=[nid_a, nid_b, nid_c],
            candidate_ids=[nid_a, nid_b, nid_c],
        )
        vals = list(scores.values())
        assert max(vals) - min(vals) < 0.1

    def test_nonexistent_candidate_absent_from_result(self, tmp_path):
        """Nonexistent node IDs return without that key, no exception raised."""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid = _insert_active(store, "real", "fact:real")
        scores = personalized_pagerank(
            store, seed_ids=[nid], candidate_ids=[nid, "nonexistent-id"]
        )
        assert "nonexistent-id" not in scores

    def test_deterministic_output(self, tmp_path):
        """Two calls with same input produce identical results."""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "A", "fact:a")
        nid_b = _insert_active(store, "B", "fact:b")
        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)

        s1 = personalized_pagerank(
            store, seed_ids=[nid_a], candidate_ids=[nid_a, nid_b]
        )
        s2 = personalized_pagerank(
            store, seed_ids=[nid_a], candidate_ids=[nid_a, nid_b]
        )
        assert abs(s1[nid_a] - s2[nid_a]) < 1e-6
        assert abs(s1[nid_b] - s2[nid_b]) < 1e-6

    def test_invalid_seeds_filtered_out(self, tmp_path):
        """Seeds not in graph are silently ignored."""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid = _insert_active(store, "real", "fact:real")
        scores = personalized_pagerank(
            store,
            seed_ids=[nid, "not-in-graph"],
            candidate_ids=[nid],
        )
        assert scores == {nid: pytest.approx(scores[nid])}


# ─── compute_global_pagerank unit tests ───────────────────────


class TestGlobalPagerank:
    def test_scores_sum_to_one(self, tmp_path):
        """All node scores should sum to ≈ 1."""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "A", "fact:a")
        nid_b = _insert_active(store, "B", "fact:b")
        nid_c = _insert_active(store, "C", "fact:c")
        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)
        store.insert_edge(from_id=nid_b, to_id=nid_c, edge_type=EdgeType.RELATED_TO)

        scores = compute_global_pagerank(store)
        total = sum(scores.values())
        assert 0.99 < total < 1.01

    def test_empty_graph_returns_empty(self, tmp_path):
        store = SparkGraphStore(tmp_path / "sg.db")
        scores = compute_global_pagerank(store)
        assert scores == {}

    def test_global_pr_more_even_than_ppr(self, tmp_path):
        """Global PR should give more evenly distributed scores than PPR."""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "A", "fact:a")
        nid_b = _insert_active(store, "B", "fact:b")
        nid_c = _insert_active(store, "C", "fact:c")
        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)
        store.insert_edge(from_id=nid_b, to_id=nid_c, edge_type=EdgeType.RELATED_TO)

        global_scores = compute_global_pagerank(store)
        ppr_scores = personalized_pagerank(
            store, seed_ids=[nid_a], candidate_ids=[nid_a, nid_b, nid_c]
        )

        # PPR from A should be more skewed toward A than global PR
        g_range = max(global_scores.values()) - min(global_scores.values())
        p_range = max(ppr_scores.values()) - min(ppr_scores.values())
        assert p_range > g_range


# ─── recall + PPR integration tests ───────────────────────────


class TestRecallWithPPR:
    def test_result_has_ppr_score_field(self, tmp_path):
        """
        When graph traversal produces related nodes, recall_nodes should attach
        _ppr_score to every result node (PPR mode is active).
        """
        store = SparkGraphStore(tmp_path / "sg.db")
        direct_id = _insert_active(
            store, "elasticsearch field mapping", "fact:es-mapping"
        )
        # Add a connected neighbour so related_hits is non-empty → PPR triggers
        related_id = _insert_active(
            store, "elasticsearch index configuration", "fact:es-index"
        )
        store.insert_edge(
            from_id=direct_id, to_id=related_id, edge_type=EdgeType.RELATED_TO
        )

        nodes, _edges, _ = recall_nodes(
            store,
            query="elasticsearch field mapping",
            config=RecallConfig(
                max_nodes=4, search_limit=4, vector_limit=0, related_limit=4
            ),
        )
        assert len(nodes) > 0
        assert "_ppr_score" in nodes[0]
        assert isinstance(nodes[0]["_ppr_score"], float)

    def test_direct_hit_ranks_higher_than_neighbor(self, tmp_path):
        """Direct FTS hit (seed node) should outrank its 1-hop neighbour."""
        store = SparkGraphStore(tmp_path / "sg.db")
        direct_id = _insert_active(
            store, "redis remote access problem", "fact:redis"
        )
        indirect_id = _insert_active(
            store, "port binding connectivity", "fact:port-binding"
        )
        store.insert_edge(
            from_id=direct_id, to_id=indirect_id, edge_type=EdgeType.RELATED_TO
        )

        nodes, _edges, _ = recall_nodes(
            store,
            query="redis remote access",
            config=RecallConfig(
                max_nodes=2, search_limit=2, vector_limit=0, related_limit=4
            ),
        )
        assert nodes[0]["id"] == direct_id

    def test_empty_graph_no_crash(self, tmp_path):
        """Empty graph recall returns [], does not raise."""
        store = SparkGraphStore(tmp_path / "sg.db")
        nodes, _edges, _ = recall_nodes(
            store, query="anything", config=RecallConfig(max_nodes=4)
        )
        assert nodes == []

    def test_ppr_score_reflects_graph_distance(self, tmp_path):
        """
        Graph distance: seed (hub) > 1-hop (spoke) > 2-hop > isolated.

        Use a star centred on seed: seed↔hop1, seed↔hop2, seed↔hop3.
        Seed accumulates contributions from all 3 spokes, each spoke only feeds back to seed,
        so seed > any spoke. Hop1 and hop2 are both 1-hop from seed, so hop1 ≈ hop2.
        Isolated has no path to seed, so it scores ~0.
        """
        store = SparkGraphStore(tmp_path / "sg.db")
        seed = _insert_active(store, "seed", "fact:seed")
        hop1 = _insert_active(store, "hop1", "fact:hop1")
        hop2 = _insert_active(store, "hop2", "fact:hop2")
        hop3 = _insert_active(store, "hop3", "fact:hop3")  # second 1-hop
        isolated = _insert_active(store, "isolated", "fact:isolated")

        # Star centred on seed
        store.insert_edge(from_id=seed, to_id=hop1, edge_type=EdgeType.RELATED_TO)
        store.insert_edge(from_id=seed, to_id=hop2, edge_type=EdgeType.RELATED_TO)
        store.insert_edge(from_id=seed, to_id=hop3, edge_type=EdgeType.RELATED_TO)
        # isolated: no edges

        scores = personalized_pagerank(
            store,
            seed_ids=[seed],
            candidate_ids=[seed, hop1, hop2, hop3, isolated],
        )
        # Seed (hub) > any single spoke (receives contributions from all 3)
        assert scores[seed] > scores[hop1]
        assert scores[seed] > scores[hop2]
        assert scores[seed] > scores[hop3]
        # Isolated: no path to seed → ~0
        assert scores[isolated] < 0.01
        # Hop1 and hop2 are symmetric 1-hops: scores should be close
        assert abs(scores[hop1] - scores[hop2]) < 0.05

    def test_multihop_graph_recall_includes_neighbours(self, tmp_path):
        """2-hop graph: A(query) ↔ B ↔ C. B should appear in recall results."""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "docker deployment", "fact:docker-deploy")
        nid_b = _insert_active(store, "docker compose", "fact:docker-compose")
        nid_c = _insert_active(store, "port forwarding", "fact:port-forward")

        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)
        store.insert_edge(from_id=nid_b, to_id=nid_c, edge_type=EdgeType.RELATED_TO)

        nodes, _edges, _ = recall_nodes(
            store,
            query="docker deployment",
            config=RecallConfig(
                max_nodes=4, search_limit=2, vector_limit=0, related_limit=4
            ),
        )
        ids = [n["id"] for n in nodes]
        assert nid_a in ids  # direct hit
        assert nid_b in ids  # 1-hop neighbour

    def test_deprecated_node_not_recalled(self, tmp_path):
        """Deprecated nodes are not recalled (CANDIDATE is removed)."""
        store = SparkGraphStore(tmp_path / "sg.db")
        store.insert_node(
            SparkGraphNodeInput(
                type=NodeType.FACT,
                summary="deprecated node",
                canonical_key="fact:deprecated",
                source_kind="reflection",
                status=NodeStatus.DEPRECATED,
                confidence=0.50,
            )
        )
        nodes, _edges, _ = recall_nodes(
            store, query="deprecated", config=RecallConfig(max_nodes=4)
        )
        # deprecated nodes are never recalled
        assert len(nodes) == 0

    def test_dedup_preserved_after_ppr(self, tmp_path):
        """Same node from direct+related appears only once in results."""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid = _insert_active(store, "shared", "fact:shared")
        other_id = _insert_active(store, "other", "fact:other")
        store.insert_edge(from_id=nid, to_id=other_id, edge_type=EdgeType.RELATED_TO)

        nodes, _edges, _ = recall_nodes(
            store,
            query="shared",
            config=RecallConfig(
                max_nodes=4, search_limit=4, vector_limit=0, related_limit=4
            ),
        )
        ids = [n["id"] for n in nodes]
        assert ids == list(dict.fromkeys(ids))  # no duplicates
