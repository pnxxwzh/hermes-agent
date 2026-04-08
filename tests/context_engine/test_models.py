"""Tests for context_engine.models."""

import pytest

from agent.context_engine.models import (
    AssemblyResult,
    ContextChunk,
    ContextMetrics,
    SourceMetrics,
)


class TestContextChunk:
    """T1: ContextChunk validation and metadata."""

    def test_context_chunk_stage_validation(self):
        """T1.1: stage="invalid" raises ValueError."""
        with pytest.raises(ValueError, match="stage must be 'stable' or 'dynamic'"):
            ContextChunk(
                source="test",
                stage="invalid",
                slot="slot",
                priority=1,
                content="hello",
            )

    def test_context_chunk_empty_content(self):
        """T1.2: content="" does not raise, char_count=0."""
        chunk = ContextChunk(
            source="test",
            stage="stable",
            slot="slot",
            priority=1,
            content="",
        )
        assert chunk.content == ""
        assert chunk.metadata["char_count"] == 0

    def test_context_chunk_normal(self):
        """T1.x: normal creation works."""
        chunk = ContextChunk(
            source="memory",
            stage="stable",
            slot="memory",
            priority=1,
            content="some content",
        )
        assert chunk.source == "memory"
        assert chunk.stage == "stable"
        assert chunk.metadata["char_count"] == 12


class TestContextMetrics:
    """T1: ContextMetrics sync_to."""

    def test_context_metrics_sync_to_none(self):
        """T1.3: sync_to(None) does not raise."""
        metrics = ContextMetrics(
            stable_tokens=100,
            dynamic_tokens=50,
            total_estimated_tokens=150,
            by_source=[],
        )
        # Should not raise
        metrics.sync_to(None)

    def test_context_metrics_sync_to_updates_field(self):
        """T1.4: sync_to updates last_prompt_tokens."""
        class FakeCompressor:
            def __init__(self):
                self.last_prompt_tokens = 0

        compressor = FakeCompressor()
        metrics = ContextMetrics(
            stable_tokens=100,
            dynamic_tokens=50,
            total_estimated_tokens=150,
            by_source=[],
        )
        metrics.sync_to(compressor)
        assert compressor.last_prompt_tokens == 150

    def test_context_metrics_sync_to_preserves_other_fields(self):
        """T1.x: sync_to only changes last_prompt_tokens."""
        class FakeCompressor:
            last_prompt_tokens = 0
            last_completion_tokens = 10
            some_other_field = "unchanged"

        compressor = FakeCompressor()
        metrics = ContextMetrics(
            stable_tokens=100,
            dynamic_tokens=0,
            total_estimated_tokens=100,
            by_source=[],
        )
        metrics.sync_to(compressor)
        assert compressor.last_prompt_tokens == 100
        assert compressor.last_completion_tokens == 10

    def test_context_metrics_merged_with_combines_counts_and_sources(self):
        """T1.x: merged_with returns a new aggregate snapshot."""
        left = ContextMetrics(
            stable_tokens=100,
            dynamic_tokens=0,
            total_estimated_tokens=100,
            by_source=[SourceMetrics("identity", "stable", 400, 100)],
        )
        right = ContextMetrics(
            stable_tokens=0,
            dynamic_tokens=20,
            total_estimated_tokens=20,
            by_source=[SourceMetrics("plugin", "dynamic", 80, 20)],
        )

        merged = left.merged_with(right)

        assert merged.stable_tokens == 100
        assert merged.dynamic_tokens == 20
        assert merged.total_estimated_tokens == 120
        assert [s.source for s in merged.by_source] == ["identity", "plugin"]
        assert left.total_estimated_tokens == 100
        assert right.total_estimated_tokens == 20


class TestAssemblyResult:
    """T1: AssemblyResult creation and properties."""

    def test_assembly_result_from_chunks_empty(self):
        """T1.5: all empty chunks, effective_system=""."""
        result = AssemblyResult.from_chunks([], [])
        assert result.stable_system == ""
        assert result.dynamic_system == ""
        assert result.effective_system == ""

    def test_assembly_result_from_chunks_partial(self):
        """T1.6: only stable, dynamic empty, effective=stable."""
        chunks = [ContextChunk(
            source="identity",
            stage="stable",
            slot="soul",
            priority=1,
            content="you are hermes",
        )]
        result = AssemblyResult.from_chunks(chunks, [])
        assert result.stable_system == "you are hermes"
        assert result.dynamic_system == ""
        assert result.effective_system == "you are hermes"

    def test_assembly_result_by_source_contains_all_sources(self):
        """T1.7: each non-empty chunk enters by_source."""
        stable = [ContextChunk(
            source="memory",
            stage="stable",
            slot="memory",
            priority=1,
            content="remember this",
        )]
        dynamic = [ContextChunk(
            source="sparkgraph_recall",
            stage="dynamic",
            slot="recall",
            priority=1,
            content="past info",
        )]
        result = AssemblyResult.from_chunks(stable, dynamic)
        assert len(result.metrics.by_source) == 2
        sources = {sm.source for sm in result.metrics.by_source}
        assert sources == {"memory", "sparkgraph_recall"}

    def test_assembly_result_tokens_sum(self):
        """T1.8: stable + dynamic = total."""
        # "abcdefghijkl" = 12 chars, rough_tokens = 12 // 4 = 3
        stable = [ContextChunk(
            source="a", stage="stable", slot="s", priority=1, content="abcdefghijkl"
        )]
        # "xyzw" = 4 chars, rough_tokens = 1
        dynamic = [ContextChunk(
            source="b", stage="dynamic", slot="d", priority=1, content="xyzw"
        )]
        result = AssemblyResult.from_chunks(stable, dynamic)
        assert result.metrics.stable_tokens == 3
        assert result.metrics.dynamic_tokens == 1
        assert result.metrics.total_estimated_tokens == 4

    def test_assembly_result_effective_system_joiner(self):
        """T1.9: effective_system uses double newline join."""
        stable = [ContextChunk(
            source="a", stage="stable", slot="s", priority=1, content="stable"
        )]
        dynamic = [ContextChunk(
            source="b", stage="dynamic", slot="d", priority=1, content="dynamic"
        )]
        result = AssemblyResult.from_chunks(stable, dynamic)
        assert result.effective_system == "stable\n\ndynamic"

    def test_assembly_result_only_dynamic(self):
        """T1.x: only dynamic chunks."""
        dynamic = [ContextChunk(
            source="sg", stage="dynamic", slot="r", priority=1, content="recall"
        )]
        result = AssemblyResult.from_chunks([], dynamic)
        assert result.stable_system == ""
        assert result.dynamic_system == "recall"
        assert result.effective_system == "recall"

    def test_assembly_result_strips_trailing_whitespace(self):
        """T1.x: effective_system strips leading/trailing whitespace.

        effective_system is prepended to API messages; it should not have
        leading/trailing whitespace regardless of chunk content.
        """
        stable = [ContextChunk(
            source="a", stage="stable", slot="s", priority=1, content="  spaced  "
        )]
        result = AssemblyResult.from_chunks(stable, [])
        # .strip() is applied to the joined result
        assert result.effective_system == "spaced"

    def test_assembly_result_skips_empty_content_chunks(self):
        """T1.x: chunks with empty content don't appear in by_source."""
        stable = [
            ContextChunk(source="a", stage="stable", slot="s", priority=1, content=""),
            ContextChunk(source="b", stage="stable", slot="s", priority=2, content="real"),
        ]
        result = AssemblyResult.from_chunks(stable, [])
        assert len(result.metrics.by_source) == 1
        assert result.metrics.by_source[0].source == "b"
