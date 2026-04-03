from agent.sparkgraph.recaller import RecallConfig, recall_nodes
from agent.sparkgraph.maintenance import run_flush_maintenance
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType
from agent.sparkgraph.config import SparkGraphEmbeddingConfig


def _insert_active_node(
    store: SparkGraphStore,
    *,
    node_type: NodeType,
    summary: str,
    canonical_key: str,
    confidence: float = 0.8,
    stability: float = 0.8,
    reuse_score: float = 0.7,
):
    return store.insert_node(
        SparkGraphNodeInput(
            type=node_type,
            summary=summary,
            canonical_key=canonical_key,
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=confidence,
            stability=stability,
            reuse_score=reuse_score,
        )
    )


def test_recall_nodes_filters_to_active_and_high_confidence(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    good_id = _insert_active_node(
        store,
        node_type=NodeType.FACT,
        summary="socksio may be required for SOCKS proxy support",
        canonical_key="fact:socksio-required",
    )
    low_conf_id = _insert_active_node(
        store,
        node_type=NodeType.FACT,
        summary="socksio can sometimes matter",
        canonical_key="fact:socksio-sometimes",
        confidence=0.4,
    )
    assert good_id != low_conf_id

    nodes, _edges = recall_nodes(store, query="socksio proxy", config=RecallConfig(max_nodes=4))
    assert [node["id"] for node in nodes] == [good_id]


def test_recall_nodes_expands_one_hop_related_nodes(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    issue_id = _insert_active_node(
        store,
        node_type=NodeType.ISSUE,
        summary="libGL errors can break headless browser startup",
        canonical_key="issue:libgl-headless-browser",
    )
    resource_id = _insert_active_node(
        store,
        node_type=NodeType.RESOURCE,
        summary="Install libgl1-mesa-glx when Playwright reports libGL missing",
        canonical_key="resource:install-libgl1-mesa-glx",
    )
    store.insert_edge(
        from_id=issue_id,
        to_id=resource_id,
        edge_type=EdgeType.RELATED_TO,
    )

    nodes, _edges = recall_nodes(
        store,
        query="libGL browser startup",
        config=RecallConfig(search_limit=4, related_limit=4, max_nodes=4),
    )
    node_ids = {node["id"] for node in nodes}
    assert issue_id in node_ids
    assert resource_id in node_ids


def test_manager_recall_respects_disabled_config(tmp_path):
    from agent.sparkgraph.manager import SparkGraphManager

    manager = SparkGraphManager.from_raw_config(
        {
            "mode": "flush_integrated",
            "recall": {
                "enabled": False,
                "max_items": 4,
                "max_related": 4,
                "budget_ratio": 0.12,
                "max_chars": 1800,
            },
        },
        hermes_home=tmp_path,
    )
    assert manager.build_recall_block("anything") == ""


def test_manager_recall_uses_configured_limits(tmp_path):
    from agent.sparkgraph.manager import SparkGraphManager

    manager = SparkGraphManager.from_raw_config(
        {
            "mode": "flush_integrated",
            "recall": {
                "enabled": True,
                "max_items": 1,
                "max_related": 1,
                "budget_ratio": 0.12,
                "max_chars": 120,
            },
        },
        hermes_home=tmp_path,
    )
    store = manager.ensure_store()
    _insert_active_node(
        store,
        node_type=NodeType.FACT,
        summary="socksio may be required for SOCKS proxy support",
        canonical_key="fact:socksio-required",
    )
    _insert_active_node(
        store,
        node_type=NodeType.PREFERENCE,
        summary="User prefers concise replies",
        canonical_key="preference:user-prefers-concise",
    )

    block = manager.build_recall_block("socksio concise replies")
    assert block.count("- [") <= 1
    assert len(block) <= 120


def test_manager_recall_marks_recalled_nodes(tmp_path):
    from agent.sparkgraph.manager import SparkGraphManager

    manager = SparkGraphManager.from_raw_config(
        {
            "mode": "flush_integrated",
            "recall": {
                "enabled": True,
                "max_items": 4,
                "max_related": 4,
                "budget_ratio": 0.12,
                "max_chars": 1800,
            },
        },
        hermes_home=tmp_path,
    )
    store = manager.ensure_store()
    node_id = _insert_active_node(
        store,
        node_type=NodeType.FACT,
        summary="socksio may be required for SOCKS proxy support",
        canonical_key="fact:socksio-required",
    )

    block = manager.build_recall_block("socksio proxy support")
    node = store.get_node(node_id)

    assert "[SparkGraph Recall]" in block
    assert int(node["last_recalled_at"]) > 0
    assert '"recall_hits": 1' in node["meta"]


def test_manager_recall_does_not_mark_nodes_when_block_is_empty(tmp_path):
    from agent.sparkgraph.manager import SparkGraphManager

    manager = SparkGraphManager.from_raw_config(
        {
            "mode": "flush_integrated",
            "recall": {
                "enabled": True,
                "max_items": 4,
                "max_related": 4,
                "budget_ratio": 0.12,
                "max_chars": 10,
            },
        },
        hermes_home=tmp_path,
    )
    store = manager.ensure_store()
    node_id = _insert_active_node(
        store,
        node_type=NodeType.FACT,
        summary="socksio may be required for SOCKS proxy support",
        canonical_key="fact:socksio-required",
    )

    block = manager.build_recall_block("socksio proxy support")
    node = store.get_node(node_id)

    assert block == ""
    assert int(node["last_recalled_at"]) == 0
    assert '"recall_hits":' not in node["meta"]


def test_recall_nodes_excludes_deprecated_nodes(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    active_id = _insert_active_node(
        store,
        node_type=NodeType.FACT,
        summary="socksio may be required for SOCKS proxy support",
        canonical_key="fact:socksio-required",
    )
    deprecated_id = store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="Old socks proxy workaround",
            canonical_key="fact:old-socks-workaround",
            source_kind="flush",
            status=NodeStatus.DEPRECATED,
            confidence=0.95,
            stability=0.95,
            reuse_score=0.95,
        )
    )

    nodes, _edges = recall_nodes(store, query="socks proxy", config=RecallConfig(max_nodes=8))
    node_ids = {node["id"] for node in nodes}
    assert active_id in node_ids
    assert deprecated_id not in node_ids


def test_recall_nodes_excludes_low_stability_active_nodes(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    strong_id = _insert_active_node(
        store,
        node_type=NodeType.FACT,
        summary="socksio may be required for SOCKS proxy support",
        canonical_key="fact:socksio-required",
        confidence=0.8,
        stability=0.8,
    )
    weak_id = _insert_active_node(
        store,
        node_type=NodeType.FACT,
        summary="speculative socks workaround",
        canonical_key="fact:speculative-socks-workaround",
        confidence=0.95,
        stability=0.3,
    )

    nodes, _edges = recall_nodes(store, query="socks proxy", config=RecallConfig(max_nodes=8))
    node_ids = {node["id"] for node in nodes}
    assert strong_id in node_ids
    assert weak_id not in node_ids


def test_recall_nodes_vector_search_can_hit_older_relevant_nodes(tmp_path, monkeypatch):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    target_id = _insert_active_node(
        store,
        node_type=NodeType.ISSUE,
        summary="Elasticsearch 查询变慢时，先检查 keyword 和 text 字段映射是否错误",
        canonical_key="issue:es-field-mapping",
    )
    store.upsert_vector(
        node_id=target_id,
        content_hash="target",
        embedding=[1.0, 0.0, 0.0],
    )
    store.conn.execute(
        "UPDATE sg_nodes SET updated_at = ? WHERE id = ?",
        (1, target_id),
    )
    store.conn.commit()

    for idx in range(30):
        node_id = _insert_active_node(
            store,
            node_type=NodeType.FACT,
            summary=f"noise node {idx}",
            canonical_key=f"fact:noise-{idx}",
        )
        store.upsert_vector(
            node_id=node_id,
            content_hash=f"noise-{idx}",
            embedding=[0.0, 1.0, 0.0],
        )

    monkeypatch.setattr("agent.sparkgraph.recaller.create_embedding", lambda *a, **kw: [1.0, 0.0, 0.0])

    nodes, _edges = recall_nodes(
        store,
        query="field mapping problem",
        config=RecallConfig(search_limit=4, related_limit=0, max_nodes=4, vector_limit=4),
        embedding_config=SparkGraphEmbeddingConfig(
            provider="openai-compatible",
            model="fake-embed",
            base_url="http://localhost:8000",
            timeout=10,
        ),
    )
    node_ids = {node["id"] for node in nodes}
    assert target_id in node_ids


def test_manager_empty_recall_block_is_safe_when_no_nodes_match(tmp_path):
    from agent.sparkgraph.manager import SparkGraphManager

    manager = SparkGraphManager.from_raw_config(
        {
            "mode": "flush_integrated",
            "recall": {
                "enabled": True,
                "max_items": 4,
                "max_related": 4,
                "budget_ratio": 0.12,
                "max_chars": 1800,
            },
        },
        hermes_home=tmp_path,
    )
    store = manager.ensure_store()
    store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary="candidate-only placeholder knowledge",
            canonical_key="fact:candidate-placeholder",
            source_kind="flush",
            status=NodeStatus.CANDIDATE,
            confidence=0.9,
            stability=0.9,
            reuse_score=0.9,
        )
    )

    assert manager.build_recall_block("unrelated query") == ""


def test_recall_nodes_can_use_embedding_when_fts_misses(tmp_path, monkeypatch):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    redis_id = _insert_active_node(
        store,
        node_type=NodeType.ISSUE,
        summary="Redis remote access troubleshooting checks bind protected-mode and port mapping",
        canonical_key="issue:redis-remote-access",
    )
    store.upsert_vector(
        node_id=redis_id,
        content_hash="redis-hash",
        embedding=[0.9, 0.1, 0.0],
    )

    def _fake_embed(text, config):
        assert config.model == "test-embedding"
        return [0.9, 0.1, 0.0]

    monkeypatch.setattr("agent.sparkgraph.recaller.create_embedding", _fake_embed)

    nodes, _edges = recall_nodes(
        store,
        query="service is up but clients cannot connect remotely",
        config=RecallConfig(max_nodes=4, search_limit=4),
        embedding_config=SparkGraphEmbeddingConfig(
            provider="openai-compatible",
            model="test-embedding",
            base_url="http://localhost:8000/v1",
            api_key="test-key",
            timeout=5,
        ),
    )

    assert [node["id"] for node in nodes] == [redis_id]


def test_recall_nodes_skip_low_signal_greeting_queries(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    _insert_active_node(
        store,
        node_type=NodeType.FACT,
        summary="User greeted with hallo during onboarding",
        canonical_key="fact:user-hallo-onboarding",
    )

    nodes, _edges = recall_nodes(store, query="hallo", config=RecallConfig(max_nodes=4))
    assert nodes == []


def test_recall_nodes_rank_vector_hits_by_similarity_before_recency(tmp_path, monkeypatch):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    strong_id = _insert_active_node(
        store,
        node_type=NodeType.ISSUE,
        summary="Redis remote access checks bind protected mode and port mapping",
        canonical_key="issue:redis-remote-access",
    )
    weak_id = _insert_active_node(
        store,
        node_type=NodeType.ISSUE,
        summary="Generic recent note that should not outrank the better semantic hit",
        canonical_key="issue:generic-recent-note",
        confidence=0.95,
        stability=0.95,
        reuse_score=0.95,
    )
    store.upsert_vector(node_id=strong_id, content_hash="strong", embedding=[0.95, 0.05, 0.0])
    store.upsert_vector(node_id=weak_id, content_hash="weak", embedding=[0.58, 0.42, 0.0])

    store.conn.execute("UPDATE sg_nodes SET updated_at = ? WHERE id = ?", (1, strong_id))
    store.conn.execute("UPDATE sg_nodes SET updated_at = ? WHERE id = ?", (999999999, weak_id))
    store.conn.commit()

    monkeypatch.setattr(
        "agent.sparkgraph.recaller.create_embedding",
        lambda *args, **kwargs: [1.0, 0.0, 0.0],
    )

    nodes, _edges = recall_nodes(
        store,
        query="redis remote clients cannot connect",
        config=RecallConfig(max_nodes=2, search_limit=2, vector_limit=2, related_limit=0),
        embedding_config=SparkGraphEmbeddingConfig(
            provider="openai-compatible",
            model="test-embedding",
            base_url="http://localhost:8000/v1",
            api_key="test-key",
            timeout=5,
        ),
    )

    assert [node["id"] for node in nodes[:2]] == [strong_id, weak_id]


def test_flush_maintenance_backfills_missing_vectors(tmp_path, monkeypatch):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    redis_id = _insert_active_node(
        store,
        node_type=NodeType.ISSUE,
        summary="Redis remote access troubleshooting checks bind protected-mode and port mapping",
        canonical_key="issue:redis-remote-access",
    )
    assert store.get_vector(redis_id) is None

    monkeypatch.setattr(
        "agent.sparkgraph.maintenance.create_embedding",
        lambda text, config: [0.9, 0.1, 0.0],
    )

    result = run_flush_maintenance(
        store,
        embedding_config=SparkGraphEmbeddingConfig(
            provider="openai-compatible",
            model="test-embedding",
            base_url="http://localhost:8000/v1",
            api_key="test-key",
            timeout=5,
        ),
    )

    assert result["vectors_backfilled"] == 1
    assert store.get_vector(redis_id) is not None
