from agent.sparkgraph.dedup import build_canonical_key, find_cross_type_dedup_match, find_dedup_match
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import NodeType


def test_build_canonical_key_is_stable_for_punctuation_variants():
    left = build_canonical_key(NodeType.FACT, "SOCKS proxy support requires socksio.")
    right = build_canonical_key(NodeType.FACT, "SOCKS proxy support requires socksio!")
    assert left == right


def test_find_dedup_match_returns_exact_match(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    canonical_key = build_canonical_key(NodeType.PREFERENCE, "user prefers concise replies")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.PREFERENCE,
            summary="user prefers concise replies",
            canonical_key=canonical_key,
            source_kind="flush",
        )
    )

    match = find_dedup_match(
        store,
        node_type=NodeType.PREFERENCE,
        summary="user prefers concise replies",
        canonical_key=canonical_key,
    )
    assert match is not None
    assert match.node_id == node_id
    assert match.match_type == "exact"


def test_near_duplicate_merge_is_stable(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.ISSUE,
            summary="libGL runtime errors can come from missing system packages",
            canonical_key=build_canonical_key(
                NodeType.ISSUE,
                "libGL runtime errors can come from missing system packages",
            ),
            source_kind="flush",
        )
    )

    match = find_dedup_match(
        store,
        node_type=NodeType.ISSUE,
        summary="Missing system packages can cause libGL runtime errors",
        canonical_key=build_canonical_key(
            NodeType.ISSUE,
            "Missing system packages can cause libGL runtime errors",
        ),
    )
    assert match is not None
    assert match.node_id == node_id
    assert match.match_type == "near"


def test_cross_type_duplicate_match_prevents_fact_issue_fork(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    node_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.ISSUE,
            summary="Redis 明明启动了但应用始终连不上时，按顺序检查 bind、protected-mode 和端口映射",
            canonical_key=build_canonical_key(
                NodeType.ISSUE,
                "Redis 明明启动了但应用始终连不上时，按顺序检查 bind、protected-mode 和端口映射",
            ),
            source_kind="flush",
        )
    )

    match = find_cross_type_dedup_match(
        store,
        node_types=(NodeType.ISSUE, NodeType.FACT),
        summary="排障经验：Redis 启动了但客户端仍然连不上时，优先检查 bind、protected-mode 和端口映射。",
    )

    assert match is not None
    assert match.node_id == node_id
    assert match.node_type == "ISSUE"
