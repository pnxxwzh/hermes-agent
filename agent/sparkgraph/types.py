"""Core SparkGraph type definitions."""

from __future__ import annotations

from enum import Enum


class NodeType(str, Enum):
    FACT = "FACT"
    PREFERENCE = "PREFERENCE"
    ISSUE = "ISSUE"
    RESOURCE = "RESOURCE"
    DECISION = "DECISION"


class EdgeType(str, Enum):
    RELATED_TO = "RELATED_TO"
    SOLVES = "SOLVES"
    DEPENDS_ON = "DEPENDS_ON"
    CONFLICTS_WITH = "CONFLICTS_WITH"
    DERIVED_FROM = "DERIVED_FROM"
    APPLIES_TO = "APPLIES_TO"


class NodeStatus(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class RecallChannel(str, Enum):
    """三条召回通道，对应 recall_eligibility 的三个层次。

    L1 DIRECT — 精准匹配，含 candidate（需 evidence>=1）
    L2 GRAPH  — 图扩展，active-only
    L3 COLD   — 零 evidence candidate 兜底
    """

    DIRECT = "direct"
    GRAPH = "graph"
    COLD = "cold"
