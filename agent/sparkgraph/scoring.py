"""SparkGraph scoring and status-transition helpers."""

from __future__ import annotations

from dataclasses import dataclass

from agent.sparkgraph.types import NodeType
from agent.sparkgraph.types import NodeStatus

SOURCE_SCORES = {
    "manual": 1.00,
    "review": 0.92,
    "explicit": 0.88,
    "flush": 0.72,
    "auto": 0.72,
    "reflection": 0.58,
    "shadow": 0.50,
}

SESSION_BOUND_PENALTY = 0.35
ACTIVE_CONFIDENCE_THRESHOLD = 0.70
ACTIVE_STABILITY_THRESHOLD = 0.65
DEPRECATE_STABILITY_THRESHOLD = 0.45
DEPRECATE_SUPPORT_THRESHOLD = 0.35
STALE_RECALL_DAYS = 30
TYPE_PRIORS = {
    NodeType.FACT: {"stability": 0.70, "reuse_score": 0.68},
    NodeType.PREFERENCE: {"stability": 0.80, "reuse_score": 0.78},
    NodeType.ISSUE: {"stability": 0.62, "reuse_score": 0.66},
    NodeType.RESOURCE: {"stability": 0.58, "reuse_score": 0.60},
    NodeType.DECISION: {"stability": 0.66, "reuse_score": 0.70},
}


@dataclass(frozen=True)
class ScoreBreakdown:
    confidence: float
    stability: float
    reuse_score: float
    support_score: float
    source_score: float
    session_bound_penalty: float
    confidence_components: dict


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, round(value, 4)))


def source_score(source_kind: str) -> float:
    return SOURCE_SCORES.get(source_kind, SOURCE_SCORES["auto"])


def default_type_priors(node_type: NodeType) -> dict[str, float]:
    return dict(TYPE_PRIORS[node_type])


def support_score(*, evidence_count: int, active_edge_count: int = 0, recall_hits: int = 0) -> float:
    evidence_component = min(max(evidence_count, 0) / 3.0, 1.0) * 0.6
    edge_component = min(max(active_edge_count, 0) / 2.0, 1.0) * 0.25
    recall_component = min(max(recall_hits, 0) / 3.0, 1.0) * 0.15
    return _clamp(evidence_component + edge_component + recall_component)


def compute_scores(
    *,
    source_kind: str,
    evidence_count: int,
    stability: float,
    reuse_score: float,
    active_edge_count: int = 0,
    recall_hits: int = 0,
    session_bound: bool = False,
    durability_score: float | None = None,
    dedup_consistency_bonus: float = 0.0,
) -> ScoreBreakdown:
    src = source_score(source_kind)
    support = support_score(
        evidence_count=evidence_count,
        active_edge_count=active_edge_count,
        recall_hits=recall_hits,
    )
    stability_value = _clamp(stability)
    reuse_value = _clamp(reuse_score)
    durability_value = stability_value if durability_score is None else _clamp(durability_score)
    penalty = SESSION_BOUND_PENALTY if session_bound else 0.0

    confidence = _clamp(
        (src * 0.30)
        + (durability_value * 0.20)
        + (reuse_value * 0.15)
        + (stability_value * 0.15)
        + (support * 0.15)
        + max(0.0, dedup_consistency_bonus)
        - penalty
    )

    return ScoreBreakdown(
        confidence=confidence,
        stability=stability_value,
        reuse_score=reuse_value,
        support_score=support,
        source_score=src,
        session_bound_penalty=penalty,
        confidence_components={
            "source_score": src,
            "durability_score": durability_value,
            "reuse_score": reuse_value,
            "stability": stability_value,
            "support_score": support,
            "dedup_consistency_bonus": max(0.0, dedup_consistency_bonus),
            "session_bound_penalty": penalty,
        },
    )


def should_promote_candidate(
    *,
    source_kind: str,
    confidence: float,
    stability: float,
    evidence_count: int,
    session_bound: bool = False,
    relation_supported: bool = False,
    dedup_blocked: bool = False,
) -> bool:
    if session_bound or dedup_blocked:
        return False
    if source_kind in {"manual", "review", "explicit"}:
        return True
    if confidence >= ACTIVE_CONFIDENCE_THRESHOLD and stability >= ACTIVE_STABILITY_THRESHOLD and evidence_count >= 2:
        return True
    if relation_supported and confidence >= 0.72 and stability >= ACTIVE_STABILITY_THRESHOLD:
        return True
    return False


def should_deprecate_active(
    *,
    superseded: bool = False,
    merged: bool = False,
    days_since_recall_hit: int = 0,
    support_score_value: float = 1.0,
    stability: float = 1.0,
) -> bool:
    if superseded or merged:
        return True
    return (
        days_since_recall_hit >= STALE_RECALL_DAYS
        and support_score_value < DEPRECATE_SUPPORT_THRESHOLD
        and stability < DEPRECATE_STABILITY_THRESHOLD
    )


def next_status_for_candidate(
    *,
    source_kind: str,
    confidence: float,
    stability: float,
    evidence_count: int,
    session_bound: bool = False,
    relation_supported: bool = False,
    dedup_blocked: bool = False,
) -> NodeStatus:
    if should_promote_candidate(
        source_kind=source_kind,
        confidence=confidence,
        stability=stability,
        evidence_count=evidence_count,
        session_bound=session_bound,
        relation_supported=relation_supported,
        dedup_blocked=dedup_blocked,
    ):
        return NodeStatus.ACTIVE
    return NodeStatus.CANDIDATE
