import time

from agent.sparkgraph.maintenance import STALE_CANDIDATE_DAYS, run_flush_maintenance
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import NodeStatus, NodeType


def test_run_flush_maintenance_deprecates_stale_low_signal_candidate(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Transient low-signal candidate",
            canonical_key="fact:transient-low-signal-candidate",
            source_kind="flush",
            status=NodeStatus.CANDIDATE,
            confidence=0.40,
            stability=0.30,
            reuse_score=0.20,
        )
    )
    store.append_evidence(
        node_id=node_id,
        session_id="session-1",
        turn_index=1,
        source_text="single weak evidence",
        source_kind="flush",
    )
    stale_ts = int(time.time()) - ((STALE_CANDIDATE_DAYS + 1) * 24 * 60 * 60)
    store.conn.execute("UPDATE sg_nodes SET updated_at = ? WHERE id = ?", (stale_ts, node_id))
    store.conn.commit()

    result = run_flush_maintenance(store, now_ts=int(time.time()))
    node = store.get_node(node_id)

    assert result["deprecated"] == 1
    assert node["status"] == NodeStatus.DEPRECATED.value


def test_run_flush_maintenance_keeps_recent_candidate(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.PREFERENCE,
            summary="User prefers concise replies",
            canonical_key="preference:user-prefers-concise-replies",
            source_kind="flush",
            status=NodeStatus.CANDIDATE,
            confidence=0.60,
            stability=0.70,
            reuse_score=0.70,
        )
    )
    store.append_evidence(
        node_id=node_id,
        session_id="session-1",
        turn_index=1,
        source_text="Please keep replies concise.",
        source_kind="flush",
    )

    result = run_flush_maintenance(store, now_ts=int(time.time()))
    node = store.get_node(node_id)

    assert result["deprecated"] == 0
    assert node["status"] == NodeStatus.CANDIDATE.value


def test_run_flush_maintenance_deprecates_stale_low_signal_active(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Old weak active guidance",
            canonical_key="fact:old-weak-active-guidance",
            source_kind="auto",
            status=NodeStatus.ACTIVE,
            confidence=0.62,
            stability=0.30,
            reuse_score=0.20,
            meta={"recall_hits": 0},
        )
    )
    store.append_evidence(
        node_id=node_id,
        session_id="session-1",
        turn_index=1,
        source_text="single weak evidence",
        source_kind="auto",
    )
    stale_ts = int(time.time()) - 45 * 24 * 60 * 60
    store.conn.execute(
        "UPDATE sg_nodes SET last_recalled_at = ?, updated_at = ? WHERE id = ?",
        (stale_ts, stale_ts, node_id),
    )
    store.conn.commit()

    result = run_flush_maintenance(store, now_ts=int(time.time()))
    node = store.get_node(node_id)

    assert result["deprecated"] == 1
    assert node["status"] == NodeStatus.DEPRECATED.value
