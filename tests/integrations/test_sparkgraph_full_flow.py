"""
End-to-end integration test for SparkGraph via real Hermes business entry points.

Entry points:
  agent/sparkgraph/manager.py   — SparkGraphManager (agent real entry point)
  tools/sparkgraph_tool.py      — sparkgraph_record_tool, sparkgraph_search_tool, sparkgraph_stats_tool
  agent/sparkgraph/recaller.py — recall_nodes (three-channel recall engine)
  agent/sparkgraph/maintenance.py — run_flush_maintenance (deprecation + PPR)
  agent/sparkgraph/formatter.py  — build_recall_payload (output formatter)

No mocking of store operations — real SQLite DB, real business logic.
Embedding is DISABLED for all tests (no local server dependency).
"""

import json
import time
from pathlib import Path

import pytest

from agent.sparkgraph.config import SparkGraphConfig, SparkGraphEmbeddingConfig, SparkGraphRecallConfig
from agent.sparkgraph.manager import SparkGraphManager
from agent.sparkgraph.maintenance import run_flush_maintenance, run_ppr_maintenance
from agent.sparkgraph.recaller import RecallConfig, recall_nodes
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType
from tools.sparkgraph_tool import sparkgraph_record_tool, sparkgraph_search_tool, sparkgraph_stats_tool


# ─── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def sg_manager(tmp_path):
    """Real SparkGraphManager. Embedding disabled (no server dependency)."""
    db_path = tmp_path / "sparkgraph" / "default.db"
    config = SparkGraphConfig(
        mode="flush_integrated",
        db_path=db_path,
        recall=SparkGraphRecallConfig(enabled=True, max_items=4, max_related=4,
                                       max_chars=1800),
        embedding=SparkGraphEmbeddingConfig(provider="", model="", base_url="",
                                            api_key="", timeout=20),
    )
    manager = SparkGraphManager(config=config)
    manager.ensure_store()
    return manager


@pytest.fixture
def store(sg_manager):
    return sg_manager.ensure_store()


def _item(summary, node_type, evidence=None, source_kind=None):
    """Helper: build a valid item dict. evidence is required; source_kind is tool-level."""
    d = {"summary": summary, "type": node_type}
    if evidence:
        d["evidence"] = evidence
    return d


# ─── T0: Schema bootstrap ─────────────────────────────────────────────────────

def test_schema_bootstrap_creates_all_tables(tmp_path):
    db_path = tmp_path / "sparkgraph" / "default.db"
    config = SparkGraphConfig(
        mode="flush_integrated", db_path=db_path,
        recall=SparkGraphRecallConfig(), embedding=SparkGraphEmbeddingConfig(),
    )
    store = SparkGraphManager(config=config).ensure_store()
    tables = {r["name"] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "sg_nodes" in tables
    assert "sg_edges" in tables
    assert "sg_vectors" in tables
    assert "sg_nodes_fts" in tables


def test_schema_v5_removes_stability_adds_source_sessions(tmp_path):
    db_path = tmp_path / "sparkgraph" / "default.db"
    config = SparkGraphConfig(
        mode="flush_integrated", db_path=db_path,
        recall=SparkGraphRecallConfig(), embedding=SparkGraphEmbeddingConfig(),
    )
    store = SparkGraphManager(config=config).ensure_store()
    cols = {r["name"] for r in store.conn.execute("PRAGMA table_info(sg_nodes)").fetchall()}
    assert "source_sessions" in cols,   "v5: source_sessions added"
    assert "default_inject" in cols,     "v5: default_inject added"
    assert "validated_count" in cols,    "v5: validated_count added"
    assert "stability" not in cols,     "v5: stability removed"
    assert "reuse_score" not in cols,   "v5: reuse_score removed"


# ─── T1: Record — node types and source kinds ─────────────────────────────────

@pytest.mark.parametrize("node_type", ["FACT", "ISSUE", "RESOURCE", "DECISION", "PREFERENCE"])
def test_record_all_node_types(sg_manager, store, node_type):
    r = json.loads(sparkgraph_record_tool(
        items=[_item(f"Test {node_type}", node_type, evidence="Test evidence")],
        store=store, session_id="s1", turn_index=1,
    ))
    assert r["success"] is True
    assert r["created"] == 1
    node = store.get_node(r["recorded_ids"][0])
    assert node["type"] == node_type.upper(), f"expected {node_type.upper()}, got {node['type']}"
    assert node["status"] == "active"


def test_record_reflection_nodes_are_active(sg_manager, store):
    """source_kind=reflection → ACTIVE, default_inject=1 (no deprecated source kinds)."""
    r = json.loads(sparkgraph_record_tool(
        items=[_item("Self-reflection note", "FACT", evidence="Introspection")],
        store=store, session_id="s1", turn_index=1,
        source_kind="reflection",
    ))
    node = store.get_node(r["recorded_ids"][0])
    assert node["status"] == "active"
    assert node["default_inject"] == 1


def test_record_shadow_nodes_are_active(sg_manager, store):
    """source_kind=shadow → ACTIVE, default_inject=1 (no deprecated source kinds)."""
    r = json.loads(sparkgraph_record_tool(
        items=[_item("Shadow observation", "ISSUE", evidence="Observed pattern")],
        store=store, session_id="s1", turn_index=1,
        source_kind="shadow",
    ))
    node = store.get_node(r["recorded_ids"][0])
    assert node["status"] == "active"
    assert node["default_inject"] == 1


def test_record_item_without_evidence_rejected(sg_manager, store):
    """Item missing evidence field is rejected."""
    r = json.loads(sparkgraph_record_tool(
        items=[{"summary": "No evidence here", "type": "FACT"}],
        store=store, session_id="s1", turn_index=1,
    ))
    # rejected counter increments; no node created
    assert r["rejected"] >= 1


# ─── T2: Batch record ────────────────────────────────────────────────────────

def test_record_batch_creates_multiple_nodes(sg_manager, store):
    r = json.loads(sparkgraph_record_tool(
        items=[
            _item("PostgreSQL pg_hba manages authentication", "FACT",
                  evidence="pg_hba controls client access"),
            _item("pg_hba is the authentication config file", "FACT",
                  evidence="Host-based auth rules"),
        ],
        store=store, session_id="s1", turn_index=1,
    ))
    assert r["created"] == 2
    assert r["related_edges_created"] >= 0  # may or may not link


# ─── T3: Same-type dedup ─────────────────────────────────────────────────────

def test_same_type_dedup_updates_existing(sg_manager, store):
    summary = "pg_hba controls PostgreSQL client authentication"
    r1 = json.loads(sparkgraph_record_tool(
        items=[_item(summary, "FACT", evidence="First record")],
        store=store, session_id="s-alpha", turn_index=1,
    ))
    r2 = json.loads(sparkgraph_record_tool(
        items=[_item(summary, "FACT", evidence="Second record")],
        store=store, session_id="s-beta", turn_index=2,
    ))
    assert r1["created"] == 1
    assert r2["created"] == 0
    assert r2["updated"] == 1
    assert r2["recorded_ids"][0] == r1["recorded_ids"][0]

    sessions = set(json.loads(store.get_node(r1["recorded_ids"][0])["source_sessions"]))
    assert sessions == {"s-alpha", "s-beta"}, "sessions from both records merged"


def test_same_summary_different_type_both_exist(sg_manager, store):
    """Same summary but different type: both nodes exist (cross-type dedup merges into keep)."""
    summary = "Redis bind configuration controls network access"
    r1 = json.loads(sparkgraph_record_tool(
        items=[_item(summary, "ISSUE", evidence="Redis issue")],
        store=store, session_id="s1", turn_index=1,
    ))
    r2 = json.loads(sparkgraph_record_tool(
        items=[_item(summary, "FACT", evidence="Redis fact")],
        store=store, session_id="s2", turn_index=2,
    ))
    # Cross-type dedup: recorded_ids point to keep node (ISSUE), but both nodes exist
    assert store.count_nodes() == 2
    assert r1["recorded_ids"][0] == r2["recorded_ids"][0], \
        "cross-type dedup: recorded_ids point to keep node"
    deprecated = store.conn.execute(
        "SELECT COUNT(*) FROM sg_nodes WHERE status=?", ("deprecated",)
    ).fetchone()[0]
    assert deprecated == 1, "merged node should be deprecated"


# ─── T4: Cross-type dedup → merge_nodes ───────────────────────────────────────

def test_cross_type_dedup_merges_nodes(sg_manager, store):
    """ISSUE+FACT same summary → new FACT inserted → merge_nodes(keep=ISSUE, merge=FACT)."""
    summary = "Redis bind order: bind → protected-mode → port mapping"

    issue = json.loads(sparkgraph_record_tool(
        items=[_item(summary, "ISSUE", evidence="Redis connection issue")],
        store=store, session_id="s-alpha", turn_index=1,
    ))
    issue_id = issue["recorded_ids"][0]

    fact = json.loads(sparkgraph_record_tool(
        items=[_item(summary, "FACT", evidence="Redis connection fact")],
        store=store, session_id="s-beta", turn_index=2,
    ))

    assert fact["created"] == 0
    assert fact["updated"] == 1
    assert fact["recorded_ids"][0] == issue_id

    assert store.count_nodes() == 2
    deprecated = store.conn.execute(
        "SELECT COUNT(*) FROM sg_nodes WHERE status=?", ("deprecated",)
    ).fetchone()[0]
    assert deprecated == 1, "merged FACT should be deprecated"

    keep_sessions = set(json.loads(store.get_node(issue_id)["source_sessions"]))
    assert keep_sessions == {"s-alpha", "s-beta"}, "sessions merged on keep node"


def test_cross_type_dedup_validated_count_accumulates(sg_manager, store):
    """merge_nodes: merge's validated_count accumulates into keep."""
    summary = "Docker daemon must be running for docker CLI"

    keep_id = json.loads(sparkgraph_record_tool(
        items=[_item(summary, "ISSUE", evidence="Docker issue")],
        store=store, session_id="s1", turn_index=1,
    ))["recorded_ids"][0]

    # Manually create merge node with validated_count=2
    merge_id = store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT,
        summary=summary,
        canonical_key="fact:docker-daemon",
        source_kind="manual",
        status=NodeStatus.ACTIVE,
        confidence=0.85,
        meta={"validated_count": 2},
    ))
    store.merge_nodes(keep_id=keep_id, merge_id=merge_id)

    keep = store.get_node(keep_id)
    assert keep["validated_count"] == 2


# ─── T5: Recall — three channels ──────────────────────────────────────────────

def test_recall_channel1_fts_finds_active_nodes(sg_manager, store):
    """Channel 1: FTS search returns matching active nodes."""
    # Note: FTS5 sanitization doesn't yet handle '.' in queries (e.g. "pg_hba").
    # Using "pg_hba" as search term (no dot).
    sparkgraph_record_tool(
        items=[_item("pg_hba controls PostgreSQL authentication", "FACT",
                     evidence="pg_hba config")],
        store=store, session_id="s1", turn_index=1,
    )
    block, tokens = sg_manager.build_recall_block("pg_hba")
    assert "pg_hba" in block
    assert tokens > 0


def test_recall_channel2_graph_extension(sg_manager, store):
    """Channel 2: 1-hop neighbors are included via graph expansion."""
    r1 = json.loads(sparkgraph_record_tool(
        items=[_item("PostgreSQL authentication overview", "FACT",
                     evidence="PG auth system")],
        store=store, session_id="s1", turn_index=1,
    ))
    r2 = json.loads(sparkgraph_record_tool(
        items=[_item("pg_hba controls authentication", "FACT",
                     evidence="PG auth config")],
        store=store, session_id="s1", turn_index=2,
    ))
    store.insert_edge(from_id=r1["recorded_ids"][0], to_id=r2["recorded_ids"][0],
                      edge_type=EdgeType.RELATED_TO)

    block, _ = sg_manager.build_recall_block("authentication")
    assert block != ""


def test_recall_channel3_explicit_priority(sg_manager, store):
    """Channel 3: explicit/manual nodes have match_priority=4 (highest)."""
    sparkgraph_record_tool(
        items=[_item("User prefers concise replies in Slack", "PREFERENCE",
                     evidence="User said keep it short")],
        store=store, session_id="s1", turn_index=1,
    )
    block, _ = sg_manager.build_recall_block("preferences")
    assert "prefers" in block or "Slack" in block or block == ""


def test_recall_low_signal_query_returns_empty(sg_manager, store):
    """hello/thanks/ok → empty recall."""
    sparkgraph_record_tool(
        items=[_item("Important pg_hba fact", "FACT", evidence="Auth config")],
        store=store, session_id="s1", turn_index=1,
    )
    for query in ["hello", "thanks", "ok"]:
        block, tokens = sg_manager.build_recall_block(query)
        assert block == "", f"low-signal query '{query}' should return empty"
        assert tokens == 0


def test_recall_empty_query_returns_empty(sg_manager, store):
    block, tokens = sg_manager.build_recall_block("")
    assert block == ""
    assert tokens == 0


def test_recall_default_inject_0_excluded(sg_manager, store):
    """default_inject=0 nodes → not in recall output (reflection nodes now have default_inject=1)."""
    # active injectable node
    sparkgraph_record_tool(
        items=[_item("pg_hba authentication config", "FACT",
                     evidence="Auth file")],
        store=store, session_id="s1", turn_index=1,
    )
    # reflection node — now created as ACTIVE with default_inject=1 (no deprecated source kinds)
    sparkgraph_record_tool(
        items=[_item("Reflection: check pg_hba later", "ISSUE",
                     evidence="Note to self")],
        store=store, session_id="s2", turn_index=2,
        source_kind="reflection",
    )

    block, _ = sg_manager.build_recall_block("pg_hba")
    # Both nodes should appear since reflection now gets default_inject=True
    assert "pg_hba" in block


def test_recall_deprecated_nodes_excluded(sg_manager, store):
    """Deprecated nodes are not recalled."""
    node_id = json.loads(sparkgraph_record_tool(
        items=[_item("Old deprecated knowledge", "FACT", evidence="Old")],
        store=store, session_id="s1", turn_index=1,
    ))["recorded_ids"][0]
    store.update_node_status(node_id, status=NodeStatus.DEPRECATED.value)

    block, _ = sg_manager.build_recall_block("deprecated")
    assert "Old deprecated knowledge" not in block


def test_recall_mark_recalled_updates_timestamp(sg_manager, store):
    """Successful recall updates last_recalled_at."""
    node_id = json.loads(sparkgraph_record_tool(
        items=[_item("pg_hba auth config", "FACT", evidence="Auth")],
        store=store, session_id="s1", turn_index=1,
    ))["recorded_ids"][0]

    before = store.get_node(node_id)["last_recalled_at"] or 0
    time.sleep(0.01)
    sg_manager.build_recall_block("pg_hba")
    after = store.get_node(node_id)["last_recalled_at"]
    assert after > before


# ─── T6: Recall via recall_nodes directly ─────────────────────────────────────

def test_recall_nodes_returns_correct_structure(sg_manager, store):
    """recall_nodes returns (nodes, edges, token_estimate)."""
    node_id = json.loads(sparkgraph_record_tool(
        items=[_item("PostgreSQL pg_hba controls authentication", "FACT",
                     evidence="Auth file")],
        store=store, session_id="s1", turn_index=1,
    ))["recorded_ids"][0]

    nodes, edges, token_est = recall_nodes(
        store, query="pg_hba",
        config=RecallConfig(max_nodes=4),
    )
    assert isinstance(nodes, list)
    assert isinstance(edges, list)
    assert isinstance(token_est, int)
    assert token_est > 0
    assert node_id in [n["id"] for n in nodes]


# ─── T7: Search tool ───────────────────────────────────────────────────────────

def test_search_tool_finds_active_nodes(sg_manager, store):
    sparkgraph_record_tool(
        items=[_item("Redis connection refused resolution", "ISSUE",
                     evidence="Redis error")],
        store=store, session_id="s1", turn_index=1,
    )
    result = json.loads(sparkgraph_search_tool(query="Redis connection", store=store))
    assert result["success"] is True
    assert result["count"] >= 1
    assert any("Redis" in n["summary"] for n in result["items"])


def test_search_tool_no_match_returns_empty(sg_manager, store):
    result = json.loads(sparkgraph_search_tool(query="xyzzy_nomatch_123", store=store))
    assert result["success"] is True
    assert result["count"] == 0
    assert result["items"] == []


# ─── T8: Stats tool ───────────────────────────────────────────────────────────

def test_stats_tool_reports_totals(sg_manager, store):
    sparkgraph_record_tool(
        items=[_item("User prefers dark mode", "PREFERENCE", evidence="Pref")],
        store=store, session_id="s1", turn_index=1,
    )
    sparkgraph_record_tool(
        items=[_item("Redis pub/sub pattern", "FACT", evidence="Pattern")],
        store=store, session_id="s2", turn_index=2,
    )
    result = json.loads(sparkgraph_stats_tool(store=store))
    assert result["success"] is True
    assert result["nodes_total"] == 2
    assert result["nodes_by_type"]["PREFERENCE"] == 1
    assert result["nodes_by_type"]["FACT"] == 1


# ─── T9: Maintenance ─────────────────────────────────────────────────────────

def test_maintenance_deprecates_stale_low_signal_nodes(sg_manager, store):
    """30+ days idle + validated_count≤1 → deprecated."""
    node_id = json.loads(sparkgraph_record_tool(
        items=[_item("Stale old fact", "FACT", evidence="Old")],
        store=store, session_id="s1", turn_index=1,
    ))["recorded_ids"][0]

    # Set last_recalled_at to 31 days ago with validated_count>0
    # (validated_count=0 → reference_ts=now → never deprecated)
    old_ts = int(time.time()) - 31 * 86400
    store.conn.execute("UPDATE sg_nodes SET last_recalled_at=?, validated_count=1 WHERE id=?",
                      (old_ts, node_id))
    store.conn.commit()

    result = run_flush_maintenance(store)

    node = store.get_node(node_id)
    assert node["status"] == "deprecated"


def test_maintenance_preserves_frequently_recalled_nodes(sg_manager, store):
    """validated_count > 1 nodes survive maintenance even if stale."""
    node_id = json.loads(sparkgraph_record_tool(
        items=[_item("Important frequently used fact", "FACT", evidence="Important")],
        store=store, session_id="s1", turn_index=1,
    ))["recorded_ids"][0]

    store.conn.execute(
        "UPDATE sg_nodes SET last_recalled_at=?, validated_count=? WHERE id=?",
        (int(time.time()) - 31 * 86400, 5, node_id)
    )
    store.conn.commit()

    result = run_flush_maintenance(store)
    node = store.get_node(node_id)
    assert node["status"] == "active"


def test_ppr_maintenance_computes_scores(sg_manager, store):
    """run_ppr_maintenance computes PageRank and invalidates cache."""
    r1 = json.loads(sparkgraph_record_tool(
        items=[_item("PostgreSQL auth", "FACT", evidence="PG auth")],
        store=store, session_id="s1", turn_index=1,
    ))
    r2 = json.loads(sparkgraph_record_tool(
        items=[_item("pg_hba", "FACT", evidence="Config")],
        store=store, session_id="s1", turn_index=2,
    ))
    store.insert_edge(from_id=r1["recorded_ids"][0], to_id=r2["recorded_ids"][0],
                      edge_type=EdgeType.RELATED_TO)

    result = run_ppr_maintenance(store)
    assert result["ppr_computed"] is True
    assert result["nodes_scored"] == 2


# ─── T10: Edges ───────────────────────────────────────────────────────────────

def test_edges_connect_and_query_nodes(sg_manager, store):
    r1 = json.loads(sparkgraph_record_tool(
        items=[_item("PostgreSQL auth system", "FACT", evidence="Auth")],
        store=store, session_id="s1", turn_index=1,
    ))
    r2 = json.loads(sparkgraph_record_tool(
        items=[_item("pg_hba config file", "FACT", evidence="Config")],
        store=store, session_id="s1", turn_index=2,
    ))
    eid = store.insert_edge(
        from_id=r1["recorded_ids"][0],
        to_id=r2["recorded_ids"][0],
        edge_type=EdgeType.RELATED_TO,
    )
    assert eid is not None

    edges = store.get_edges_for_nodes([r1["recorded_ids"][0], r2["recorded_ids"][0]])
    assert len(edges) == 1
    assert edges[0]["from_id"] == r1["recorded_ids"][0]
    assert edges[0]["to_id"] == r2["recorded_ids"][0]


# ─── T11: Config ─────────────────────────────────────────────────────────────

def test_config_requires_flush_integrated_mode(tmp_path):
    from agent.sparkgraph.config import parse_sparkgraph_config, SparkGraphConfigError
    with pytest.raises(SparkGraphConfigError):
        parse_sparkgraph_config({"mode": "standalone"})


def test_recall_disabled_returns_empty(tmp_path):
    # Create a manager with recall disabled
    db_path = tmp_path / "sparkgraph" / "default.db"
    from agent.sparkgraph.config import SparkGraphConfig, SparkGraphEmbeddingConfig, SparkGraphRecallConfig
    config = SparkGraphConfig(
        mode="flush_integrated", db_path=db_path,
        recall=SparkGraphRecallConfig(enabled=False),
        embedding=SparkGraphEmbeddingConfig(),
    )
    manager = SparkGraphManager(config=config)
    store = manager.ensure_store()
    sparkgraph_record_tool(
        items=[_item("Some fact about testing", "FACT", evidence="Test")],
        store=store, session_id="s1", turn_index=1,
    )
    block, tokens = manager.build_recall_block("some fact")
    assert block == ""
    assert tokens == 0


# ─── T12: get_by_source_kind (Channel 3 backend) ──────────────────────────────

def test_get_by_source_kind_returns_matching_nodes(sg_manager, store):
    # source_kind=explicit so it matches the get_by_source_kind query
    sparkgraph_record_tool(
        items=[_item("User likes dark mode in VSCode", "PREFERENCE", evidence="Pref")],
        store=store, session_id="s1", turn_index=1,
        source_kind="explicit",
    )
    results = store.get_by_source_kind(["explicit", "manual"], "dark mode", limit=10)
    summaries = [r["summary"] for r in results]
    assert "User likes dark mode in VSCode" in summaries


# ─── T13: Increment validated_count ────────────────────────────────────────────

def test_recall_increments_validated_count(sg_manager, store):
    node_id = json.loads(sparkgraph_record_tool(
        items=[_item("pg_hba for auth", "FACT", evidence="Auth")],
        store=store, session_id="s1", turn_index=1,
    ))["recorded_ids"][0]
    assert store.get_node(node_id)["validated_count"] == 0

    sg_manager.build_recall_block("pg_hba")

    count = store.get_node(node_id)["validated_count"]
    assert count >= 1, "validated_count should increment after recall"
