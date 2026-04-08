"""Unified input models for full-input context assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agent.context_engine.models import AssemblyResult, ContextChunk, ContextMetrics

_CHARS_PER_TOKEN = 4
_VALID_KINDS = {"context", "message", "prefill", "tool_schema"}
_VALID_STAGES = {"stable", "dynamic", "request"}


def _safe_char_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, ContextChunk):
        return len(value.content)
    try:
        return len(str(value))
    except Exception:
        try:
            return len(repr(type(value)))
        except Exception:
            return 0


def _rough_tokens_from_chars(char_count: int) -> int:
    if char_count <= 0:
        return 0
    return char_count // _CHARS_PER_TOKEN


@dataclass
class InputNode:
    """A single semantic input unit sent toward model assembly."""

    kind: Literal["context", "message", "prefill", "tool_schema"]
    name: str
    stage: Literal["stable", "dynamic", "request"]
    content: Any
    char_count: int | None = None
    rough_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in _VALID_KINDS:
            raise ValueError(f"kind must be one of {_VALID_KINDS!r}, got {self.kind!r}")
        if self.stage not in _VALID_STAGES:
            raise ValueError(f"stage must be one of {_VALID_STAGES!r}, got {self.stage!r}")
        if self.char_count is None:
            self.char_count = _safe_char_count(self.content)
        if self.rough_tokens is None:
            self.rough_tokens = _rough_tokens_from_chars(self.char_count)

    @classmethod
    def from_context_chunk(cls, chunk: ContextChunk) -> "InputNode":
        """Create a context node that preserves the original chunk object."""
        return cls(
            kind="context",
            name=chunk.source,
            stage=chunk.stage,
            content=chunk,
            char_count=len(chunk.content),
            rough_tokens=_rough_tokens_from_chars(len(chunk.content)),
            metadata={
                "slot": chunk.slot,
                "priority": chunk.priority,
                **dict(chunk.metadata),
            },
        )

    def as_context_chunk(self) -> ContextChunk:
        """Return the underlying ContextChunk for context nodes."""
        if self.kind != "context":
            raise TypeError(f"InputNode kind {self.kind!r} cannot be converted to ContextChunk")
        if isinstance(self.content, ContextChunk):
            return self.content
        return ContextChunk(
            source=self.name,
            stage=self.stage,
            slot=str(self.metadata.get("slot", self.name)),
            priority=int(self.metadata.get("priority", 0)),
            content=str(self.content or ""),
            metadata={
                key: value
                for key, value in self.metadata.items()
                if key not in {"slot", "priority"}
            },
        )


@dataclass
class AssemblySnapshots:
    """Compatibility snapshots derived from a unified input assembly."""

    context: AssemblyResult
    request: Any | None = None


@dataclass
class InputAssembly:
    """Semantic snapshot of the full input prepared for model transport."""

    stable_nodes: list[InputNode] = field(default_factory=list)
    dynamic_nodes: list[InputNode] = field(default_factory=list)
    request_nodes: list[InputNode] = field(default_factory=list)
    effective_system: str = ""
    normalized_messages: list[dict[str, Any]] = field(default_factory=list)
    prefill_messages: list[dict[str, Any]] = field(default_factory=list)
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._validate_stage(self.stable_nodes, expected="stable")
        self._validate_stage(self.dynamic_nodes, expected="dynamic")
        self._validate_stage(self.request_nodes, expected="request")

    @staticmethod
    def _validate_stage(nodes: list[InputNode], *, expected: str) -> None:
        for node in nodes:
            if node.stage != expected:
                raise ValueError(
                    f"InputAssembly expected {expected!r} nodes, got {node.stage!r} for {node.name!r}",
                )

    @property
    def all_nodes(self) -> list[InputNode]:
        return [*self.stable_nodes, *self.dynamic_nodes, *self.request_nodes]

    def context_chunks(self, stage: Literal["stable", "dynamic"] | None = None) -> list[ContextChunk]:
        nodes = [*self.stable_nodes, *self.dynamic_nodes]
        chunks = [node.as_context_chunk() for node in nodes if node.kind == "context"]
        if stage is None:
            return chunks
        return [chunk for chunk in chunks if chunk.stage == stage]

    @property
    def context_metrics(self) -> ContextMetrics:
        result = AssemblyResult.from_chunks(
            stable_chunks=self.context_chunks("stable"),
            dynamic_chunks=self.context_chunks("dynamic"),
        )
        return result.metrics

    @property
    def request_metrics(self):
        from agent.context_engine.request_metrics import build_request_metrics

        return build_request_metrics(
            stable_chunks=self.context_chunks("stable"),
            dynamic_chunks=self.context_chunks("dynamic"),
            messages=self.normalized_messages,
            prefill_messages=self.prefill_messages,
            tools=self.tool_schemas,
            context_metrics=self.context_metrics,
        )
