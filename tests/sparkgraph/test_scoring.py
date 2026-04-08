"""Tests for simplified scoring module (graph-memory aligned)."""

import pytest

from agent.sparkgraph.scoring import (
    SOURCE_CONFIDENCE,
    initial_score_for,
    recall_priority_score,
    should_deprecate_active,
    STALE_RECALL_DAYS,
)
from agent.sparkgraph.types import NodeStatus


class TestInitialScoreFor:
    """TC-S-01 ~ TC-S-02: source_kind → (status, confidence) 查表"""

    def test_manual_explicit_review_active(self):
        """TC-S-01a: manual → active, confidence=0.90"""
        result = initial_score_for("manual")
        assert result.initial_status == NodeStatus.ACTIVE
        assert result.confidence == 0.90

    def test_explicit_active(self):
        """TC-S-01b: explicit → active, confidence=0.88"""
        result = initial_score_for("explicit")
        assert result.initial_status == NodeStatus.ACTIVE
        assert result.confidence == 0.88

    def test_review_active(self):
        """TC-S-01c: review → active, confidence=0.92"""
        result = initial_score_for("review")
        assert result.initial_status == NodeStatus.ACTIVE
        assert result.confidence == 0.92

    def test_flush_auto_active(self):
        """TC-S-01d: flush/auto → active, confidence=0.72"""
        for kind in ("flush", "auto"):
            result = initial_score_for(kind)
            assert result.initial_status == NodeStatus.ACTIVE, f"{kind} should be ACTIVE"
            assert result.confidence == 0.72, f"{kind} confidence should be 0.72"

    def test_reflection_active(self):
        """TC-S-02: reflection → ACTIVE (no deprecated source kind), confidence=0.50"""
        result = initial_score_for("reflection")
        assert result.initial_status == NodeStatus.ACTIVE
        assert result.confidence == 0.50

    def test_shadow_active(self):
        """TC-S-02: shadow → ACTIVE (no deprecated source kind), confidence=0.50"""
        result = initial_score_for("shadow")
        assert result.initial_status == NodeStatus.ACTIVE
        assert result.confidence == 0.50

    def test_unknown_falls_back_to_auto(self):
        """Unknown source_kind falls back to 0.72 (auto behavior)."""
        result = initial_score_for("unknown_source")
        assert result.initial_status == NodeStatus.ACTIVE
        assert result.confidence == 0.72


class TestRecallPriorityScore:
    """TC-S-03 ~ TC-S-05: recall_priority_score 排序公式"""

    def test_validated_count_contributes_more_than_confidence(self):
        """TC-S-03: validated_count × 5 贡献大于 confidence × 100（无 PPR 时）。"""
        # validated_count=20 → +100, flush+0.5 confidence → +50
        high_count = recall_priority_score(ppr_score=0.0, validated_count=20, confidence=0.5, source_kind="flush", superseded=False)
        # validated_count=0, confidence=1.0 → +100, no bonus
        high_conf = recall_priority_score(ppr_score=0.0, validated_count=0, confidence=1.0, source_kind="flush", superseded=False)
        assert high_count > high_conf, "validated_count=20 should contribute more than confidence=1.0 (no bonus)"

    def test_validated_count_contributes(self):
        """TC-S-03b: validated_count × 5 贡献排序分。"""
        base = recall_priority_score(ppr_score=0.05, validated_count=0, confidence=0.8, source_kind="flush", superseded=False)
        with_count = recall_priority_score(ppr_score=0.05, validated_count=10, confidence=0.8, source_kind="flush", superseded=False)
        assert with_count > base
        assert with_count - base == pytest.approx(10 * 5.0, abs=0.01)

    def test_explicit_source_bonus(self):
        """TC-S-03c: explicit 有 +80 bonus。"""
        base = recall_priority_score(ppr_score=0.05, validated_count=0, confidence=0.8, source_kind="auto", superseded=False)
        explicit = recall_priority_score(ppr_score=0.05, validated_count=0, confidence=0.8, source_kind="explicit", superseded=False)
        assert explicit - base == pytest.approx(80.0, abs=0.01)

    def test_superseded_penalty(self):
        """TC-S-04: superseded 节点排序分 -500。"""
        normal = recall_priority_score(ppr_score=0.05, validated_count=5, confidence=0.8, source_kind="flush", superseded=False)
        bad = recall_priority_score(ppr_score=0.05, validated_count=5, confidence=0.8, source_kind="flush", superseded=True)
        assert normal - bad == pytest.approx(500.0, abs=0.01)

    def test_validated_count_caps_at_20(self):
        """TC-S-05: validated_count 封顶 20次（贡献 ≤100）。"""
        score_100 = recall_priority_score(ppr_score=0.0, validated_count=20, confidence=0.0, source_kind="flush", superseded=False)
        score_1000 = recall_priority_score(ppr_score=0.0, validated_count=1000, confidence=0.0, source_kind="flush", superseded=False)
        assert score_100 == score_1000, "validated_count beyond 20 should have no additional effect"


class TestShouldDeprecateActive:
    """TC-S-06: should_deprecate_active 条件判断（仅 days_since_recall_hit + validated_count）"""

    def test_stale_low_validated_deprecated(self):
        """30天无召回 + validated_count≤1 → deprecated。"""
        assert should_deprecate_active(
            days_since_recall_hit=STALE_RECALL_DAYS,
            validated_count=0,
        ) is True

    def test_stale_but_high_validated_not_deprecated(self):
        """30天无召回但 validated_count 高 → 不 deprecated。"""
        assert should_deprecate_active(
            days_since_recall_hit=STALE_RECALL_DAYS,
            validated_count=3,
        ) is False

    def test_recent_recall_not_deprecated(self):
        """刚被召回 → 不 deprecated。"""
        assert should_deprecate_active(
            days_since_recall_hit=0,
            validated_count=0,
        ) is False
