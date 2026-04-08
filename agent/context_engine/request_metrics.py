"""Request-level metrics for full model input breakdown."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.context_engine.models import ContextChunk, ContextMetrics

_CHARS_PER_TOKEN = 4
_CONTEXT_BUCKET_PREFIX = "context_"


def rough_tokens_from_text(text: str) -> int:
    """Rough token estimate using the shared Hermes chars/4 convention."""
    if not text:
        return 0
    return len(text) // _CHARS_PER_TOKEN


def _safe_len(value: Any) -> int:
    """Best-effort character count that never raises."""
    if value is None:
        return 0
    try:
        return len(str(value))
    except Exception:
        try:
            return len(repr(type(value)))
        except Exception:
            return 0


def rough_tokens_from_message(message: dict[str, Any]) -> int:
    """Rough token estimate for a message payload."""
    return _safe_len(message) // _CHARS_PER_TOKEN


def _bucket_for_context_source(source: str) -> str:
    if source == "project_context":
        return "context_project"
    return f"{_CONTEXT_BUCKET_PREFIX}{source}"


def _bucket_for_message(message: dict[str, Any]) -> str:
    role = str(message.get("role", "") or "").strip().lower()
    if role == "user":
        return "messages_user"
    if role == "assistant":
        return "messages_assistant"
    if role == "tool":
        return "messages_tool"
    return "messages_other"


@dataclass
class RequestBucketMetrics:
    """Token / char metrics for one request-level input bucket."""

    bucket: str
    char_count: int
    rough_tokens: int
    included: bool = True
    category: str = "other"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RequestMetrics:
    """Complete request-level breakdown for a single model input."""

    total_estimated_tokens: int
    total_char_count: int
    by_bucket: list[RequestBucketMetrics]
    context_metrics: ContextMetrics | None = None
    version: int = 1

    def get_bucket(self, name: str) -> RequestBucketMetrics | None:
        """Return the first bucket matching ``name`` or ``None``."""
        for bucket in self.by_bucket:
            if bucket.bucket == name:
                return bucket
        return None


def build_request_metrics(
    *,
    stable_chunks: list[ContextChunk] | None = None,
    dynamic_chunks: list[ContextChunk] | None = None,
    messages: list[dict[str, Any]] | None = None,
    prefill_messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    context_metrics: ContextMetrics | None = None,
) -> RequestMetrics:
    """Build request-level metrics from context chunks and request payload buckets."""

    bucket_map: dict[str, RequestBucketMetrics] = {}

    def add_bucket(
        bucket_name: str,
        *,
        char_count: int,
        category: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if char_count < 0:
            char_count = 0
        rough_tokens = char_count // _CHARS_PER_TOKEN
        existing = bucket_map.get(bucket_name)
        if existing is None:
            bucket_map[bucket_name] = RequestBucketMetrics(
                bucket=bucket_name,
                char_count=char_count,
                rough_tokens=rough_tokens,
                category=category,
                metadata=metadata or {},
            )
            return
        existing.char_count += char_count
        existing.rough_tokens += rough_tokens
        if metadata:
            existing.metadata.update(metadata)

    for chunk in stable_chunks or []:
        if not chunk.content:
            continue
        add_bucket(
            _bucket_for_context_source(chunk.source),
            char_count=len(chunk.content),
            category="context",
            metadata={"stage": "stable"},
        )

    for chunk in dynamic_chunks or []:
        if not chunk.content:
            continue
        add_bucket(
            _bucket_for_context_source(chunk.source),
            char_count=len(chunk.content),
            category="context",
            metadata={"stage": "dynamic"},
        )

    for message in messages or []:
        add_bucket(
            _bucket_for_message(message),
            char_count=_safe_len(message),
            category="messages",
        )

    for message in prefill_messages or []:
        add_bucket(
            "prefill_messages",
            char_count=_safe_len(message),
            category="prefill",
        )

    if tools:
        add_bucket(
            "tool_schemas",
            char_count=_safe_len(tools),
            category="tools",
        )

    by_bucket = list(bucket_map.values())
    total_char_count = sum(bucket.char_count for bucket in by_bucket)
    total_estimated_tokens = sum(bucket.rough_tokens for bucket in by_bucket)

    return RequestMetrics(
        total_estimated_tokens=total_estimated_tokens,
        total_char_count=total_char_count,
        by_bucket=by_bucket,
        context_metrics=context_metrics,
    )
