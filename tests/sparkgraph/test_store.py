import json
import sqlite3

import pytest

from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType


def test_insert_node_round_trips(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="socksio may be required for SOCKS proxy support",
            canonical_key="fact:socksio-proxy",
            source_kind="flush",
        )
    )

    node = store.get_node(node_id)
    assert node is not None
    assert node["type"] == "FACT"
    assert node["canonical_key"] == "fact:socksio-proxy"


def test_increment_validated_count(tmp_path):
    """increment_validated_count increments validated_count (no meta redundant write)."""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.PREFERENCE,
            summary="user prefers concise replies",
            canonical_key="pref:concise-replies",
            source_kind="flush",
        )
    )

    store.increment_validated_count([node_id])
    node = store.get_node(node_id)
    assert node["validated_count"] == 1

    store.increment_validated_count([node_id])
    node = store.get_node(node_id)
    assert node["validated_count"] == 2

    # incrementing non-existent node is a no-op
    store.increment_validated_count(["nonexistent-id"])


def test_get_by_source_kind(tmp_path):
    """get_by_source_kind returns explicit/manual nodes filtered by query."""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")

    # Insert nodes with different source_kinds
    store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="proxy pac script setup guide",
            canonical_key="fact:proxy-pac-script",
            source_kind="explicit",
        )
    )
    explicit_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="docker compose port forwarding",
            canonical_key="fact:docker-compose-port-forwarding",
            source_kind="manual",
        )
    )
    flush_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.ISSUE,
            summary="proxy pac script not applied",
            canonical_key="issue:proxy-pac-not-applied",
            source_kind="flush",
        )
    )

    # Filter by source_kind only
    results = store.get_by_source_kind(["manual"])
    assert len(results) == 1
    assert results[0]["id"] == explicit_id

    results = store.get_by_source_kind(["explicit", "manual"])
    assert len(results) == 2
    ids = {r["id"] for r in results}
    assert explicit_id in ids
    assert flush_id not in ids  # flush is not in explicit/manual

    # Filter by source_kind + query keyword
    results = store.get_by_source_kind(["explicit", "manual"], "docker")
    assert len(results) == 1
    assert results[0]["id"] == explicit_id

    # Non-matching query returns empty
    results = store.get_by_source_kind(["explicit", "manual"], "nonexistent")
    assert results == []

    # Empty kinds returns empty
    results = store.get_by_source_kind([], "proxy")
    assert results == []


def test_default_inject_flag(tmp_path):
    """default_inject: explicit/manual→1, reflection/shadow→0, 默认→1."""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")

    explicit_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="explicit fact",
            canonical_key="fact:explicit-fact",
            source_kind="explicit",
            default_inject=True,
        )
    )
    reflection_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="reflection",
            canonical_key="fact:reflection",
            source_kind="reflection",
            default_inject=False,
        )
    )
    default_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="default",
            canonical_key="fact:default",
            source_kind="flush",
        )
    )

    explicit_node = store.get_node(explicit_id)
    reflection_node = store.get_node(reflection_id)
    default_node = store.get_node(default_id)

    assert explicit_node["default_inject"] == 1
    assert reflection_node["default_inject"] == 0
    assert default_node["default_inject"] == 1  # default=True


def test_merge_nodes(tmp_path):
    """merge_nodes: validated_count累加 + source_sessions合并 + 边迁移 + deprecated."""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")

    # Create two nodes
    keep_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="keep node",
            canonical_key="fact:keep-node",
            source_kind="explicit",
            status=NodeStatus.ACTIVE,
            confidence=0.90,
            meta={"source_sessions": ["session-alpha"]},
        )
    )
    merge_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="merge node",
            canonical_key="fact:merge-node",
            source_kind="manual",
            status=NodeStatus.ACTIVE,
            confidence=0.70,
            meta={"source_sessions": ["session-beta"]},
        )
    )

    # Create an edge from merge to another node
    other_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.ISSUE,
            summary="related issue",
            canonical_key="issue:related-issue",
            source_kind="flush",
        )
    )
    store.insert_edge(from_id=merge_id, to_id=other_id, edge_type=EdgeType.RELATED_TO, weight=0.5)

    # Merge
    store.merge_nodes(keep_id, merge_id)

    # keep: validated_count累加, sessions合并, status保持active
    keep = store.get_node(keep_id)
    assert keep["status"] == NodeStatus.ACTIVE.value
    assert keep["validated_count"] == 0  # neither had validated_count
    sessions = set(json.loads(keep["source_sessions"]))
    assert sessions == {"session-alpha", "session-beta"}

    # merge: deprecated
    merged = store.get_node(merge_id)
    assert merged["status"] == NodeStatus.DEPRECATED.value

    # edge migrated: from_id now points to keep_id
    rows = store.conn.execute(
        "SELECT * FROM sg_edges WHERE from_id = ?", (keep_id,)
    ).fetchall()
    assert len(rows) == 1

    # merge node has no outgoing edges left
    rows2 = store.conn.execute(
        "SELECT * FROM sg_edges WHERE from_id = ?", (merge_id,)
    ).fetchall()
    assert len(rows2) == 0


def test_merge_source_sessions(tmp_path):
    """merge_source_sessions adds new session ID (dedup path)."""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="some fact",
            canonical_key="fact:some-fact",
            source_kind="flush",
            meta={"source_sessions": ["session-alpha"]},
        )
    )

    # Merge new session
    store.merge_source_sessions(node_id, "session-beta")
    node = store.get_node(node_id)
    sessions = json.loads(node["source_sessions"])
    assert set(sessions) == {"session-alpha", "session-beta"}

    # Duplicate merge is a no-op
    store.merge_source_sessions(node_id, "session-beta")
    node = store.get_node(node_id)
    sessions = json.loads(node["source_sessions"])
    assert sessions.count("session-beta") == 1  # no duplication

    # Merge on non-existent node is a no-op
    store.merge_source_sessions("nonexistent-id", "session-gamma")


def test_search_nodes_uses_fts_sync(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.ISSUE,
            summary="libGL runtime errors can come from missing system packages",
            canonical_key="issue:libgl-runtime-packages",
            source_kind="flush",
        )
    )

    matches = store.search_nodes("libGL")
    assert len(matches) == 1
    assert matches[0]["type"] == "ISSUE"


def test_search_nodes_prefers_fts_relevance_before_recency(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    exact_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.ISSUE,
            summary="redis remote access troubleshooting",
            canonical_key="issue:redis-remote-access",
            source_kind="flush",
        )
    )
    partial_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.ISSUE,
            summary=(
                "redis remote access note with a lot of extra filler words that make "
                "this match noisier than the concise troubleshooting summary"
            ),
            canonical_key="issue:redis-troubleshooting-note",
            source_kind="flush",
        )
    )
    store.conn.execute("UPDATE sg_nodes SET updated_at = ? WHERE id = ?", (1, exact_id))
    store.conn.execute("UPDATE sg_nodes SET updated_at = ? WHERE id = ?", (999999999, partial_id))
    store.conn.commit()

    matches = store.search_nodes("redis remote access", limit=2)
    assert [match["id"] for match in matches[:2]] == [exact_id, partial_id]


def test_search_nodes_sanitizes_punctuation_heavy_query(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="chat-send helper works with structured prompts",
            canonical_key="fact:chat-send-helper",
            source_kind="flush",
        )
    )

    matches = store.search_nodes('chat-send AND ("oops"')
    assert isinstance(matches, list)


def test_search_nodes_returns_empty_for_fully_invalid_query(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    assert store.search_nodes('AND +++ ((( )))') == []


def test_store_respects_canonical_uniqueness(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.DECISION,
            summary="use podman for local testing",
            canonical_key="decision:use-podman",
            source_kind="flush",
        )
    )

    try:
        store.insert_node(
            SparkGraphNodeInput(
                type=NodeType.DECISION,
                summary="use podman instead of docker",
                canonical_key="decision:use-podman",
                source_kind="flush",
            )
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("Expected duplicate canonical decision to fail")


def test_vector_round_trip(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="postgres accepts remote clients only when configured",
            canonical_key="fact:postgres-remote-clients",
            source_kind="flush",
        )
    )

    store.upsert_vector(
        node_id=node_id,
        content_hash="hash-1",
        embedding=[0.25, 0.5, 0.75],
    )

    vector = store.get_vector(node_id)
    assert vector is not None
    assert vector["content_hash"] == "hash-1"
    assert vector["embedding"] == pytest.approx([0.25, 0.5, 0.75], rel=1e-6)
    assert store.count_vectors() == 1


def test_list_nodes_missing_vectors(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    with_vector = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="postgres remote access needs listen addresses",
            canonical_key="fact:postgres-listen-addresses",
            source_kind="flush",
        )
    )
    missing = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.ISSUE,
            summary="redis remote access often fails because bind is local-only",
            canonical_key="issue:redis-local-bind",
            source_kind="flush",
        )
    )
    store.upsert_vector(
        node_id=with_vector,
        content_hash="hash-2",
        embedding=[0.1, 0.2, 0.3],
    )

    rows = store.list_nodes_missing_vectors(limit=10)
    assert [row["id"] for row in rows] == [missing]


def test_mark_recalled_updates_timestamp_and_meta(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="postgres accepts remote clients only when configured",
            canonical_key="fact:postgres-remote-clients",
            source_kind="flush",
        )
    )

    store.mark_recalled([node_id], now_ts=1234567890)

    node = store.get_node(node_id)
    assert node["last_recalled_at"] == 1234567890
    assert '"recall_hits": 1' in node["meta"]
