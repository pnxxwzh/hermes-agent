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
