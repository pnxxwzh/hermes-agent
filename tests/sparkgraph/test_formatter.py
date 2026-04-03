"""Tests for SparkGraph recall block formatter."""
import pytest
from agent.sparkgraph.formatter import build_recall_payload


def _node(node_id, summary, node_type="FACT", default_inject=1):
    return {
        "id": node_id,
        "summary": summary,
        "type": node_type,
        "default_inject": default_inject,
    }


def test_default_inject_1_included():
    nodes = [_node("n1", "remember to check pg_hba.conf", default_inject=1)]
    block, ids = build_recall_payload(nodes)
    assert "pg_hba.conf" in block
    assert "n1" in ids


def test_default_inject_0_excluded():
    """default_inject=0 nodes are suppressed from recall output."""
    nodes = [
        _node("n1", "active knowledge that should show", default_inject=1),
        _node("n2", "reflection should not appear", default_inject=0),
        _node("n3", "another active fact", default_inject=1),
    ]
    block, ids = build_recall_payload(nodes)
    assert "reflection should not appear" not in block
    assert "n2" not in ids
    assert "active knowledge that should show" in block
    assert "another active fact" in block
    # Check n1 and n3 are present
    assert len(ids) == 2
    assert "n1" in ids
    assert "n3" in ids


def test_default_inject_missing_defaults_to_1():
    """Nodes without default_inject field default to injectable."""
    nodes = [
        {"id": "n1", "summary": "no default_inject field", "type": "FACT"},
        {"id": "n2", "summary": "has it explicitly", "type": "FACT", "default_inject": 0},
    ]
    block, ids = build_recall_payload(nodes)
    assert "no default_inject field" in block
    assert "has it explicitly" not in block
    assert "n1" in ids
    assert "n2" not in ids


def test_empty_nodes_returns_empty():
    block, ids = build_recall_payload([])
    assert block == ""
    assert ids == []
