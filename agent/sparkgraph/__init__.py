"""SparkGraph core package.

SparkGraph is Hermes' structured knowledge supplement layer. This package
contains the standalone core primitives used by later flush integration,
maintenance, and recall bridges.
"""

from agent.sparkgraph.config import (
    DEFAULT_SPARKGRAPH_CONFIG,
    SparkGraphConfig,
    SparkGraphConfigError,
    SparkGraphEmbeddingConfig,
    SparkGraphRecallConfig,
    resolve_sparkgraph_db_path,
    sparkgraph_home,
)
from agent.sparkgraph.runtime import RuntimeHealth
from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType

__all__ = [
    "DEFAULT_SPARKGRAPH_CONFIG",
    "EdgeType",
    "NodeStatus",
    "NodeType",
    "RuntimeHealth",
    "SparkGraphConfig",
    "SparkGraphConfigError",
    "SparkGraphEmbeddingConfig",
    "SparkGraphRecallConfig",
    "resolve_sparkgraph_db_path",
    "sparkgraph_home",
]
