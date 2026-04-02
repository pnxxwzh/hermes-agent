from agent.sparkgraph.scoring import compute_scores, next_status_for_candidate, should_deprecate_active
from agent.sparkgraph.types import NodeStatus


def test_confidence_components_are_persistable_shape():
    score = compute_scores(
        source_kind="flush",
        evidence_count=1,
        stability=0.7,
        reuse_score=0.6,
    )
    assert "source_score" in score.confidence_components
    assert "support_score" in score.confidence_components
    assert 0 <= score.confidence <= 1


def test_confidence_varies_by_source_kind():
    manual = compute_scores(
        source_kind="manual",
        evidence_count=1,
        stability=0.7,
        reuse_score=0.6,
    )
    reflection = compute_scores(
        source_kind="reflection",
        evidence_count=1,
        stability=0.7,
        reuse_score=0.6,
    )
    assert manual.confidence > reflection.confidence


def test_confidence_increases_with_support():
    low = compute_scores(
        source_kind="flush",
        evidence_count=1,
        stability=0.7,
        reuse_score=0.6,
    )
    high = compute_scores(
        source_kind="flush",
        evidence_count=3,
        stability=0.7,
        reuse_score=0.6,
        active_edge_count=1,
    )
    assert high.confidence > low.confidence


def test_session_bound_penalty_reduces_confidence():
    stable = compute_scores(
        source_kind="flush",
        evidence_count=2,
        stability=0.7,
        reuse_score=0.7,
        session_bound=False,
    )
    bound = compute_scores(
        source_kind="flush",
        evidence_count=2,
        stability=0.7,
        reuse_score=0.7,
        session_bound=True,
    )
    assert stable.confidence > bound.confidence


def test_candidate_path_does_not_collapse_all_nodes():
    weak = compute_scores(
        source_kind="flush",
        evidence_count=1,
        stability=0.55,
        reuse_score=0.55,
    )
    strong = compute_scores(
        source_kind="explicit",
        evidence_count=1,
        stability=0.65,
        reuse_score=0.70,
    )
    assert next_status_for_candidate(
        source_kind="flush",
        confidence=weak.confidence,
        stability=weak.stability,
        evidence_count=1,
    ) == NodeStatus.CANDIDATE
    assert next_status_for_candidate(
        source_kind="explicit",
        confidence=strong.confidence,
        stability=strong.stability,
        evidence_count=1,
    ) == NodeStatus.ACTIVE


def test_should_deprecate_active_for_stale_low_signal_node():
    assert should_deprecate_active(
        days_since_recall_hit=45,
        support_score_value=0.2,
        stability=0.3,
    ) is True
