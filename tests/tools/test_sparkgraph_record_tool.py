import json
from unittest.mock import patch

from agent.sparkgraph.store import SparkGraphStore
from agent.sparkgraph.types import NodeType, NodeStatus, EdgeType
from tools.sparkgraph_tool import sparkgraph_record_tool


def test_sparkgraph_record_creates_nodes(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    result = json.loads(
        sparkgraph_record_tool(
            items=[
                {
                    "summary": "socksio may be required for SOCKS proxy support",
                    "type": "FACT",
                    "evidence": "Check whether socksio is installed when SOCKS proxy errors appear.",
                }
            ],
            store=store,
            session_id="session-1",
            turn_index=4,
        )
    )
    assert result["success"] is True
    assert result["created"] == 1
    assert result["updated"] == 0


def test_sparkgraph_record_rejects_invalid_payload(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    result = json.loads(
        sparkgraph_record_tool(
            items=[{"summary": "missing evidence", "type": "FACT"}],
            store=store,
            session_id="session-1",
            turn_index=1,
        )
    )
    assert result["success"] is True
    assert result["created"] == 0
    assert result["rejected"] == 1


def test_sparkgraph_record_updates_existing_node(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    first = json.loads(
        sparkgraph_record_tool(
            items=[
                {
                    "summary": "user prefers concise replies",
                    "type": "PREFERENCE",
                    "evidence": "Please keep replies concise.",
                }
            ],
            store=store,
            session_id="session-1",
            turn_index=1,
        )
    )
    second = json.loads(
        sparkgraph_record_tool(
            items=[
                {
                    "summary": "User prefers concise replies.",
                    "type": "PREFERENCE",
                    "evidence": "Keep answers short in future.",
                }
            ],
            store=store,
            session_id="session-1",
            turn_index=2,
        )
    )
    assert first["created"] == 1
    assert second["updated"] == 1


def test_sparkgraph_record_merges_cross_type_troubleshooting_duplicates(tmp_path):
    """Cross-type dedup (ISSUE+FACT 同 summary) → 插入 FACT → merge_nodes(keep=ISSUE, merge=FACT)。
    merge 节点 deprecated，边迁移到 keep，validated_count 累加，sessions 合并。"""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")

    first = json.loads(
        sparkgraph_record_tool(
            items=[
                {
                    "summary": "Redis bind 配置检查顺序：先查 bind，再查 protected-mode，最后看端口映射",
                    "type": "ISSUE",
                    "evidence": "Redis 连不上时，按 bind → protected-mode → 端口映射顺序排查。",
                }
            ],
            store=store,
            session_id="session-alpha",
            turn_index=1,
        )
    )
    issue_id = first["recorded_ids"][0]

    second = json.loads(
        sparkgraph_record_tool(
            items=[
                {
                    "summary": "Redis bind 配置检查顺序：先查 bind，再查 protected-mode，最后看端口映射",
                    "type": "FACT",
                    "evidence": "Redis 连不上时，顺序检查 bind、protected-mode、端口映射。",
                }
            ],
            store=store,
            session_id="session-beta",
            turn_index=2,
        )
    )
    # cross-type dedup: updated=1, created=0, recorded_id = keep (ISSUE)
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["updated"] == 1
    assert second["recorded_ids"][0] == issue_id, "recorded_id should be the keep node (ISSUE)"

    # 两个节点存在（ISSUE=keep, FACT=deprecated after merge）
    assert store.count_nodes() == 2

    issue_node = store.get_node(issue_id)
    assert issue_node["status"] == "active"
    assert set(json.loads(issue_node["source_sessions"])) == {"session-alpha", "session-beta"}, \
        "sessions should be merged from both records"
    assert issue_node["validated_count"] == 0

    # merge 节点的 ID 未记录在 recorded_ids（指向 keep 节点），
    # 通过 total count=2 + deprecated count=1 确认 merge 节点存在且已被 deprecated。
    deprecated_count = store.conn.execute(
        "SELECT COUNT(*) FROM sg_nodes WHERE status = ?", ("deprecated",)
    ).fetchone()[0]
    assert deprecated_count == 1, "one node should be deprecated (the merged FACT)"


def test_sparkgraph_record_cross_type_dedup_upserts_vector_on_keep_node(tmp_path):
    """Cross-type dedup should refresh the surviving node's embedding, not the merged stub."""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")

    first = json.loads(
        sparkgraph_record_tool(
            items=[
                {
                    "summary": "Use socksio for SOCKS proxy support",
                    "type": "FACT",
                    "evidence": "SOCKS proxy errors often mean socksio is missing.",
                }
            ],
            store=store,
            session_id="session-alpha",
            turn_index=1,
            source_kind="flush",
        )
    )
    keep_id = first["recorded_ids"][0]

    with (
        patch("tools.sparkgraph_tool.embedding_enabled", return_value=True),
        patch("tools.sparkgraph_tool.create_embedding", return_value=[0.1, 0.2]),
        patch("tools.sparkgraph_tool.embedding_content_hash", return_value="hash-1"),
    ):
        second = json.loads(
            sparkgraph_record_tool(
                items=[
                    {
                        "summary": "Use socksio for SOCKS proxy support",
                        "type": "ISSUE",
                        "evidence": "Install socksio when SOCKS connections fail.",
                    }
                ],
                store=store,
                session_id="session-beta",
                turn_index=2,
                source_kind="flush",
                embedding_config=object(),
            )
        )

    assert second["updated"] == 1
    assert second["recorded_ids"] == [keep_id]
    assert store.get_vector(keep_id) is not None

    deprecated_rows = store.list_nodes(status=NodeStatus.DEPRECATED.value)
    assert len(deprecated_rows) == 1
    assert store.get_vector(deprecated_rows[0]["id"]) is None


def test_sparkgraph_record_links_related_batch_items(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    result = json.loads(
        sparkgraph_record_tool(
            items=[
                {
                    "summary": "Redis 远程连接失败时优先检查 bind 和 protected-mode 配置",
                    "type": "ISSUE",
                    "evidence": "Redis 远程客户端连不上时，优先检查 bind 和 protected-mode。",
                },
                {
                    "summary": "Redis 远程连接排查还要确认容器或主机端口映射是否正确",
                    "type": "RESOURCE",
                    "evidence": "Redis 连不上时，也要检查 docker 端口映射和防火墙。",
                },
            ],
            store=store,
            session_id="session-1",
            turn_index=3,
            source_kind="auto",
        )
    )

    assert result["created"] == 2
    assert result["related_edges_created"] == 1
    related = store.get_related_nodes([result["recorded_ids"][0]], active_only=False, limit=4)
    related_ids = {row["id"] for row in related}
    assert related_ids == {result["recorded_ids"][1]}


def test_sparkgraph_record_skips_edges_for_unrelated_batch_items(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    result = json.loads(
        sparkgraph_record_tool(
            items=[
                {
                    "summary": "PostgreSQL 连接超时时先检查监听地址和 pg_hba.conf",
                    "type": "ISSUE",
                    "evidence": "PostgreSQL 连接超时先检查监听地址和 pg_hba.conf。",
                },
                {
                    "summary": "User prefers concise replies without repeated explanations",
                    "type": "PREFERENCE",
                    "evidence": "请回答简洁直接，不要重复解释。",
                },
            ],
            store=store,
            session_id="session-1",
            turn_index=4,
            source_kind="auto",
        )
    )

    assert result["created"] == 2
    assert result["related_edges_created"] == 0
    related = store.get_related_nodes(result["recorded_ids"], active_only=False, limit=4)
    assert related == []


def test_sparkgraph_record_cross_type_dedup_edges_migrated(tmp_path):
    """Cross-type dedup → merge_nodes：merge 节点的边迁移到 keep 节点。"""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")

    # Create an unrelated ISSUE node (will become the 'other' endpoint)
    unrelated = json.loads(
        sparkgraph_record_tool(
            items=[{
                "summary": "Docker daemon not running causes connection errors",
                "type": "ISSUE",
                "evidence": "Docker 客户端连不上时，检查 dockerd 是否在运行。",
            }],
            store=store,
            session_id="session-root",
            turn_index=1,
        )
    )
    other_id = unrelated["recorded_ids"][0]

    # Insert ISSUE that will become the keep node
    keep_result = json.loads(
        sparkgraph_record_tool(
            items=[{
                "summary": "Etcd raft group lost leader causes write failures",
                "type": "ISSUE",
                "evidence": "Etcd 写入失败时，检查是否存在 raft leader。",
            }],
            store=store,
            session_id="session-root",
            turn_index=2,
        )
    )
    keep_id = keep_result["recorded_ids"][0]

    # Manually insert the merge node (simulating it was inserted before dedup check)
    # This is hard to trigger naturally, so we directly test the store merge_nodes
    from agent.sparkgraph.store import SparkGraphNodeInput
    from agent.sparkgraph.types import NodeStatus
    merge_id = store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT,
        summary="Etcd raft group lost leader causes write failures",
        detail="Check for raft leader",
        canonical_key="fact:etcd-raft-lost-leader",
        source_kind="explicit",
        status=NodeStatus.ACTIVE,
        confidence=0.85,
    ))
    # Add an edge from merge node to unrelated
    store.insert_edge(from_id=merge_id, to_id=other_id, edge_type=EdgeType.RELATED_TO)

    # Call merge_nodes
    store.merge_nodes(keep_id=keep_id, merge_id=merge_id)

    # Edge should now be from keep_id to other_id (migrated)
    edges = store.get_edges_for_nodes([keep_id, other_id])
    migrated = [e for e in edges if e["from_id"] == keep_id and e["to_id"] == other_id]
    assert len(migrated) == 1, "edge should be migrated from merge to keep"

    # merge_id should have no outgoing edges
    merge_edges = store.get_edges_for_nodes([merge_id])
    assert len(merge_edges) == 0, "merge node should have no edges"

    # merge node should be deprecated
    merge_node = store.get_node(merge_id)
    assert merge_node["status"] == "deprecated"


def test_sparkgraph_record_same_type_dedup_no_new_node(tmp_path):
    """Same-type exact dedup：不插入新节点，只更新现有节点。"""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")

    first = json.loads(
        sparkgraph_record_tool(
            items=[{
                "summary": "macOS notarization required for distribution outside App Store",
                "type": "FACT",
                "evidence": "macOS 应用在 App Store 之外分发需要 notarization。",
            }],
            store=store,
            session_id="s1",
            turn_index=1,
        )
    )
    node_id = first["recorded_ids"][0]

    second = json.loads(
        sparkgraph_record_tool(
            items=[{
                "summary": "macOS notarization required for distribution outside App Store",
                "type": "FACT",
                "evidence": "Notarization is mandatory for distribution outside App Store.",
            }],
            store=store,
            session_id="s2",
            turn_index=2,
        )
    )

    # Same-type dedup: updated=1, created=0, same node id
    assert second["created"] == 0
    assert second["updated"] == 1
    assert second["recorded_ids"][0] == node_id
    assert store.count_nodes() == 1, "no new node should be created"

    # source_sessions should be merged
    node = store.get_node(node_id)
    sessions = json.loads(node["source_sessions"])
    assert set(sessions) == {"s1", "s2"}, "sessions from both records should be merged"


def test_sparkgraph_record_same_type_dedup_updates_detail(tmp_path):
    """Issue #2 fix: same-type dedup hit must update the node's detail field.

    The second record with the same canonical_key carries newer/different evidence.
    The detail column (evidence) must be overwritten with the latest evidence,
    not left stale from the first insert.
    """
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")

    first = json.loads(
        sparkgraph_record_tool(
            items=[{
                "summary": "Redis bind config order check",
                "type": "ISSUE",
                "evidence": "Step 1: check bind in redis.conf",
            }],
            store=store,
            session_id="s1",
            turn_index=1,
        )
    )
    node_id = first["recorded_ids"][0]

    first_node = store.get_node(node_id)
    assert first_node["detail"] == "Step 1: check bind in redis.conf"

    second = json.loads(
        sparkgraph_record_tool(
            items=[{
                "summary": "Redis bind config order check",
                "type": "ISSUE",
                # New, richer evidence that must replace the old one
                "evidence": "Full order: bind → protected-mode → port mapping",
            }],
            store=store,
            session_id="s2",
            turn_index=2,
        )
    )

    assert second["updated"] == 1
    assert second["created"] == 0
    assert second["recorded_ids"][0] == node_id

    updated_node = store.get_node(node_id)
    assert updated_node["detail"] == "Full order: bind → protected-mode → port mapping", (
        "detail (evidence) must be updated on same-type dedup, not left stale"
    )


def test_sparkgraph_record_race_condition_recovery_updates_detail(tmp_path):
    """IntegrityError race condition path must also update detail."""
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")

    first = json.loads(
        sparkgraph_record_tool(
            items=[{
                "summary": "Concurrent write test",
                "type": "FACT",
                "evidence": "First evidence",
            }],
            store=store,
            session_id="s1",
            turn_index=1,
        )
    )
    node_id = first["recorded_ids"][0]

    # Simulate a race: a concurrent writer inserted the same canonical_key.
    # We call insert_node directly (bypassing dedup check) to trigger IntegrityError,
    # then verify the recovery path updates detail.
    from agent.sparkgraph.store import SparkGraphNodeInput
    from agent.sparkgraph.types import NodeStatus

    # Insert with the same canonical_key directly to force IntegrityError
    from agent.sparkgraph.dedup import build_canonical_key
    canonical = build_canonical_key("FACT", "Concurrent write test")

    try:
        store.insert_node(
            SparkGraphNodeInput(
                type=NodeType.FACT,
                summary="Concurrent write test",
                canonical_key=canonical,
                source_kind="flush",
                status=NodeStatus.ACTIVE,
                confidence=0.8,
                detail="Concurrent evidence",
            )
        )
    except Exception:
        pass  # Expected if canonical_key UNIQUE constraint fires

    third = json.loads(
        sparkgraph_record_tool(
            items=[{
                "summary": "Concurrent write test",
                "type": "FACT",
                "evidence": "Recovery path evidence",
            }],
            store=store,
            session_id="s3",
            turn_index=3,
        )
    )

    # Should succeed via the race-recovery path (updated=1)
    assert third["updated"] == 1
    updated_node = store.get_node(node_id)
    assert updated_node["detail"] == "Recovery path evidence", (
        "race-recovery path must also update detail"
    )
