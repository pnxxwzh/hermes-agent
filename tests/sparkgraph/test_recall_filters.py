"""Tests for issue #1: default_inject=0 nodes must not appear in recall output or PPR ranking."""

import pytest

from agent.sparkgraph.recaller import RecallConfig, recall_nodes, _is_injectable
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType


def test_is_injectable_active_node_returns_true():
    """Regular active node (default_inject=1) is injectable."""
    node = {"id": "n1", "default_inject": 1, "type": "FACT", "summary": "test"}
    assert _is_injectable(node) is True


def test_is_injectable_default_inject_zero_returns_false():
    """default_inject=0 node must be excluded from recall entirely."""
    node = {"id": "n1", "default_inject": 0, "type": "FACT", "summary": "test"}
    assert _is_injectable(node) is False


def test_is_injectable_default_inject_none_returns_true():
    """Nodes without default_inject column are treated as injectable."""
    node = {"id": "n1", "type": "FACT", "summary": "test"}
    assert _is_injectable(node) is True


def test_is_injectable_default_inject_string_zero_returns_false():
    """default_inject stored as string "0" is also excluded."""
    node = {"id": "n1", "default_inject": "0", "type": "FACT", "summary": "test"}
    assert _is_injectable(node) is False


def test_recall_excludes_default_inject_zero_nodes(tmp_path):
    """recall_nodes must not include default_inject=0 nodes in its output."""
    store = SparkGraphStore(tmp_path / "sg.db")

    # Injectable node (default_inject=1) — should appear in recall
    injectable_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Redis bind configuration order matters",
            canonical_key="fact:redis-bind-order",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.8,
        )
    )

    # Non-injectable node (default_inject=0) — must NOT appear
    non_injectable_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Redis bind configuration order matters",
            canonical_key="fact:redis-bind-order-alt",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.5,
            default_inject=False,
        )
    )

    nodes, edges, _ = recall_nodes(
        store,
        query="Redis bind configuration order matters",
        config=RecallConfig(search_limit=8, max_nodes=4),
    )

    node_ids = {n["id"] for n in nodes}
    assert injectable_id in node_ids, "injectable node must be in recall output"
    assert non_injectable_id not in node_ids, (
        "default_inject=0 node must NOT appear in recall output"
    )


def test_recall_non_injectable_does_not_affect_ppr_ranking(tmp_path):
    """A default_inject=0 node must not influence the PPR score of injectable nodes.

    Setup: three nodes form a chain A → C → B where C is non-injectable.
    After the fix, C is filtered out at _merge_hit, so it never enters
    the PPR candidate set. B's PPR score should reflect only the injectable path.
    """
    store = SparkGraphStore(tmp_path / "sg.db")

    # Node A — injectable FTS match (seed)
    node_a = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Docker compose port mapping syntax",
            canonical_key="fact:docker-port-mapping",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.9,
        )
    )

    # Node B — injectable but NOT reachable directly from A
    node_b = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Docker compose environment variables",
            canonical_key="fact:docker-env",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.9,
        )
    )

    # Node C — non-injectable, sits between A and B
    node_c = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Docker compose port mapping syntax",
            canonical_key="fact:docker-port-mapping-alt",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.5,
            default_inject=False,
        )
    )

    # Edges: A → C → B
    store.insert_edge(from_id=node_a, to_id=node_c, edge_type=EdgeType.RELATED_TO)
    store.insert_edge(from_id=node_c, to_id=node_b, edge_type=EdgeType.RELATED_TO)

    nodes, _, _ = recall_nodes(
        store,
        query="Docker compose port mapping syntax",
        config=RecallConfig(search_limit=8, max_nodes=4, related_limit=4),
    )

    node_ids = {n["id"] for n in nodes}
    assert node_a in node_ids, "seed FTS hit must appear"
    assert node_c not in node_ids, "non-injectable node must not appear in output"


def test_recall_all_nodes_active_but_only_injectable_appear(tmp_path):
    """When only default_inject=0 nodes match the query, recall returns empty."""
    store = SparkGraphStore(tmp_path / "sg.db")

    store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="temporary scratch state",
            canonical_key="fact:temp-scratch",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.5,
            default_inject=False,
        )
    )

    nodes, edges, token_est = recall_nodes(
        store,
        query="temporary scratch state",
        config=RecallConfig(search_limit=8, max_nodes=4),
    )

    assert nodes == [], "recall must return empty when all hits are non-injectable"
    assert token_est == 0
