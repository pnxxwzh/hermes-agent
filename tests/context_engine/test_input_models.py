"""Tests for unified input models."""

import pytest

from agent.context_engine import InputAssembly, InputNode
from agent.context_engine.models import ContextChunk
from agent.context_engine.tool_compaction import ShapedToolHistory
from agent.context_engine.tool_groups import ToolGroup


def _make_chunk(source="project_context", stage="stable", content="hello world"):
    return ContextChunk(
        source=source,
        stage=stage,
        slot=source,
        priority=1,
        content=content,
    )


class TestInputNode:
    def test_input_node_creation(self):
        node = InputNode(
            kind="message",
            name="messages_user",
            stage="request",
            content={"role": "user", "content": "hello"},
        )

        assert node.kind == "message"
        assert node.name == "messages_user"
        assert node.stage == "request"
        assert node.char_count > 0
        assert node.rough_tokens >= 0

    def test_input_node_rejects_invalid_kind(self):
        with pytest.raises(ValueError, match="kind must be one of"):
            InputNode(kind="bad", name="x", stage="request", content="oops")

    def test_input_node_rejects_invalid_stage(self):
        with pytest.raises(ValueError, match="stage must be one of"):
            InputNode(kind="message", name="x", stage="bad", content="oops")

    def test_from_context_chunk_round_trips(self):
        chunk = _make_chunk()
        node = InputNode.from_context_chunk(chunk)

        assert node.kind == "context"
        assert node.stage == "stable"
        assert node.name == "project_context"
        assert node.as_context_chunk() is chunk


class TestInputAssembly:
    def test_input_assembly_accepts_three_node_groups(self):
        assembly = InputAssembly(
            stable_nodes=[InputNode.from_context_chunk(_make_chunk("identity", "stable"))],
            dynamic_nodes=[InputNode.from_context_chunk(_make_chunk("plugin", "dynamic"))],
            request_nodes=[InputNode(
                kind="message",
                name="messages_user",
                stage="request",
                content={"role": "user", "content": "hello"},
            )],
        )

        assert len(assembly.stable_nodes) == 1
        assert len(assembly.dynamic_nodes) == 1
        assert len(assembly.request_nodes) == 1
        assert len(assembly.all_nodes) == 3

    def test_input_assembly_accepts_empty(self):
        assembly = InputAssembly()
        assert assembly.stable_nodes == []
        assert assembly.dynamic_nodes == []
        assert assembly.request_nodes == []
        assert assembly.context_chunks() == []

    def test_input_assembly_rejects_wrong_stage_group(self):
        with pytest.raises(ValueError, match="expected 'stable' nodes"):
            InputAssembly(
                stable_nodes=[InputNode(
                    kind="message",
                    name="messages_user",
                    stage="request",
                    content={"role": "user", "content": "hello"},
                )],
            )

    def test_context_metrics_only_include_context_nodes(self):
        assembly = InputAssembly(
            stable_nodes=[InputNode.from_context_chunk(_make_chunk("project_context", "stable", "x" * 8))],
            dynamic_nodes=[InputNode.from_context_chunk(_make_chunk("plugin", "dynamic", "y" * 8))],
            request_nodes=[InputNode(
                kind="message",
                name="messages_user",
                stage="request",
                content={"role": "user", "content": "hello"},
            )],
            normalized_messages=[{"role": "user", "content": "hello"}],
        )

        metrics = assembly.context_metrics

        assert metrics.stable_tokens == 2
        assert metrics.dynamic_tokens == 2
        assert {entry.source for entry in metrics.by_source} == {"project_context", "plugin"}

    def test_request_metrics_include_all_input_classes(self):
        assembly = InputAssembly(
            stable_nodes=[InputNode.from_context_chunk(_make_chunk("project_context", "stable", "x" * 8))],
            request_nodes=[
                InputNode(
                    kind="message",
                    name="messages_user",
                    stage="request",
                    content={"role": "user", "content": "hello"},
                ),
                InputNode(
                    kind="prefill",
                    name="prefill_messages",
                    stage="request",
                    content={"role": "assistant", "content": "prefill"},
                ),
                InputNode(
                    kind="tool_schema",
                    name="tool_schemas",
                    stage="request",
                    content=[{"name": "shell"}],
                ),
            ],
            normalized_messages=[{"role": "user", "content": "hello"}],
            prefill_messages=[{"role": "assistant", "content": "prefill"}],
            tool_schemas=[{"name": "shell"}],
        )

        metrics = assembly.request_metrics

        assert metrics.get_bucket("context_project") is not None
        assert metrics.get_bucket("messages_user") is not None
        assert metrics.get_bucket("prefill_messages") is not None
        assert metrics.get_bucket("tool_schemas") is not None

    def test_request_metrics_use_tool_compaction_sidecar_when_present(self):
        snapshot = ShapedToolHistory(
            shaped_messages=[{"role": "tool", "content": "trimmed", "tool_call_id": "call_1"}],
            hot_groups=[
                ToolGroup(
                    turn_index=1,
                    assistant_index=0,
                    tool_start_index=1,
                    tool_end_index=1,
                    assistant_message={"role": "assistant", "tool_calls": [{"id": "call_1"}]},
                    tool_messages=[{"role": "tool", "content": "trimmed", "tool_call_id": "call_1"}],
                    char_count=7,
                )
            ],
            warm_groups=[],
            cold_groups=[],
            message_heat_by_index={0: "hot"},
            message_persistence_by_index={0: "inline"},
        )
        assembly = InputAssembly(
            request_nodes=[InputNode(
                kind="message",
                name="messages_tool",
                stage="request",
                content={"role": "tool", "content": "trimmed", "tool_call_id": "call_1"},
            )],
            normalized_messages=[{"role": "tool", "content": "trimmed", "tool_call_id": "call_1"}],
            tool_compaction_snapshot=snapshot,
        )

        metrics = assembly.request_metrics

        assert metrics.get_bucket("messages_tool_hot") is not None
