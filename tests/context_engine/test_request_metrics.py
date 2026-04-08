"""Tests for request-level metrics and builder helpers."""

from agent.context_engine.models import ContextChunk, ContextMetrics
from agent.context_engine.request_metrics import (
    RequestBucketMetrics,
    RequestMetrics,
    build_request_metrics,
    rough_tokens_from_message,
    rough_tokens_from_text,
)


class TestRequestHelpers:
    """Low-level rough token helpers."""

    def test_rough_tokens_from_text_empty(self):
        assert rough_tokens_from_text("") == 0

    def test_rough_tokens_from_text_normal(self):
        assert rough_tokens_from_text("abcdefgh") == 2

    def test_rough_tokens_from_message_uses_string_length(self):
        msg = {"role": "user", "content": "hello"}
        assert rough_tokens_from_message(msg) == len(str(msg)) // 4

    def test_rough_tokens_from_message_ignores_cache_control(self):
        msg = {
            "role": "assistant",
            "content": "hello",
            "cache_control": {"type": "ephemeral"},
        }
        expected = len(str({"role": "assistant", "content": "hello"})) // 4
        assert rough_tokens_from_message(msg) == expected


class TestRequestBucketMetrics:
    """RequestBucketMetrics basics."""

    def test_request_bucket_metrics_constructs(self):
        bucket = RequestBucketMetrics(
            bucket="messages_user",
            char_count=12,
            rough_tokens=3,
            category="messages",
        )
        assert bucket.bucket == "messages_user"
        assert bucket.char_count == 12
        assert bucket.rough_tokens == 3
        assert bucket.category == "messages"


class TestRequestMetrics:
    """RequestMetrics helpers."""

    def test_get_bucket_returns_match(self):
        metrics = RequestMetrics(
            total_estimated_tokens=3,
            total_char_count=12,
            by_bucket=[
                RequestBucketMetrics(
                    bucket="messages_user",
                    char_count=12,
                    rough_tokens=3,
                    category="messages",
                ),
            ],
        )
        assert metrics.get_bucket("messages_user") is not None
        assert metrics.get_bucket("missing") is None


class TestBuildRequestMetrics:
    """Request metrics builder behavior."""

    def test_build_request_metrics_empty(self):
        metrics = build_request_metrics()
        assert metrics.total_estimated_tokens == 0
        assert metrics.total_char_count == 0
        assert metrics.by_bucket == []

    def test_build_request_metrics_maps_context_sources(self):
        stable = [
            ContextChunk(
                source="identity",
                stage="stable",
                slot="default",
                priority=1,
                content="a" * 8,
            ),
            ContextChunk(
                source="project_context",
                stage="stable",
                slot="context_files",
                priority=2,
                content="b" * 12,
            ),
        ]
        dynamic = [
            ContextChunk(
                source="plugin",
                stage="dynamic",
                slot="plugin_context",
                priority=1,
                content="c" * 4,
            ),
        ]
        metrics = build_request_metrics(stable_chunks=stable, dynamic_chunks=dynamic)
        assert metrics.get_bucket("context_identity").rough_tokens == 2
        assert metrics.get_bucket("context_project").rough_tokens == 3
        assert metrics.get_bucket("context_plugin").rough_tokens == 1

    def test_build_request_metrics_classifies_messages(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi", "tool_calls": [{"id": "x"}]},
            {"role": "tool", "content": "result"},
            {"content": "mystery"},
        ]
        metrics = build_request_metrics(messages=messages)
        assert metrics.get_bucket("messages_user").char_count == len(str(messages[0]))
        assert metrics.get_bucket("messages_assistant").char_count == len(str(messages[1]))
        assert metrics.get_bucket("messages_tool").char_count == len(str(messages[2]))
        assert metrics.get_bucket("messages_other").char_count == len(str(messages[3]))

    def test_build_request_metrics_classifies_tool_heat_buckets(self):
        messages = [
            {"role": "tool", "content": "hot", "tool_call_id": "call_1"},
            {"role": "tool", "content": "warm", "tool_call_id": "call_2"},
            {"role": "tool", "content": "cold", "tool_call_id": "call_3"},
        ]
        metrics = build_request_metrics(
            messages=messages,
            message_heat_by_index={0: "hot", 1: "warm", 2: "cold"},
        )
        assert metrics.get_bucket("messages_tool_hot").char_count == len(str(messages[0]))
        assert metrics.get_bucket("messages_tool_warm").char_count == len(str(messages[1]))
        assert metrics.get_bucket("messages_tool_cold").char_count == len(str(messages[2]))

    def test_build_request_metrics_prefill_is_separate(self):
        prefill = [
            {"role": "user", "content": "few-shot user"},
            {"role": "assistant", "content": "few-shot assistant"},
        ]
        metrics = build_request_metrics(
            messages=[{"role": "user", "content": "real user"}],
            prefill_messages=prefill,
        )
        assert metrics.get_bucket("prefill_messages").char_count == sum(
            len(str(msg)) for msg in prefill
        )
        assert metrics.get_bucket("messages_user").char_count == len(
            str({"role": "user", "content": "real user"})
        )

    def test_build_request_metrics_tools_bucket(self):
        tools = [{"name": "terminal", "parameters": {"type": "object"}}]
        metrics = build_request_metrics(tools=tools)
        assert metrics.get_bucket("tool_schemas").char_count == len(str(tools))

    def test_build_request_metrics_supports_structured_message_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                ],
            },
            {
                "role": "assistant",
                "content": {"text": "structured reply"},
            },
        ]
        metrics = build_request_metrics(messages=messages)
        assert metrics.get_bucket("messages_user").char_count == len(str(messages[0]))
        assert metrics.get_bucket("messages_assistant").char_count == len(str(messages[1]))

    def test_build_request_metrics_missing_role_falls_back_to_other(self):
        message = {"content": "role missing"}
        metrics = build_request_metrics(messages=[message])
        assert metrics.get_bucket("messages_other").char_count == len(str(message))

    def test_build_request_metrics_ignores_cache_control_in_message_size(self):
        message = {
            "role": "assistant",
            "content": "reply",
            "cache_control": {"type": "ephemeral"},
        }
        metrics = build_request_metrics(messages=[message])
        expected = len(str({"role": "assistant", "content": "reply"}))
        assert metrics.get_bucket("messages_assistant").char_count == expected

    def test_build_request_metrics_aggregates_duplicate_buckets(self):
        stable = [
            ContextChunk(
                source="memory",
                stage="stable",
                slot="memory",
                priority=1,
                content="a" * 8,
            ),
            ContextChunk(
                source="memory",
                stage="stable",
                slot="memory",
                priority=2,
                content="b" * 4,
            ),
        ]
        metrics = build_request_metrics(stable_chunks=stable)
        bucket = metrics.get_bucket("context_memory")
        assert bucket.char_count == 12
        assert bucket.rough_tokens == 3

    def test_build_request_metrics_total_equals_sum_of_buckets(self):
        stable = [
            ContextChunk(
                source="identity",
                stage="stable",
                slot="default",
                priority=1,
                content="a" * 8,
            ),
        ]
        messages = [{"role": "user", "content": "hello"}]
        tools = [{"name": "terminal"}]
        context_metrics = ContextMetrics(
            stable_tokens=2,
            dynamic_tokens=0,
            total_estimated_tokens=2,
            by_source=[],
        )
        metrics = build_request_metrics(
            stable_chunks=stable,
            messages=messages,
            tools=tools,
            context_metrics=context_metrics,
        )
        assert metrics.total_estimated_tokens == sum(
            bucket.rough_tokens for bucket in metrics.by_bucket
        )
        assert metrics.total_char_count == sum(
            bucket.char_count for bucket in metrics.by_bucket
        )
        assert metrics.context_metrics is context_metrics
