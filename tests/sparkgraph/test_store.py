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


def test_append_evidence_is_deduplicated(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.PREFERENCE,
            summary="user prefers concise replies",
            canonical_key="pref:concise-replies",
            source_kind="flush",
        )
    )

    first = store.append_evidence(
        node_id=node_id,
        session_id="session-1",
        turn_index=3,
        source_text="Please keep replies concise.",
        source_kind="flush",
    )
    second = store.append_evidence(
        node_id=node_id,
        session_id="session-1",
        turn_index=3,
        source_text="Please keep replies concise.",
        source_kind="flush",
    )

    assert first is True
    assert second is False


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
