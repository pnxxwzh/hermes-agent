from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType


def test_node_types_match_v2_contract():
    assert [item.value for item in NodeType] == [
        "FACT",
        "PREFERENCE",
        "ISSUE",
        "RESOURCE",
        "DECISION",
    ]


def test_edge_types_match_v2_contract():
    assert [item.value for item in EdgeType] == [
        "RELATED_TO",
        "DEPENDS_ON",
        "CONFLICTS_WITH",
        "DERIVED_FROM",
        "APPLIES_TO",
    ]


def test_node_status_values_match_v2_contract():
    assert [item.value for item in NodeStatus] == [
        "candidate",
        "active",
        "deprecated",
    ]
