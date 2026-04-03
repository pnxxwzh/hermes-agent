import json

from agent.sparkgraph.store import SparkGraphStore
from tools.sparkgraph_tool import sparkgraph_record_tool, sparkgraph_search_tool, sparkgraph_stats_tool


def test_sparkgraph_search_returns_matching_nodes(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
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
        turn_index=1,
    )

    result = json.loads(
        sparkgraph_search_tool(query="socksio", limit=5, store=store)
    )
    assert result["success"] is True
    assert result["count"] == 1
    assert result["items"][0]["summary"] == "socksio may be required for SOCKS proxy support"
    assert result["items"][0]["status"] == "active"


def test_sparkgraph_search_no_results(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    result = json.loads(
        sparkgraph_search_tool(query="missing", status="active", limit=5, store=store)
    )
    assert result["success"] is True
    assert result["count"] == 0
    assert result["items"] == []


def test_sparkgraph_stats_reports_totals(tmp_path):
    store = SparkGraphStore(tmp_path / "sparkgraph" / "default.db")
    sparkgraph_record_tool(
        items=[
            {
                "summary": "User prefers concise replies",
                "type": "PREFERENCE",
                "evidence": "Please keep replies concise.",
            }
        ],
        store=store,
        session_id="session-1",
        turn_index=1,
    )

    result = json.loads(sparkgraph_stats_tool(store=store))
    assert result["success"] is True
    assert result["nodes_total"] == 1
    assert result["nodes_by_type"]["PREFERENCE"] == 1
