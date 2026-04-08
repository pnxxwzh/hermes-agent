"""Tests for simplified maintenance (no CANDIDATE, no evidence, no stability/reuse_score)."""

import time

from agent.sparkgraph.maintenance import run_flush_maintenance
from agent.sparkgraph.scoring import STALE_RECALL_DAYS
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import NodeStatus, NodeType


def test_run_flush_maintenance_never_counted_old_node_deprecated(tmp_path):
    """长期未召回且长期未更新的节点会自然过期，即使 validated_count=0。"""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Old node never counted",
            canonical_key="fact:old-never-counted",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.72,
        )
    )
    stale_ts = int(time.time()) - ((STALE_RECALL_DAYS + 1) * 24 * 60 * 60)
    # 31+ 天前创建，从未被计数：last_recalled_at=0, validated_count=0, updated_at=stale_ts
    store.conn.execute(
        "UPDATE sg_nodes SET updated_at=?, validated_count=0, last_recalled_at=0 WHERE id=?",
        (stale_ts, node_id),
    )
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
        )
    )

    result = run_flush_maintenance(store, now_ts=int(time.time()))
    node = store.get_node(node_id)

    assert result["deprecated"] == 0
    assert node["status"] == NodeStatus.ACTIVE.value


def test_run_flush_maintenance_dedup_does_not_reset_idle_time(tmp_path):
    """B1 core: dedup 刷新 updated_at 不应重置 idle 时间。

    场景：节点 31 天前被召回（last_recalled_at = 31天前，>= STALE_RECALL_DAYS），
    昨天做了同类型 dedup（updated_at = 昨天），
    修复前：reference_ts = updated_at = 昨天 → days_idle=1 → 不 deprecated（BUG）
    修复后：reference_ts = last_recalled_at = 31天前 → days_idle=31 → deprecated ✓
    """
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.ISSUE,
            summary="Docker build hangs on pip install",
            canonical_key="issue:docker-build-hangs-pip",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.72,
        )
    )
    now = int(time.time())
    recalled_31d_ago = now - ((STALE_RECALL_DAYS + 1) * 24 * 60 * 60)
    dedup_yesterday = now - (1 * 24 * 60 * 60)
    # 模拟：31天前被召回（validated_count=1, last_recalled_at=31天前）
    # 然后昨天做 dedup 更新（updated_at=昨天，detail 覆盖）
    store.conn.execute(
        "UPDATE sg_nodes SET validated_count=1, last_recalled_at=?, updated_at=? WHERE id=?",
        (recalled_31d_ago, dedup_yesterday, node_id),
    )
    store.conn.commit()

    result = run_flush_maintenance(store, now_ts=now)
    node = store.get_node(node_id)

    # 修复前：B4 → reference_ts=updated_at=昨天 → days_idle=1 < 30 → 不 deprecated（错误）
    # 修复后：last_recalled_at(31天前) > updated_at(昨天) → reference_ts=31天前 → deprecated ✓
    assert result["deprecated"] == 1, (
        f"dedup refreshed updated_at but last_recalled_at={STALE_RECALL_DAYS+1}d ago should "
        f"still trigger deprecation; got deprecated={result['deprecated']}"
    )
    assert node["status"] == NodeStatus.DEPRECATED.value


def test_run_flush_maintenance_last_recalled_at_zero_validated_positive_deprecated(tmp_path):
    """B1 legacy: last_recalled_at=0 但 validated_count>0（老数据）→ fallback 到 updated_at 正确淘汰。

    场景：节点创建于 35 天前，被计数过（validated_count=1），
    但 last_recalled_at 字段尚未写入（=0），
    updated_at 也是 35 天前（从未 dedup），
    期望 deprecated。
    """
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Legacy fact from pre-last_recalled_at era",
            canonical_key="fact:legacy-pre-era",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.72,
        )
    )
    now = int(time.time())
    created_35d_ago = now - (35 * 24 * 60 * 60)
    # 老数据：last_recalled_at 未写入（=0），validated_count=1，updated_at=创建时间
    store.conn.execute(
        "UPDATE sg_nodes SET validated_count=1, last_recalled_at=0, updated_at=? WHERE id=?",
        (created_35d_ago, node_id),
    )
    store.conn.commit()

    result = run_flush_maintenance(store, now_ts=now)
    node = store.get_node(node_id)

    assert result["deprecated"] == 1, (
        f"last_recalled_at=0 + validated_count=1 should fallback to updated_at "
        f"and deprecate; got deprecated={result['deprecated']}"
    )
    assert node["status"] == NodeStatus.DEPRECATED.value


def test_run_flush_maintenance_never_recalled_new_node_not_deprecated(tmp_path):
    """从未被召回的新节点（last_recalled_at=0, validated_count=0）→ 不淘汰。

    期望 idle=0，不满足 30 天条件。
    """
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Brand new node never recalled",
            canonical_key="fact:brand-new-never-recalled",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.72,
        )
    )
    # 新节点：last_recalled_at=0（默认），validated_count=0（默认），updated_at=now
    result = run_flush_maintenance(store, now_ts=int(time.time()))
    node = store.get_node(node_id)

    assert result["deprecated"] == 0
    assert node["status"] == NodeStatus.ACTIVE.value


def test_run_flush_maintenance_recent_recall_not_deprecated(tmp_path):
    """近期召回的节点（last_recalled_at recent）→ 即使 updated_at 很旧也不淘汰。

    场景：节点 5 天前被召回，updated_at 是 35 天前（从未 dedup），
    期望不淘汰（idle 以 last_recalled_at 为准）。
    """
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Recalled recently",
            canonical_key="fact:recalled-recently",
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.72,
        )
    )
    now = int(time.time())
    recalled_5d_ago = now - (5 * 24 * 60 * 60)
    created_35d_ago = now - (35 * 24 * 60 * 60)
    store.conn.execute(
        "UPDATE sg_nodes SET validated_count=1, last_recalled_at=?, updated_at=? WHERE id=?",
        (recalled_5d_ago, created_35d_ago, node_id),
    )
    store.conn.commit()

    result = run_flush_maintenance(store, now_ts=now)
    node = store.get_node(node_id)

    assert result["deprecated"] == 0, (
        f"last_recalled_at=5d ago should prevent deprecation even if "
        f"updated_at=35d ago; got deprecated={result['deprecated']}"
    )
    assert node["status"] == NodeStatus.ACTIVE.value
