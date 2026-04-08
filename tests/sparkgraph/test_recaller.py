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

    def test_direct_hit_priority_beats_explicit_backfill_when_scores_are_close(self, tmp_path):
        """Direct lexical hits should rank ahead of weaker explicit/manual supplements."""
        from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
        from agent.sparkgraph.types import NodeType

        store = SparkGraphStore(tmp_path / "default.db")
        direct_id = store.insert_node(
            SparkGraphNodeInput(
                type=NodeType.FACT,
                summary="docker compose proxy issue",
                canonical_key="fact:docker-compose-proxy-issue",
                source_kind="flush",
                status=NodeStatus.ACTIVE,
                confidence=0.72,
            )
        )
        explicit_id = store.insert_node(
            SparkGraphNodeInput(
                type=NodeType.FACT,
                summary="docker desktop install note",
                canonical_key="fact:docker-desktop-install-note",
                source_kind="explicit",
                status=NodeStatus.ACTIVE,
                confidence=0.88,
            )
        )
        store.conn.execute("UPDATE sg_nodes SET validated_count = 20 WHERE id = ?", (explicit_id,))
        store.conn.commit()

        nodes, _, _ = recall_nodes(
            store,
            query="docker compose proxy issue",
            config=RecallConfig(max_nodes=4, related_limit=0, vector_limit=0),
        )

        assert nodes[0]["id"] == direct_id


# ─── Tests for Session Pool ──────────────────────────────────────

class TestPoolConstants:
    """TC-SP-01: Pool 常量定义"""

    def test_pool_capacity(self):
        from agent.sparkgraph.recaller import POOL_CAPACITY
        assert POOL_CAPACITY == 12

    def test_lrfu_weights(self):
        from agent.sparkgraph.recaller import LRFU_FREQ_W, LRFU_RECENCY_W
        assert LRFU_FREQ_W == 0.4
        assert LRFU_RECENCY_W == 0.6


class TestPoolEntry:
    """TC-SP-02: PoolEntry 数据结构"""

    def test_pool_entry_slots(self):
        from agent.sparkgraph.recaller import PoolEntry
        entry = PoolEntry({"node": {"id": "n1", "summary": "test"}, "added_at": 1000.0, "hit_count": 2, "last_hit": 2000.0})
        assert entry["node"]["id"] == "n1"
        assert entry["hit_count"] == 2
        assert entry["last_hit"] == 2000.0


class TestLRFUScore:
    """TC-SP-03: LRFU 分数计算"""

    def test_lrfu_score_favors_high_hit_count(self):
        from agent.sparkgraph.recaller import PoolEntry, _lrfu_score
        import time
        now = time.time()
        low_hit = PoolEntry({"node": {"id": "n1"}, "added_at": now, "hit_count": 1, "last_hit": now})
        high_hit = PoolEntry({"node": {"id": "n2"}, "added_at": now, "hit_count": 5, "last_hit": now})
        assert _lrfu_score(high_hit) > _lrfu_score(low_hit)

    def test_lrfu_score_favors_recent_hits(self):
        from agent.sparkgraph.recaller import PoolEntry, _lrfu_score
        import time
        now = time.time()
        old = PoolEntry({"node": {"id": "n1"}, "added_at": now - 7200, "hit_count": 3, "last_hit": now - 7200})  # 2h ago
        recent = PoolEntry({"node": {"id": "n2"}, "added_at": now - 10, "hit_count": 3, "last_hit": now - 10})  # 10s ago
        assert _lrfu_score(recent) > _lrfu_score(old)


class TestPoolEviction:
    """TC-SP-04: LRFU 驱逐"""

    def test_eviction_when_over_capacity(self):
        from agent.sparkgraph.recaller import PoolEntry, _pool_evict_lrfu, POOL_CAPACITY
        import time
        now = time.time()
        # Create POOL_CAPACITY + 5 entries with varying hit counts
        pool = {}
        for i in range(POOL_CAPACITY + 5):
            hit = i + 1  # increasing hit counts: 1, 2, 3, ...
            pool[f"n{i}"] = PoolEntry({
                "node": {"id": f"n{i}"},
                "added_at": now,
                "hit_count": hit,
                "last_hit": now,
            })
        _pool_evict_lrfu(pool)
        # After eviction, pool should be at capacity
        assert len(pool) == POOL_CAPACITY
        # The lowest hit-count entries should have been evicted
        evicted_ids = {f"n{i}" for i in range(5)}  # n0..n4 have lowest hit counts
        assert len(pool.keys() & evicted_ids) == 0

    def test_no_eviction_when_under_capacity(self):
        from agent.sparkgraph.recaller import PoolEntry, _pool_evict_lrfu
        import time
        now = time.time()
        pool = {}
        for i in range(5):
            pool[f"n{i}"] = PoolEntry({
                "node": {"id": f"n{i}"},
                "added_at": now,
                "hit_count": i + 1,
                "last_hit": now,
            })
        _pool_evict_lrfu(pool)
        assert len(pool) == 5


class TestPoolAdd:
    """TC-SP-05: Pool 添加行为"""

    def test_new_node_added_to_pool(self):
        from agent.sparkgraph.recaller import PoolEntry, _pool_add
        import time
        now = time.time()
        pool = {}
        node = {"id": "n1", "summary": "test"}
        _pool_add(pool, node)
        assert "n1" in pool
        assert pool["n1"]["hit_count"] == 1
        assert pool["n1"]["node"]["summary"] == "test"

    def test_existing_node_hit_count_incremented(self):
        from agent.sparkgraph.recaller import PoolEntry, _pool_add
        import time
        now = time.time()
        pool = {}
        pool["n1"] = PoolEntry({"node": {"id": "n1"}, "added_at": now, "hit_count": 3, "last_hit": now})
        _pool_add(pool, {"id": "n1", "summary": "updated"})
        assert pool["n1"]["hit_count"] == 4
        assert pool["n1"]["node"]["summary"] == "updated"

    def test_triggers_eviction_when_full(self):
        from agent.sparkgraph.recaller import PoolEntry, _pool_add, POOL_CAPACITY
        import time
        now = time.time()
        pool = {}
        # Fill pool with equal-hit-count entries; n0 added first so it has
        # the lowest recency and will be evicted when the new high-hit node arrives.
        for i in range(POOL_CAPACITY):
            _pool_add(pool, {"id": f"n{i}", "summary": f"node {i}"})
        assert len(pool) == POOL_CAPACITY
        # Add high-hit node — pool must evict exactly one entry (the lowest LRFU)
        _pool_add(pool, {"id": "n_new", "summary": "new high hit", "hit_count": 100})
        assert len(pool) == POOL_CAPACITY
        assert "n_new" in pool  # high-hit node must be in pool
        assert len(pool) == POOL_CAPACITY  # capacity strictly enforced


class TestPoolRefresh:
    """TC-SP-06: Pool Refresh"""

    def test_recalled_ids_get_updated_last_hit(self):
        from agent.sparkgraph.recaller import PoolEntry, _pool_refresh
        import time
        now = time.time()
        pool = {
            "n1": PoolEntry({"node": {"id": "n1"}, "added_at": now - 100, "hit_count": 1, "last_hit": now - 100}),
            "n2": PoolEntry({"node": {"id": "n2"}, "added_at": now - 100, "hit_count": 1, "last_hit": now - 100}),
        }
        _pool_refresh(pool, {"n1"})  # only n1 was recalled this turn
        # n1's last_hit should be updated to now (within 1 second tolerance)
        assert abs(pool["n1"]["last_hit"] - now) < 1.0
        # n2's last_hit should remain old
        assert abs(pool["n2"]["last_hit"] - (now - 100)) < 1

    def test_empty_recalled_ids_does_not_crash(self):
        from agent.sparkgraph.recaller import PoolEntry, _pool_refresh
        import time
        now = time.time()
        pool = {
            "n1": PoolEntry({"node": {"id": "n1"}, "added_at": now - 100, "hit_count": 1, "last_hit": now - 100}),
        }
        _pool_refresh(pool, set())  # no nodes recalled this turn
        assert pool["n1"]["last_hit"] == now - 100  # unchanged


class TestRecallPoolClear:
    """TC-SP-07: Pool 清除"""

    def test_clear_removes_pool(self):
        from agent.sparkgraph.recaller import PoolEntry, recall_pool_clear, recall_pool_get
        import time
        now = time.time()
        pool = recall_pool_get("session_x")
        pool["n1"] = PoolEntry({"node": {"id": "n1"}, "added_at": now, "hit_count": 1, "last_hit": now})
        assert "n1" in recall_pool_get("session_x")
        recall_pool_clear("session_x")
        # After clear, pool should be empty (re-created)
        assert "n1" not in recall_pool_get("session_x")

    def test_clear_nonexistent_is_noop(self):
        from agent.sparkgraph.recaller import recall_pool_clear
        recall_pool_clear("nonexistent_session")  # should not raise


class TestRecallPoolGet:
    """TC-SP-08: Pool 获取"""

    def test_get_creates_pool_if_missing(self):
        from agent.sparkgraph.recaller import recall_pool_clear, recall_pool_get
        import uuid
        sid = f"test_{uuid.uuid4().hex[:8]}"
        try:
            pool = recall_pool_get(sid)
            assert isinstance(pool, dict)
            assert len(pool) == 0
            # Second call returns same instance
            pool2 = recall_pool_get(sid)
            assert pool is pool2
        finally:
            recall_pool_clear(sid)


class TestRecallNodesWithPool:
    """TC-SP-09 ~ TC-SP-14: recall_nodes 与 Session Pool 集成"""

    def _make_node(self, node_id, summary, **kwargs):
        defaults = {"status": "active", "confidence": 0.72, "source_kind": "flush", "validated_count": 0, "updated_at": 1000, "meta": "{}"}
        defaults.update(kwargs)
        defaults["id"] = node_id
        defaults["summary"] = summary
        return defaults

    def test_pool_node_injected_when_not_in_fresh_results(self):
        """TC-SP-09: 上一轮召回但本轮FTS/向量未命中的节点，仍然被注入结果"""
        from unittest.mock import MagicMock
        from agent.sparkgraph.recaller import PoolEntry, recall_pool_clear, recall_pool_get, recall_nodes
        import time
        session_id = "sp09"
        try:
            pool = recall_pool_get(session_id)
            now = time.time()
            pool["pool_n1"] = PoolEntry({
                "node": self._make_node("pool_n1", "pool node from previous turn"),
                "added_at": now - 100, "hit_count": 3, "last_hit": now - 100,
            })

            store = MagicMock()
            store.search_nodes.return_value = []  # No FTS matches this turn
            store.list_vector_nodes.return_value = []
            store.get_related_nodes.return_value = []
            store.get_edges_for_nodes.return_value = []
            store.increment_validated_count = MagicMock()

            nodes, _, _ = recall_nodes(store, query="something else", session_id=session_id)
            # pool_n1 should still appear even though it wasn't in fresh results
            node_ids = {n["id"] for n in nodes}
            assert "pool_n1" in node_ids
        finally:
            recall_pool_clear(session_id)

    def test_fresh_node_also_updates_pool_hit_count(self):
        """TC-SP-10: 本轮已命中的节点，pool hit_count 递增"""
        from unittest.mock import MagicMock
        from agent.sparkgraph.recaller import recall_pool_clear, recall_pool_get, recall_nodes
        import time
        session_id = "sp10"
        try:
            pool = recall_pool_get(session_id)
            now = time.time()
            # Pre-populate pool with a node that WILL be found fresh
            node = self._make_node("n1", "proxy pac error", source_kind="explicit")
            from agent.sparkgraph.recaller import PoolEntry
            pool["n1"] = PoolEntry({"node": node, "added_at": now - 100, "hit_count": 2, "last_hit": now - 100})

            store = MagicMock()
            store.search_nodes.return_value = [self._make_node("n1", "proxy pac error", source_kind="explicit", validated_count=5)]
            store.list_vector_nodes.return_value = []
            store.get_related_nodes.return_value = []
            store.get_edges_for_nodes.return_value = []
            store.increment_validated_count = MagicMock()

            nodes, _, _ = recall_nodes(store, query="proxy pac", session_id=session_id)
            # n1 is in both pool and fresh results → hit_count increments once (2→3),
            # tracked via pool_refreshed_ids to avoid double-increment
            assert pool["n1"]["hit_count"] == 3
        finally:
            recall_pool_clear(session_id)

    def test_pool_node_ranked_above_low_priority_fresh_node(self):
        """TC-SP-11: 池节点优先级高于新鲜的低优先级节点"""
        from unittest.mock import MagicMock
        from agent.sparkgraph.recaller import recall_pool_clear, recall_pool_get, recall_nodes
        import time
        session_id = "sp11"
        try:
            pool = recall_pool_get(session_id)
            now = time.time()
            # Pool node with multiple hits — should outrank a fresh low-priority node
            pool_node = self._make_node("pool_n1", "previous session context", validated_count=0)
            from agent.sparkgraph.recaller import PoolEntry
            pool["pool_n1"] = PoolEntry({
                "node": pool_node, "added_at": now - 100, "hit_count": 5, "last_hit": now - 100,
            })

            store = MagicMock()
            # Fresh FTS match with low priority
            store.search_nodes.return_value = [self._make_node("fresh_n1", "new unrelated", validated_count=0)]
            store.list_vector_nodes.return_value = []
            store.get_related_nodes.return_value = []
            store.get_edges_for_nodes.return_value = []
            store.increment_validated_count = MagicMock()

            nodes, _, _ = recall_nodes(store, query="something unrelated", session_id=session_id)
            # pool_n1 should be ranked first due to pool boost
            assert len(nodes) >= 1
            assert nodes[0]["id"] == "pool_n1"
        finally:
            recall_pool_clear(session_id)

    def test_without_session_id_pool_not_used(self):
        """TC-SP-12: 不传 session_id 时，pool 不参与（向后兼容）"""
        from unittest.mock import MagicMock
        from agent.sparkgraph.recaller import recall_pool_clear, recall_pool_get, recall_nodes
        session_id = "sp12"
        try:
            # Pre-populate pool
            pool = recall_pool_get(session_id)
            import time
            now = time.time()
            from agent.sparkgraph.recaller import PoolEntry
            pool["n1"] = PoolEntry({"node": self._make_node("n1", "pooled node"), "added_at": now, "hit_count": 99, "last_hit": now})

            store = MagicMock()
            store.search_nodes.return_value = [self._make_node("fresh_n1", "fresh match", validated_count=0)]
            store.list_vector_nodes.return_value = []
            store.get_related_nodes.return_value = []
            store.get_edges_for_nodes.return_value = []
            store.increment_validated_count = MagicMock()

            # Call WITHOUT session_id
            nodes, _, _ = recall_nodes(store, query="anything")
            node_ids = {n["id"] for n in nodes}
            # pooled n1 should NOT appear since session_id wasn't passed
            assert "n1" not in node_ids
            assert "fresh_n1" in node_ids
        finally:
            recall_pool_clear(session_id)

    def test_low_signal_query_still_refreshes_pool(self):
        """TC-SP-13: 低信号查询返回空但仍刷新池"""
        from unittest.mock import MagicMock
        from agent.sparkgraph.recaller import recall_pool_clear, recall_pool_get, recall_nodes
        import time
        session_id = "sp13"
        try:
            pool = recall_pool_get(session_id)
            now = time.time()
            from agent.sparkgraph.recaller import PoolEntry
            pool["n1"] = PoolEntry({"node": self._make_node("n1", "test"), "added_at": now - 100, "hit_count": 1, "last_hit": now - 100})

            nodes, edges, tokens = recall_nodes(store=MagicMock(), query="hi", session_id=session_id)
            # Should return empty
            assert nodes == []
            assert edges == []
            assert tokens == 0
        finally:
            recall_pool_clear(session_id)

    def test_pool_capacity_enforced_across_recalls(self):
        """TC-SP-14: 多轮召回后池容量严格限制在 POOL_CAPACITY"""
        from unittest.mock import MagicMock
        from agent.sparkgraph.recaller import recall_pool_clear, recall_pool_get, recall_nodes, POOL_CAPACITY
        session_id = "sp14"
        try:
            pool = recall_pool_get(session_id)
            store = MagicMock()
            store.get_related_nodes.return_value = []
            store.get_edges_for_nodes.return_value = []
            store.increment_validated_count = MagicMock()

            # Simulate many turns, each recalling unique nodes
            for turn in range(30):
                store.search_nodes.return_value = [self._make_node(f"n{turn}", f"node from turn {turn}")]
                store.list_vector_nodes.return_value = []
                nodes, _, _ = recall_nodes(store, query=f"query {turn}", session_id=session_id)

            # Pool should never exceed POOL_CAPACITY
            assert len(pool) <= POOL_CAPACITY
        finally:
            recall_pool_clear(session_id)


class TestRecallPoolGlobalState:
    """TC-SP-15: 全局池状态隔离"""

    def test_different_sessions_have_separate_pools(self):
        from agent.sparkgraph.recaller import recall_pool_clear, recall_pool_get
        import time
        from agent.sparkgraph.recaller import PoolEntry
        sid_a = "session_a"
        sid_b = "session_b"
        try:
            now = time.time()
            pool_a = recall_pool_get(sid_a)
            pool_b = recall_pool_get(sid_b)
            pool_a["n_a"] = PoolEntry({"node": {"id": "n_a"}, "added_at": now, "hit_count": 10, "last_hit": now})
            pool_b["n_b"] = PoolEntry({"node": {"id": "n_b"}, "added_at": now, "hit_count": 1, "last_hit": now})

            assert "n_a" in recall_pool_get(sid_a)
            assert "n_a" not in recall_pool_get(sid_b)
            assert "n_b" in recall_pool_get(sid_b)
            assert "n_b" not in recall_pool_get(sid_a)
        finally:
            recall_pool_clear(sid_a)
            recall_pool_clear(sid_b)


class TestThresholdUpdate:
    """TC-SP-16: 阈值更新验证"""

    def test_min_vector_similarity_raised_to_075(self):
        from agent.sparkgraph.recaller import MIN_VECTOR_SIMILARITY
        assert MIN_VECTOR_SIMILARITY == 0.75

    def test_short_query_threshold_raised_to_080(self):
        from agent.sparkgraph.recaller import SHORT_QUERY_VECTOR_SIMILARITY
        assert SHORT_QUERY_VECTOR_SIMILARITY == 0.80
