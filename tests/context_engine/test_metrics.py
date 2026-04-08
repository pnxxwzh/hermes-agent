"""Tests for context_engine.metrics."""

import pytest

from agent.context_engine.metrics import chunks_to_metrics, rough_tokens
from agent.context_engine.models import ContextChunk


class TestRoughTokens:
    """T4: rough token estimation."""

    def test_rough_tokens_empty(self):
        """T4.1: rough_tokens("") == 0."""
        assert rough_tokens("") == 0

    def test_rough_tokens_normal(self):
        """T4.2: rough_tokens("abcd") == 1 (4 chars / 4 = 1)."""
        assert rough_tokens("abcd") == 1

    def test_rough_tokens_exact(self):
        """T4.x: 8 chars -> 2 tokens."""
        assert rough_tokens("abcdefgh") == 2

    def test_rough_tokens_truncates(self):
        """T4.x: 9 chars -> 2 tokens (floor division)."""
        assert rough_tokens("abcdefghi") == 2


class TestChunksToMetrics:
    """T4: chunks_to_metrics."""

    def test_chunks_to_metrics_all_empty(self):
        """T4.3: all empty returns all zeros."""
        result = chunks_to_metrics([], [])
        assert result.stable_tokens == 0
        assert result.dynamic_tokens == 0
        assert result.total_estimated_tokens == 0
        assert result.by_source == []

    def test_chunks_to_metrics_stable_only(self):
        """T4.4: only stable, dynamic_tokens=0."""
        stable = [ContextChunk(
            source="memory",
            stage="stable",
            slot="mem",
            priority=1,
            content="x" * 8,  # 8 chars -> 2 tokens
        )]
        result = chunks_to_metrics(stable, [])
        assert result.stable_tokens == 2
        assert result.dynamic_tokens == 0
        assert result.total_estimated_tokens == 2

    def test_chunks_to_metrics_dynamic_only(self):
        """T4.5: only dynamic, stable_tokens=0."""
        dynamic = [ContextChunk(
            source="sparkgraph_recall",
            stage="dynamic",
            slot="recall",
            priority=1,
            content="y" * 12,  # 12 chars -> 3 tokens
        )]
        result = chunks_to_metrics([], dynamic)
        assert result.stable_tokens == 0
        assert result.dynamic_tokens == 3
        assert result.total_estimated_tokens == 3

    def test_chunks_to_metrics_by_source_count(self):
        """T4.6: non-empty chunk count == by_source length."""
        stable = [
            ContextChunk(source="a", stage="stable", slot="s", priority=1, content="x" * 8),
            ContextChunk(source="b", stage="stable", slot="s", priority=2, content=""),
        ]
        dynamic = [
            ContextChunk(source="c", stage="dynamic", slot="d", priority=1, content="y" * 4),
            ContextChunk(source="d", stage="dynamic", slot="d", priority=2, content=""),
        ]
        result = chunks_to_metrics(stable, dynamic)
        # Empty content chunks are filtered out
        assert len(result.by_source) == 2

    def test_chunks_to_metrics_total_sum(self):
        """T4.7: stable + dynamic = total."""
        stable = [ContextChunk(source="a", stage="stable", slot="s", priority=1, content="a" * 12)]
        dynamic = [ContextChunk(source="b", stage="dynamic", slot="d", priority=1, content="b" * 8)]
        result = chunks_to_metrics(stable, dynamic)
        assert result.stable_tokens + result.dynamic_tokens == result.total_estimated_tokens

    def test_chunks_to_metrics_both(self):
        """T4.x: both stable and dynamic present."""
        stable = [ContextChunk(source="mem", stage="stable", slot="m", priority=1, content="m" * 8)]
        dynamic = [ContextChunk(source="sg", stage="dynamic", slot="r", priority=1, content="s" * 4)]
        result = chunks_to_metrics(stable, dynamic)
        assert result.stable_tokens == 2
        assert result.dynamic_tokens == 1
        assert result.total_estimated_tokens == 3
        assert len(result.by_source) == 2

    def test_chunks_to_metrics_stages_correct(self):
        """T4.x: by_source entries have correct stage values."""
        stable = [ContextChunk(source="a", stage="stable", slot="s", priority=1, content="a" * 4)]
        dynamic = [ContextChunk(source="b", stage="dynamic", slot="d", priority=1, content="b" * 4)]
        result = chunks_to_metrics(stable, dynamic)
        stages = {sm.stage for sm in result.by_source}
        assert stages == {"stable", "dynamic"}
