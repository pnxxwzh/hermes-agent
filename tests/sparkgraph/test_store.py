import sqlite3

import pytest

from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import NodeType


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
