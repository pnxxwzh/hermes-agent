"""Tests for issue #3: merge_nodes must invalidate the PPR graph cache."""

import pytest

from agent.sparkgraph.pagerank import (
    compute_global_pagerank,
    invalidate_graph_cache,
    personalized_pagerank,
)
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType

# Import the module (not just the symbol) so we can inspect _graph_cache after mutations
import agent.sparkgraph.pagerank as pagerank_module


def test_merge_nodes_invalidates_graph_cache(tmp_path):
    """After merge_nodes, the next PPR computation must see the updated graph.

    Setup:
      A → B  (existing edge, B reachable from A)
      A → merge_stub  (existing edge, merge_stub is separate)

    After merge_nodes(keep=B, merge=merge_stub):
      A → B  (edge migrated from A → merge_stub)
      B is now the ONLY path from A
      The PPR cache must be invalidated so the next call reflects A → B.
    """
    store = SparkGraphStore(tmp_path / "sg.db")

    node_a = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="A",
            canonical_key="fact:a",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.9,
        )
    )
    node_b = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="B",
            canonical_key="fact:b",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.8,
        )
    )
    merge_stub = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Merge stub",
            canonical_key="fact:merge-stub",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.5,
        )
    )

    # Initial edges: A → B, A → merge_stub
    store.insert_edge(from_id=node_a, to_id=node_b, edge_type=EdgeType.RELATED_TO)
    store.insert_edge(from_id=node_a, to_id=merge_stub, edge_type=EdgeType.RELATED_TO)

    # Warm the cache
    scores_before = personalized_pagerank(
        store,
        seed_ids=[node_a],
        candidate_ids=[node_a, node_b, merge_stub],
    )
    assert scores_before.get(node_b, 0.0) > 0.0, "B reachable from A before merge"

    # merge_nodes(keep_id=node_b, merge_id=merge_stub):
    # Edge A → merge_stub is migrated to A → node_b.
    # Now there are two parallel edges A → B (one original, one migrated).
    # The merge_stub node becomes deprecated.
    store.merge_nodes(keep_id=node_b, merge_id=merge_stub)

    # If cache was NOT invalidated, the next PPR call would still think
    # there is no extra path from A to B (using stale adjacency list).
    # After fix: cache is invalidated → next call sees updated graph.
    scores_after = personalized_pagerank(
        store,
        seed_ids=[node_a],
        candidate_ids=[node_a, node_b],
    )
    # node_b must be reachable and have a non-zero PPR score
    assert scores_after.get(node_b, 0.0) > 0.0, (
        "After merge, node_b must be reachable from seed A. "
        "This fails if merge_nodes did NOT invalidate the PPR cache."
    )
    assert scores_after.get(node_a, 0.0) > 0.0, "seed A must have self-teleport score"


def test_merge_nodes_deprecated_node_absent_from_ppr(tmp_path):
    """The deprecated (merge victim) node must not appear in PPR scores after merge."""
    store = SparkGraphStore(tmp_path / "sg.db")

    node_a = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="A",
            canonical_key="fact:a",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.9,
        )
    )
    node_b = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="B",
            canonical_key="fact:b",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.8,
        )
    )

    store.insert_edge(from_id=node_a, to_id=node_b, edge_type=EdgeType.RELATED_TO)

    # Warm cache
    scores_before = compute_global_pagerank(store)
    assert node_b in scores_before, "node_b must have a PageRank before merge"

    # Merge B into A: B becomes deprecated
    store.merge_nodes(keep_id=node_a, merge_id=node_b)

    # After merge, the cache is invalidated. PPR only considers active nodes,
    # so B (now deprecated) should not appear in the result.
    scores_after = compute_global_pagerank(store)
    assert node_a in scores_after
    assert node_b not in scores_after, (
        "Deprecated merge-victim node must not appear in PPR scores"
    )


def test_invalidate_graph_cache_clears_process_cache(tmp_path):
    """Explicit invalidate_graph_cache() call clears the process-local cache."""
    store = SparkGraphStore(tmp_path / "sg.db")

    node_a = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="X",
            canonical_key="fact:x",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.9,
        )
    )

    # Warm cache
    compute_global_pagerank(store)
    # Access via module reference (not imported binding) to see post-mutation value
    assert pagerank_module._graph_cache is not None, (
        "cache should be populated after first call"
    )

    # Explicitly invalidate
    invalidate_graph_cache()
    assert pagerank_module._graph_cache is None, (
        "cache must be None after invalidate_graph_cache()"
    )

    # Next call must recompute (no stale data)
    scores = compute_global_pagerank(store)
    assert node_a in scores
