import json

from agent.sparkgraph.store import SparkGraphStore
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
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    first = json.loads(
        sparkgraph_record_tool(
            items=[
                {
                    "summary": "Redis 明明启动了但应用始终连不上时，按顺序检查 bind、protected-mode 和端口映射",
                    "type": "ISSUE",
                    "evidence": "Redis 明明启动了但应用始终连不上时，先检查 bind、protected-mode 和端口映射。",
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
                    "summary": "排障经验：Redis 启动了但客户端仍然连不上时，优先检查 bind、protected-mode 和端口映射。",
                    "type": "FACT",
                    "evidence": "排障经验：Redis 启动后客户端仍连不上时，重点看 bind、protected-mode 和端口映射。",
                }
            ],
            store=store,
            session_id="session-1",
            turn_index=2,
        )
    )

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["updated"] == 1
    assert store.count_nodes() == 1


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
