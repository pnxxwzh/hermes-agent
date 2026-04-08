"""Tests for SparkGraph bug fixes and deleted-unused-features verification."""

import json
import time

import pytest

from agent.sparkgraph.config import SparkGraphConfig, SparkGraphEmbeddingConfig, SparkGraphRecallConfig
from agent.sparkgraph.dedup import find_dedup_match, find_cross_type_dedup_match
from agent.sparkgraph.manager import SparkGraphManager
from agent.sparkgraph.maintenance import run_flush_maintenance
from agent.sparkgraph.recaller import _list_active_vector_nodes, _vector_search, RecallConfig
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType


# ─── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def sg_store(tmp_path):
    db_path = tmp_path / "sparkgraph" / "default.db"
    config = SparkGraphConfig(
        mode="flush_integrated",
        db_path=db_path,
        recall=SparkGraphRecallConfig(enabled=True, max_items=4, max_chars=1800),
        embedding=SparkGraphEmbeddingConfig(),
    )
    manager = SparkGraphManager(config=config)
    return manager.ensure_store()


# ─── Fix 1: merge_nodes — scoped self-loop delete only affects merge node ───────


def test_merge_nodes_only_migrates_merge_edges(sg_store):
    """merge_nodes should only migrate edges involving merge_id, not unrelated edges."""
    keep_id = sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT, summary="Keep",
        canonical_key="fact:keep", source_kind="flush",
    ))
    merge_id = sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT, summary="Merge",
        canonical_key="fact:merge", source_kind="flush",
    ))
    a_id = sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT, summary="Node A",
        canonical_key="fact:node-a", source_kind="flush",
    ))
    b_id = sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT, summary="Node B",
        canonical_key="fact:node-b", source_kind="flush",
    ))

    # merge_id → a_id  (should be migrated to keep_id → a_id)
    sg_store.insert_edge(from_id=merge_id, to_id=a_id, edge_type=EdgeType.RELATED_TO)
    # a_id → merge_id  (should be migrated to a_id → keep_id)
    sg_store.insert_edge(from_id=a_id, to_id=merge_id, edge_type=EdgeType.RELATED_TO)
    # b_id → keep_id  (unrelated, should stay unchanged)
    sg_store.insert_edge(from_id=b_id, to_id=keep_id, edge_type=EdgeType.RELATED_TO)

    sg_store.merge_nodes(keep_id=keep_id, merge_id=merge_id)

    # keep_id → a_id: migrated from merge_id → a_id
    all_edges = sg_store.get_edges_for_nodes([keep_id, a_id, b_id, merge_id])
    keep_to_a = [e for e in all_edges if e["from_id"] == keep_id and e["to_id"] == a_id]
    assert len(keep_to_a) == 1, "keep→a edge should exist after migration"

    # a_id → keep_id: migrated from a_id → merge_id
    a_to_keep = [e for e in all_edges if e["from_id"] == a_id and e["to_id"] == keep_id]
    assert len(a_to_keep) == 1, "a→keep edge should exist after migration"

    # b_id → keep_id: unchanged
    b_to_keep = [e for e in all_edges if e["from_id"] == b_id and e["to_id"] == keep_id]
    assert len(b_to_keep) == 1, "b→keep edge should remain unchanged"

    # No edges should reference merge_id
    edges_to_merge = [e for e in all_edges if e["to_id"] == merge_id or e["from_id"] == merge_id]
    assert len(edges_to_merge) == 0, "No edges should reference the deprecated merge_id"


def test_merge_nodes_dedup_removes_duplicate_edges(sg_store):
    """merge_nodes dedup step removes duplicate edges after migration."""
    keep_id = sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT, summary="Keep",
        canonical_key="fact:keep", source_kind="flush",
    ))
    merge_id = sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT, summary="Merge",
        canonical_key="fact:merge", source_kind="flush",
    ))
    other_id = sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT, summary="Other",
        canonical_key="fact:other", source_kind="flush",
    ))

    # keep_id → other_id (original)
    sg_store.insert_edge(from_id=keep_id, to_id=other_id, edge_type=EdgeType.RELATED_TO)
    # merge_id → other_id (would create duplicate after migration)
    sg_store.insert_edge(from_id=merge_id, to_id=other_id, edge_type=EdgeType.RELATED_TO)

    sg_store.merge_nodes(keep_id=keep_id, merge_id=merge_id)

    # Should only have one keep_id → other_id edge (pre-delete dedup removed the duplicate)
    all_edges = sg_store.get_edges_for_nodes([keep_id, other_id])
    keep_to_other = [e for e in all_edges if e["from_id"] == keep_id and e["to_id"] == other_id]
    assert len(keep_to_other) == 1, "Duplicate edges should be deduplicated to one"


# ─── Fix 2: dedup — deprecated nodes excluded from near-duplicate matching ───────


def test_find_dedup_match_excludes_deprecated_nodes(sg_store):
    """find_dedup_match must not match deprecated nodes as dup candidates."""
    from agent.sparkgraph.dedup import build_canonical_key

    summary = "Redis connection refused fix"
    active_node = sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.ISSUE,
        summary=summary,
        canonical_key=build_canonical_key(NodeType.ISSUE, summary),
        source_kind="flush",
    ))

    # Deprecated node with same type and same summary (near-duplicate)
    sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.ISSUE,
        summary=summary,
        canonical_key=build_canonical_key(NodeType.ISSUE, summary) + "-old",
        source_kind="reflection",
        status=NodeStatus.DEPRECATED,
    ))

    match = find_dedup_match(
        sg_store,
        summary=summary,
        node_type=NodeType.ISSUE,
        canonical_key=build_canonical_key(NodeType.ISSUE, summary),
    )

    # Should find the ACTIVE node, not the deprecated one
    assert match is not None
    assert match.node_id == active_node
    assert match.match_type == "exact"


def test_find_cross_type_dedup_match_excludes_deprecated(sg_store):
    """find_cross_type_dedup_match must not match deprecated nodes."""
    summary = "Docker port mapping problem"

    # ACTIVE ISSUE
    active_issue = sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.ISSUE,
        summary=summary,
        canonical_key="issue:docker-port-mapping",
        source_kind="flush",
    ))

    # DEPRECATED FACT with same summary (cross-type potential dup)
    sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT,
        summary=summary,
        canonical_key="fact:docker-port-mapping",
        source_kind="reflection",
        status=NodeStatus.DEPRECATED,
    ))

    match = find_cross_type_dedup_match(
        sg_store,
        node_types=(NodeType.ISSUE, NodeType.FACT),
        summary=summary,
    )

    # Should find the ACTIVE issue node, not the deprecated fact
    assert match is not None
    assert match.node_id == active_issue


def test_dedup_still_matches_active_near_duplicates(sg_store):
    """Near-duplicate matching still works for active nodes."""
    from agent.sparkgraph.dedup import build_canonical_key

    summary = "PostgreSQL authentication configuration"
    canonical = build_canonical_key(NodeType.ISSUE, summary)
    existing = sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.ISSUE,
        summary=summary,
        canonical_key=canonical,
        source_kind="flush",
    ))

    match = find_dedup_match(
        sg_store,
        summary=summary,
        node_type=NodeType.ISSUE,
        canonical_key=canonical,
    )

    assert match is not None
    assert match.node_id == existing
    assert match.match_type == "exact"


# ─── Fix 3: maintenance — scanned count correctly returned ──────────────────────


def test_run_flush_maintenance_returns_scanned_count(sg_store):
    """run_flush_maintenance must return the number of active nodes scanned."""
    # Insert a few nodes
    sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT,
        summary="Fact 1",
        canonical_key="fact:one",
        source_kind="flush",
    ))
    sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.ISSUE,
        summary="Issue 2",
        canonical_key="issue:two",
        source_kind="flush",
    ))

    result = run_flush_maintenance(sg_store)

    assert "scanned" in result
    assert result["scanned"] == 2
    assert result["deprecated"] == 0  # none should be deprecated


def test_manager_passes_session_id_into_recall(tmp_path, monkeypatch):
    """build_recall_block should forward session_id so session pool can work."""
    db_path = tmp_path / "sparkgraph" / "default.db"
    config = SparkGraphConfig(
        mode="flush_integrated",
        db_path=db_path,
        recall=SparkGraphRecallConfig(enabled=True, max_items=4, max_chars=1800),
        embedding=SparkGraphEmbeddingConfig(),
    )
    manager = SparkGraphManager(config=config)

    captured = {}

    def _fake_recall_nodes(
        store,
        *,
        query,
        config,
        embedding_config,
        session_id=None,
        persist_feedback=True,
    ):
        captured["query"] = query
        captured["session_id"] = session_id
        captured["persist_feedback"] = persist_feedback
        return [], [], 0

    monkeypatch.setattr("agent.sparkgraph.manager.recall_nodes", _fake_recall_nodes)

    manager.build_recall_block("proxy config", session_id="sess-42")

    assert captured == {
        "query": "proxy config",
        "session_id": "sess-42",
        "persist_feedback": False,
    }


def test_run_flush_maintenance_scanned_reflects_deprecations(sg_store):
    """scanned count includes nodes that get deprecated in the same run."""
    node_id = sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT,
        summary="Stale fact",
        canonical_key="fact:stale",
        source_kind="flush",
    ))

    # Backdate the node so it looks stale
    old_ts = int(time.time()) - 35 * 86400
    sg_store._conn.execute(
        "UPDATE sg_nodes SET last_recalled_at = ?, updated_at = ? WHERE id = ?",
        (old_ts, old_ts, node_id),
    )
    sg_store._conn.commit()

    result = run_flush_maintenance(sg_store)

    assert result["scanned"] == 1
    assert result["deprecated"] == 1


# ─── Fix 4: _list_active_vector_nodes — no new store connection created ─────────


def test_list_active_vector_nodes_accepts_store_parameter(sg_store):
    """_list_active_vector_nodes must accept a store parameter and not open new connections."""
    # Insert a node (no vector — list_vector_nodes filters to only nodes with vectors)
    node_id = sg_store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT,
        summary="Test node",
        canonical_key="fact:test-node",
        source_kind="flush",
    ))

    # Calling with store should not raise TypeError (previously crashed with
    # "missing 1 required positional argument: 'store'" after the signature change)
    result = _list_active_vector_nodes(sg_store)
    assert isinstance(result, list)


def test_vector_search_accepts_store_parameter(sg_store):
    """_vector_search must accept store as first positional arg after the fix."""
    config = RecallConfig(max_nodes=4, related_limit=2)

    # Should not raise TypeError
    result = _vector_search(sg_store, "test query", config, None)
    assert isinstance(result, list)


# ─── Fix 5: sparkgraph_search and sparkgraph_stats not in flush tool list ─────


def test_sparkgraph_search_and_stats_removed_from_registry():
    """sparkgraph_search and sparkgraph_stats must be removed from registry.register calls."""
    import re

    source = open("/Users/wzh/isachermes/tools/sparkgraph_tool.py").read()
    # Should have exactly 1 registry.register call (for sparkgraph_record only)
    register_calls = re.findall(r'registry\.register\s*\(\s*name\s*=\s*"([^"]+)"', source)

    assert register_calls == ["sparkgraph_record"], \
        f"Expected only sparkgraph_record in registry, found: {register_calls}"

    # sparkgraph_search and sparkgraph_stats functions still exist for debugging
    from tools.sparkgraph_tool import sparkgraph_search_tool, sparkgraph_stats_tool
    assert callable(sparkgraph_search_tool)
    assert callable(sparkgraph_stats_tool)


def test_search_and_stats_functions_still_callable_for_debugging():
    """sparkgraph_search and sparkgraph_stats functions still exist for direct use."""
    from tools.sparkgraph_tool import sparkgraph_search_tool, sparkgraph_stats_tool

    # Functions exist and are callable (actual DB not set up here, just import check)
    assert callable(sparkgraph_search_tool)
    assert callable(sparkgraph_stats_tool)
