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
    """两状态：active（可召回）/ deprecated（不可召回）。

    无 CANDIDATE 中间态——来源即命运，写入时直接决定状态。
    """

    ACTIVE = "active"
    DEPRECATED = "deprecated"
