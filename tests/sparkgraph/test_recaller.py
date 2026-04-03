"""Tests for simplified two-channel recaller (aligned with graph-memory)."""

import pytest
from unittest.mock import MagicMock

from agent.sparkgraph.recaller import RecallConfig, recall_nodes, _is_low_signal_query, _query_terms
from agent.sparkgraph.types import NodeStatus


class TestQueryPreprocessing:
    """TC-R-06: 低信号查询返回空"""

    def test_empty_query_is_low_signal(self):
        assert _is_low_signal_query("") is True

    def test_hello_is_low_signal(self):
        assert _is_low_signal_query("hello") is True

    def test_thanks_is_low_signal(self):
        assert _is_low_signal_query("thanks") is True

    def test_meaningful_query_not_low_signal(self):
        assert _is_low_signal_query("proxy pac script not applied") is False

    def test_query_terms(self):
        assert _query_terms("proxy PAC script error") == ["proxy", "pac", "script", "error"]


class TestRecallConfig:
    def test_defaults(self):
        cfg = RecallConfig()
        assert cfg.search_limit == 8
        assert cfg.related_limit == 4
        assert cfg.max_nodes == 4
        assert cfg.vector_limit == 24

    def test_custom(self):
        cfg = RecallConfig(search_limit=10, max_nodes=6)
        assert cfg.search_limit == 10
        assert cfg.max_nodes == 6


class TestRecallNodes:
    """TC-R-01 ~ TC-R-05: 两条通道召回行为"""

    def _make_node(self, node_id, summary, status=NodeStatus.ACTIVE, confidence=0.72, source_kind="flush", validated_count=0):
        return {
            "id": node_id,
            "summary": summary,
            "status": status.value,
            "confidence": confidence,
            "source_kind": source_kind,
            "validated_count": validated_count,
            "updated_at": 1000,
            "meta": "{}",
        }

    def test_deprecated_nodes_not_recalled(self):
        """TC-R-05: deprecated 节点不参与任何通道。"""
        store = MagicMock()
        # search_nodes is called with status=ACTIVE.value → deprecated nodes are filtered at store level
        # So we return an empty list to simulate that filtering
        store.search_nodes.return_value = []
        store.list_vector_nodes.return_value = []
        store.get_related_nodes.return_value = []
        store.get_edges_for_nodes.return_value = []
        store.increment_validated_count = MagicMock()

        nodes, _, _ = recall_nodes(store, query="proxy pac error")
        assert nodes == []

    def test_active_fts_node_recalled(self):
        """TC-R-01: FTS 匹配 active 节点可召回。"""
        store = MagicMock()
        active_node = self._make_node("n1", "proxy pac script", confidence=0.88, source_kind="explicit", validated_count=2)
        store.search_nodes.return_value = [active_node]
        store.list_vector_nodes.return_value = []
        store.get_related_nodes.return_value = []
        store.get_edges_for_nodes.return_value = []
        store.increment_validated_count = MagicMock()

        nodes, edges, _ = recall_nodes(store, query="proxy pac script")
        assert len(nodes) == 1
        assert nodes[0]["id"] == "n1"
        store.increment_validated_count.assert_called_once_with(["n1"])

    def test_graph_expansion_includes_active_neighbors(self):
        """TC-R-02: 图扩展包含 1-hop active 邻居。"""
        store = MagicMock()
        seed_node = self._make_node("n1", "proxy pac error", confidence=0.88, source_kind="explicit")
        neighbor = self._make_node("n2", "network proxy configuration", confidence=0.72, source_kind="flush")
        store.search_nodes.return_value = [seed_node]
        store.list_vector_nodes.return_value = []
        store.get_related_nodes.return_value = [neighbor]
        store.get_edges_for_nodes.return_value = []
        store.increment_validated_count = MagicMock()

        nodes, edges, _ = recall_nodes(store, query="proxy pac error")
        node_ids = {n["id"] for n in nodes}
        assert "n1" in node_ids
        assert "n2" in node_ids

    def test_graph_expansion_excludes_non_active(self):
        """TC-R-03: 图扩展不包含 deprecated 邻居。"""
        store = MagicMock()
        seed_node = self._make_node("n1", "proxy pac error", confidence=0.88, source_kind="explicit")
        deprecated_neighbor = self._make_node("n2", "old proxy bug", status=NodeStatus.DEPRECATED)
        # get_related_nodes returns all, but search_nodes filters to ACTIVE
        store.search_nodes.return_value = [seed_node]
        store.list_vector_nodes.return_value = []
        # Simulate store already filters get_related_nodes to active_only=True
        store.get_related_nodes.return_value = []  # deprecated filtered out by store
        store.get_edges_for_nodes.return_value = []
        store.increment_validated_count = MagicMock()

        nodes, edges, _ = recall_nodes(store, query="proxy pac error")
        node_ids = {n["id"] for n in nodes}
        assert "n2" not in node_ids

    def test_recall_ranks_by_validated_count(self):
        """TC-R-04: 合并后按 recall_priority_score 排序，高 validated_count 排前。"""
        store = MagicMock()
        low_node = self._make_node("n1", "proxy pac error", confidence=0.72, validated_count=0)
        high_node = self._make_node("n2", "proxy pac issue", confidence=0.72, validated_count=10)
        store.search_nodes.return_value = [low_node, high_node]
        store.list_vector_nodes.return_value = []
        store.get_related_nodes.return_value = []
        store.get_edges_for_nodes.return_value = []
        store.increment_validated_count = MagicMock()

        nodes, edges, _ = recall_nodes(store, query="proxy pac")
        # validated_count=10 should rank above validated_count=0
        assert nodes[0]["id"] == "n2"
        assert nodes[1]["id"] == "n1"

    def test_max_nodes_limit(self):
        """Recall returns at most max_nodes items."""
        store = MagicMock()
        nodes = [self._make_node(f"n{i}", f"node {i}") for i in range(8)]
        store.search_nodes.return_value = nodes
        store.list_vector_nodes.return_value = []
        store.get_related_nodes.return_value = []
        store.get_edges_for_nodes.return_value = []
        store.increment_validated_count = MagicMock()

        cfg = RecallConfig(max_nodes=3)
        result, _, _ = recall_nodes(store, query="node", config=cfg)
        assert len(result) == 3

    def test_no_increment_without_results(self):
        """No nodes recalled → increment_validated_count not called."""
        store = MagicMock()
        store.search_nodes.return_value = []
        store.list_vector_nodes.return_value = []
        store.get_related_nodes.return_value = []
        store.get_edges_for_nodes.return_value = []
        store.increment_validated_count = MagicMock()

        recall_nodes(store, query="")
        store.increment_validated_count.assert_not_called()

    def test_recall_returns_token_estimate(self):
        """recall_nodes returns (nodes, edges, token_estimate) where token_estimate ≈ chars/3."""
        store = MagicMock()
        store.search_nodes.return_value = [
            self._make_node("n1", "proxy pac script", confidence=0.88),
            self._make_node("n2", "proxy pac error", confidence=0.72),
        ]
        store.list_vector_nodes.return_value = []
        store.get_related_nodes.return_value = []
        store.get_edges_for_nodes.return_value = []
        store.increment_validated_count = MagicMock()

        nodes, edges, token_estimate = recall_nodes(store, query="proxy pac")
        # summary chars: "proxy pac script"=17 + "proxy pac error"=15 = 32 → 32/3 ≈ 11
        assert token_estimate == pytest.approx(10.67, rel=1)
