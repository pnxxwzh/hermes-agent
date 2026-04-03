"""Tests for simplified maintenance (no CANDIDATE, no evidence)."""

import time

from agent.sparkgraph.maintenance import run_flush_maintenance
from agent.sparkgraph.scoring import STALE_RECALL_DAYS
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import NodeStatus, NodeType


def test_run_flush_maintenance_deprecates_stale_low_signal_active(tmp_path):
    """ACTIVE node idle 30+ days + low validated_count + low stability → deprecated."""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Transient low-signal guidance",
            canonical_key="fact:transient-low-signal-guidance",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.72,
            stability=0.30,
            reuse_score=0.20,
        )
    )
    stale_ts = int(time.time()) - ((STALE_RECALL_DAYS + 1) * 24 * 60 * 60)
    store.conn.execute("UPDATE sg_nodes SET updated_at = ?, validated_count=0 WHERE id = ?", (stale_ts, node_id))
    store.conn.commit()

    result = run_flush_maintenance(store, now_ts=int(time.time()))
    node = store.get_node(node_id)

    assert result["deprecated"] == 1
    assert node["status"] == NodeStatus.DEPRECATED.value


def test_run_flush_maintenance_keeps_high_validated_node(tmp_path):
    """即使 idle 30 天，高 validated_count 节点保持 active。"""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.PREFERENCE,
            summary="User prefers concise replies",
            canonical_key="preference:user-prefers-concise-replies",
            source_kind="manual",
            status=NodeStatus.ACTIVE,
            confidence=0.90,
            stability=0.80,
            reuse_score=0.78,
        )
    )
    stale_ts = int(time.time()) - ((STALE_RECALL_DAYS + 1) * 24 * 60 * 60)
    store.conn.execute(
        "UPDATE sg_nodes SET updated_at=?, validated_count=5 WHERE id=?",
        (stale_ts, node_id),
    )
    store.conn.commit()

    result = run_flush_maintenance(store, now_ts=int(time.time()))
    node = store.get_node(node_id)

    assert result["deprecated"] == 0
    assert node["status"] == NodeStatus.ACTIVE.value


def test_run_flush_maintenance_keeps_recent_node(tmp_path):
    """近期更新的节点保持 active。"""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Recent fact",
            canonical_key="fact:recent-fact",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.72,
            stability=0.62,
            reuse_score=0.66,
        )
    )

    result = run_flush_maintenance(store, now_ts=int(time.time()))
    node = store.get_node(node_id)

    assert result["deprecated"] == 0
    assert node["status"] == NodeStatus.ACTIVE.value
