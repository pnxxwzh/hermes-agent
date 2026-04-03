"""Integration tests for simplified SparkGraph v2 flow (no CANDIDATE, no evidence)."""

import json
import time
import pytest
from pathlib import Path
import tempfile

from agent.sparkgraph.store import SparkGraphStore
from agent.sparkgraph.scoring import initial_score_for
from agent.sparkgraph.types import NodeStatus, NodeType
from agent.sparkgraph.recaller import recall_nodes, RecallConfig


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "sg_test.db"


@pytest.fixture
def store(store_path):
    s = SparkGraphStore(Path(store_path))
    yield s
    s.close()


class TestSourceKindFate:
    """TC-I-01: flush 节点直接 ACTIVE，可立即召回"""

    def test_flush_node_is_active(self, store):
        score = initial_score_for("flush")
        assert score.initial_status == NodeStatus.ACTIVE
        assert score.confidence == 0.72

    def test_flush_node_recallable_immediately(self, store):
        from agent.sparkgraph.store import SparkGraphNodeInput
        node_id = store.insert_node(
            SparkGraphNodeInput(
                type=NodeType.ISSUE,
                summary="proxy pac script not applied",
                canonical_key="issue:proxy-pac-script-not-applied",
                source_kind="flush",
            )
        )
        nodes, _ = recall_nodes(store, query="proxy pac script")
        assert any(n["id"] == node_id for n in nodes), "flush node should be immediately recallable"


class TestReflectionDeprecated:
    """TC-I-02: reflection 节点直接 DEPRECATED，不可召回"""

    def test_reflection_node_is_deprecated(self, store):
        score = initial_score_for("reflection")
        assert score.initial_status == NodeStatus.DEPRECATED

    def test_reflection_node_not_recallable(self, store):
        from agent.sparkgraph.store import SparkGraphNodeInput
        score = initial_score_for("reflection")
        node_id = store.insert_node(
            SparkGraphNodeInput(
                type=NodeType.ISSUE,
                summary="old broken idea",
                canonical_key="issue:old-broken-idea",
                source_kind="reflection",
                status=score.initial_status,
                confidence=score.confidence,
            )
        )
        nodes, _ = recall_nodes(store, query="broken idea")
        assert not any(n["id"] == node_id for n in nodes), "deprecated node should not be recallable"


class TestValidatedCountIncrement:
    """TC-I-03: 节点被召回后 validated_count++"""

    def test_validated_count_increments_on_recall(self, store):
        from agent.sparkgraph.store import SparkGraphNodeInput
        node_id = store.insert_node(
            SparkGraphNodeInput(
                type=NodeType.FACT,
                summary="python is installed via brew",
                canonical_key="fact:python-is-installed-via-brew",
                source_kind="manual",
            )
        )
        # First recall
        nodes1, _ = recall_nodes(store, query="python brew")
        assert any(n["id"] == node_id for n in nodes1)

        node_after_1 = store.get_node(node_id)
        assert node_after_1["validated_count"] == 1

        # Second recall
        nodes2, _ = recall_nodes(store, query="python brew")
        assert any(n["id"] == node_id for n in nodes2)

        node_after_2 = store.get_node(node_id)
        assert node_after_2["validated_count"] == 2


class TestDedupUpdatesStatus:
    """TC-I-04: 去重更新——reflection 节点被 explicit 覆盖后变为 ACTIVE"""

    def test_dedup_overwrites_deprecated_to_active(self, store):
        from agent.sparkgraph.store import SparkGraphNodeInput
        from agent.sparkgraph.dedup import build_canonical_key

        # Insert a deprecated reflection node
        canonical = build_canonical_key(NodeType.ISSUE, "proxy pac script failure")
        reflection_score = initial_score_for("reflection")
        deprecated_id = store.insert_node(
            SparkGraphNodeInput(
                type=NodeType.ISSUE,
                summary="proxy pac script failure",
                canonical_key=canonical,
                source_kind="reflection",
                status=reflection_score.initial_status,
                confidence=reflection_score.confidence,
            )
        )
        node = store.get_node(deprecated_id)
        assert node["status"] == NodeStatus.DEPRECATED.value

        # Simulate dedup: update with explicit source_kind
        # In the tool this is done via sparkgraph_record_tool dedup path
        # Here we directly call update_node_scoring as the tool would
        score = initial_score_for("explicit")
        store.update_node_scoring(
            deprecated_id,
            confidence=score.confidence,
            stability=0.62,
            reuse_score=0.66,
            status=score.initial_status.value,
        )

        updated = store.get_node(deprecated_id)
        assert updated["status"] == NodeStatus.ACTIVE.value
        assert updated["confidence"] == 0.88


class TestMaintenanceDeprecation:
    """TC-I-05: 30天 idle + 低 validated_count → deprecated"""

    def test_stale_low_validated_node_deprecated(self, store):
        from agent.sparkgraph.store import SparkGraphNodeInput
        from agent.sparkgraph.maintenance import run_flush_maintenance

        node_id = store.insert_node(
            SparkGraphNodeInput(
                type=NodeType.ISSUE,
                summary="temporary environment issue",
                canonical_key="issue:temporary-env-issue",
                source_kind="auto",
            )
        )
        # Simulate old last_recalled_at (30+ days ago)
        old_ts = int(time.time()) - (31 * 86400)
        store._conn.execute(
            "UPDATE sg_nodes SET last_recalled_at = ?, updated_at = ? WHERE id = ?",
            (old_ts, old_ts, node_id),
        )
        store._conn.commit()

        result = run_flush_maintenance(store)

        node = store.get_node(node_id)
        assert node["status"] == NodeStatus.DEPRECATED.value

    def test_high_validated_node_not_deprecated(self, store):
        from agent.sparkgraph.store import SparkGraphNodeInput
        from agent.sparkgraph.maintenance import run_flush_maintenance

        node_id = store.insert_node(
            SparkGraphNodeInput(
                type=NodeType.ISSUE,
                summary="useful known issue",
                canonical_key="issue:useful-known-issue",
                source_kind="explicit",
            )
        )
        # Simulate high validated_count and old last_recalled_at
        old_ts = int(time.time()) - (31 * 86400)
        store._conn.execute(
            "UPDATE sg_nodes SET last_recalled_at = ?, updated_at = ?, validated_count = 5 WHERE id = ?",
            (old_ts, old_ts, node_id),
        )
        store._conn.commit()

        result = run_flush_maintenance(store)

        node = store.get_node(node_id)
        assert node["status"] == NodeStatus.ACTIVE.value
