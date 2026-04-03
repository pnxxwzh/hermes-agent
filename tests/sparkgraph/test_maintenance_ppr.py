"""Phase 1 tests: PPR maintenance integration."""

from agent.sparkgraph.maintenance import run_ppr_maintenance
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import NodeStatus, NodeType


def _insert_active(store: SparkGraphStore, summary: str, canonical_key: str) -> str:
    return store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary=summary,
            canonical_key=canonical_key,
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.8,
            stability=0.8,
        )
    )


class TestPPRMaintenance:
    def test_returns_stats_on_normal_graph(self, tmp_path):
        store = SparkGraphStore(tmp_path / "sg.db")
        nid = _insert_active(store, "test node", "fact:test")

        result = run_ppr_maintenance(store)
        assert result["ppr_computed"] is True
        assert result["nodes_scored"] >= 1
        assert isinstance(result["top_node"], str)

    def test_empty_graph_returns_zero_nodes(self, tmp_path):
        store = SparkGraphStore(tmp_path / "sg.db")
        result = run_ppr_maintenance(store)
        assert result["ppr_computed"] is True
        assert result["nodes_scored"] == 0
        assert result["top_node"] is None

    def test_invalidates_cache_after_computation(self, tmp_path):
        import agent.sparkgraph.pagerank as pg_mod

        store = SparkGraphStore(tmp_path / "sg.db")
        nid = _insert_active(store, "node", "fact:node")

        # Prime the cache
        from agent.sparkgraph.pagerank import _load_graph

        _load_graph(store)
        assert pg_mod._graph_cache is not None

        run_ppr_maintenance(store)
        assert pg_mod._graph_cache is None  # cache invalidated
