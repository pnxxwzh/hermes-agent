"""SparkGraph scoring — simplified, aligned with graph-memory.

核心原则（来源即命运）：
  - 新节点写入时由 source_kind 直接决定 status 和 confidence
  - 无 CANDIDATE 晋升路径
  - 无 evidence 累积逻辑
  - recall 排序由 validated_count + PPR + sourceKind补偿 驱动
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.sparkgraph.types import NodeStatus

# ─── Source kind → initial confidence (direct lookup, no formula) ──
SOURCE_CONFIDENCE: dict[str, float] = {
    "manual":    0.90,
    "review":    0.92,
    "explicit":  0.88,
    "flush":     0.72,
    "auto":      0.72,
    "reflection": 0.50,
    "shadow":    0.50,
}

# 来源即 deprecated（不参与召回）
DEPRECATED_SOURCES: set[str] = {"reflection", "shadow"}

# ─── Recall ranking (graph-memory style) ───────────────────────────
# PPR 权重 1000，validated_count 权重封顶 20×5=100，confidence 权重 100
_SOURCE_BONUS: dict[str, float] = {
    "explicit":  80.0,
    "manual":    40.0,
    "review":    40.0,
    "flush":      0.0,
    "auto":       0.0,
    "reflection": 0.0,
    "shadow":     0.0,
}

# ─── Deprecation thresholds ────────────────────────────────────────
STALE_RECALL_DAYS = 30


# ─── Dataclass ────────────────────────────────────────────────────


@dataclass(frozen=True)
class InitialScore:
    """写入节点时的初始状态和 confidence。"""

    confidence: float
    initial_status: NodeStatus


# ─── Public API ───────────────────────────────────────────────────


def initial_score_for(source_kind: str) -> InitialScore:
    """来源即命运：无公式，直接查表。"""
    if source_kind in DEPRECATED_SOURCES:
        return InitialScore(
            confidence=SOURCE_CONFIDENCE.get(source_kind, 0.50),
            initial_status=NodeStatus.DEPRECATED,
        )
    return InitialScore(
        confidence=SOURCE_CONFIDENCE.get(source_kind, 0.72),
        initial_status=NodeStatus.ACTIVE,
    )


def recall_priority_score(
    *,
    ppr_score: float,
    validated_count: int,
    confidence: float,
    source_kind: str,
    superseded: bool,
) -> float:
    """graph-memory 风格召回排序分。

    score = PPR×1000 + sourceKindBonus + validatedCount×5(封顶20) + confidence×100 - superseded×500
    """
    score = ppr_score * 1000.0
    score += _SOURCE_BONUS.get(source_kind, 0.0)
    score += min(validated_count, 20) * 5.0
    score += confidence * 100.0
    if superseded:
        score -= 500.0
    return score


def should_deprecate_active(
    *,
    days_since_recall_hit: int,
    validated_count: int,
) -> bool:
    """30天无召回 + 低 validated_count → deprecated。"""
    if days_since_recall_hit >= STALE_RECALL_DAYS and validated_count <= 1:
        return True
    return False
