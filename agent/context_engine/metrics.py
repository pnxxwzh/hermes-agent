"""Unified metrics for context assembly."""

from __future__ import annotations

from agent.context_engine.models import ContextChunk, ContextMetrics, SourceMetrics

_CHARS_PER_TOKEN = 4


def rough_tokens(text: str) -> int:
    """Rough token estimate: chars / 4 (matching Hermes convention)."""
    if not text:
        return 0
    return len(text) // _CHARS_PER_TOKEN


def chunks_to_metrics(
    stable_chunks: list[ContextChunk],
    dynamic_chunks: list[ContextChunk],
) -> ContextMetrics:
    """Build ContextMetrics from assembled chunks.

    Each non-empty chunk contributes one SourceMetrics entry.
    """
    by_source: list[SourceMetrics] = []

    for chunk in stable_chunks:
        if chunk.content.strip():
            by_source.append(SourceMetrics(
                source=chunk.source,
                stage="stable",
                char_count=len(chunk.content),
                rough_tokens=rough_tokens(chunk.content),
                included=True,
            ))

    for chunk in dynamic_chunks:
        if chunk.content.strip():
            by_source.append(SourceMetrics(
                source=chunk.source,
                stage="dynamic",
                char_count=len(chunk.content),
                rough_tokens=rough_tokens(chunk.content),
                included=True,
            ))

    stable_tokens = sum(s.rough_tokens for s in by_source if s.stage == "stable")
    dynamic_tokens = sum(s.rough_tokens for s in by_source if s.stage == "dynamic")

    return ContextMetrics(
        stable_tokens=stable_tokens,
        dynamic_tokens=dynamic_tokens,
        total_estimated_tokens=stable_tokens + dynamic_tokens,
        by_source=by_source,
    )
