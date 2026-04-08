"""Core data models for the context engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Rough token estimation (chars / 4, matching existing Hermes convention)
# ---------------------------------------------------------------------------

_CHARS_PER_TOKEN = 4


def _rough_tokens(text: str) -> int:
    if not text:
        return 0
    return len(text) // _CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# ContextChunk
# ---------------------------------------------------------------------------

@dataclass
class ContextChunk:
    """A single piece of context assembled into the system prompt."""

    source: str  # e.g. "memory", "sparkgraph_recall"
    stage: Literal["stable", "dynamic"]
    slot: str  # source-internal sub-slot, e.g. "memory", "user_profile"
    priority: int  # Phase 1 unused; reserved for future budget policies
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.stage not in ("stable", "dynamic"):
            raise ValueError(f"stage must be 'stable' or 'dynamic', got {self.stage!r}")
        # empty content is allowed (compat with sources that decide not to emit)
        # char_count in metadata for metrics
        if "char_count" not in self.metadata:
            self.metadata["char_count"] = len(self.content)


# ---------------------------------------------------------------------------
# SourceMetrics
# ---------------------------------------------------------------------------

@dataclass
class SourceMetrics:
    """Token / char metrics for a single context source."""

    source: str
    stage: Literal["stable", "dynamic"]
    char_count: int
    rough_tokens: int
    included: bool = True


# ---------------------------------------------------------------------------
# ContextMetrics
# ---------------------------------------------------------------------------

@dataclass
class ContextMetrics:
    """Aggregated context metrics covering all sources.

    Attributes
    ----------
    stable_tokens
        Rough token estimate for all stable chunks combined.
    dynamic_tokens
        Rough token estimate for all dynamic chunks combined.
    total_estimated_tokens
        stable_tokens + dynamic_tokens.
    by_source
        Per-source breakdown; one entry per source that produced output.
    """

    stable_tokens: int
    dynamic_tokens: int
    total_estimated_tokens: int
    by_source: list[SourceMetrics]

    def merged_with(self, other: "ContextMetrics | None") -> "ContextMetrics":
        """Combine two metric snapshots without mutating either input."""
        if other is None:
            return self
        return ContextMetrics(
            stable_tokens=self.stable_tokens + other.stable_tokens,
            dynamic_tokens=self.dynamic_tokens + other.dynamic_tokens,
            total_estimated_tokens=(
                self.total_estimated_tokens + other.total_estimated_tokens
            ),
            by_source=[*self.by_source, *other.by_source],
        )

    def sync_to(self, compressor) -> None:
        """Sync estimated tokens to a ContextCompressor instance.

        Writes total_estimated_tokens to compressor.last_prompt_tokens.
        Leaves last_completion_tokens unchanged.
        """
        if compressor is None:
            return
        # Guard against uninitialized compressor state
        current = getattr(compressor, "last_prompt_tokens", None)
        compressor.last_prompt_tokens = self.total_estimated_tokens


# ---------------------------------------------------------------------------
# AssemblyResult
# ---------------------------------------------------------------------------

@dataclass
class AssemblyResult:
    """Complete output of a context assembly operation.

    Attributes
    ----------
    stable_chunks
        All chunks for the cached system prompt, in assembly order.
    dynamic_chunks
        All per-turn chunks, in assembly order.
    stable_system
        The joined stable system prompt string.
    dynamic_system
        The joined dynamic system string.
    effective_system
        stable_system + "\n\n" + dynamic_system joined.
        Empty string when both inputs are empty.
    metrics
        Unified ContextMetrics for the assembled content.
    """

    stable_chunks: list[ContextChunk]
    dynamic_chunks: list[ContextChunk]
    stable_system: str
    dynamic_system: str
    effective_system: str
    metrics: ContextMetrics

    @staticmethod
    def _join_chunks(chunks: list[ContextChunk], separator: str = "\n\n") -> str:
        """Join chunk contents, skipping empty ones."""
        return separator.join(c.content for c in chunks if c.content.strip())

    @classmethod
    def from_chunks(
        cls,
        stable_chunks: list[ContextChunk],
        dynamic_chunks: list[ContextChunk],
    ) -> "AssemblyResult":
        stable_system = cls._join_chunks(stable_chunks)
        dynamic_system = cls._join_chunks(dynamic_chunks)

        # effective_system: strip to avoid leading/trailing newlines
        parts = []
        if stable_system:
            parts.append(stable_system)
        if dynamic_system:
            parts.append(dynamic_system)
        effective = ("\n\n".join(parts)).strip()

        # Build metrics
        stable_tokens = sum(_rough_tokens(c.content) for c in stable_chunks)
        dynamic_tokens = sum(_rough_tokens(c.content) for c in dynamic_chunks)
        total = stable_tokens + dynamic_tokens

        by_source: list[SourceMetrics] = []
        for c in stable_chunks:
            if c.content.strip():
                by_source.append(SourceMetrics(
                    source=c.source,
                    stage="stable",
                    char_count=len(c.content),
                    rough_tokens=_rough_tokens(c.content),
                    included=True,
                ))
        for c in dynamic_chunks:
            if c.content.strip():
                by_source.append(SourceMetrics(
                    source=c.source,
                    stage="dynamic",
                    char_count=len(c.content),
                    rough_tokens=_rough_tokens(c.content),
                    included=True,
                ))

        metrics = ContextMetrics(
            stable_tokens=stable_tokens,
            dynamic_tokens=dynamic_tokens,
            total_estimated_tokens=total,
            by_source=by_source,
        )
        return cls(
            stable_chunks=stable_chunks,
            dynamic_chunks=dynamic_chunks,
            stable_system=stable_system,
            dynamic_system=dynamic_system,
            effective_system=effective,
            metrics=metrics,
        )
