"""Tests for the unified input assembler interface."""

from unittest.mock import MagicMock

from agent.context_engine.assembler import ContextAssembler
from agent.context_engine.models import ContextChunk


def _chunk(source, stage, content):
    return ContextChunk(
        source=source,
        stage=stage,
        slot=source,
        priority=1,
        content=content,
    )


class TestInputAssembler:
    def test_assemble_returns_three_node_classes(self):
        assembler = ContextAssembler(
            MagicMock(),
            stable_factories=[("identity", lambda ctx: [_chunk("identity", "stable", "stable")])],
            dynamic_factories=[("plugin", lambda ctx: [_chunk("plugin", "dynamic", "dynamic")])],
        )

        assembly = assembler.assemble(
            conversation_history=[{"role": "user", "content": "hello"}],
            prefill_messages=[{"role": "assistant", "content": "prefill"}],
            tool_schemas=[{"name": "shell"}],
        )

        assert [node.name for node in assembly.stable_nodes] == ["identity"]
        assert [node.name for node in assembly.dynamic_nodes] == ["plugin"]
        assert [node.name for node in assembly.request_nodes] == [
            "messages_user",
            "prefill_messages",
            "tool_schemas",
        ]

    def test_effective_system_is_built_from_context_nodes(self):
        assembler = ContextAssembler(
            MagicMock(),
            stable_factories=[("identity", lambda ctx: [_chunk("identity", "stable", "STABLE")])],
            dynamic_factories=[("plugin", lambda ctx: [_chunk("plugin", "dynamic", "DYNAMIC")])],
            request_factories=[],
        )

        assembly = assembler.assemble()

        assert assembly.effective_system == "STABLE\n\nDYNAMIC"

    def test_normalized_messages_are_detached_copies(self):
        history = [{"role": "user", "content": "hello"}]
        assembler = ContextAssembler(MagicMock(), stable_factories=[], dynamic_factories=[])

        assembly = assembler.assemble(conversation_history=history)
        history[0]["content"] = "changed"

        assert assembly.normalized_messages == [{"role": "user", "content": "hello"}]

    def test_prefill_and_tool_schemas_are_preserved(self):
        prefill = [{"role": "assistant", "content": "prefill"}]
        tools = [{"name": "shell"}]
        assembler = ContextAssembler(MagicMock(), stable_factories=[], dynamic_factories=[])

        assembly = assembler.assemble(prefill_messages=prefill, tool_schemas=tools)

        assert assembly.prefill_messages == prefill
        assert assembly.tool_schemas == tools

    def test_context_metrics_only_cover_context_nodes(self):
        assembler = ContextAssembler(
            MagicMock(),
            stable_factories=[("identity", lambda ctx: [_chunk("identity", "stable", "s" * 8)])],
            dynamic_factories=[("plugin", lambda ctx: [_chunk("plugin", "dynamic", "d" * 8)])],
        )

        assembly = assembler.assemble(
            conversation_history=[{"role": "user", "content": "hello"}],
            tool_schemas=[{"name": "shell"}],
        )

        assert assembly.context_metrics.stable_tokens == 2
        assert assembly.context_metrics.dynamic_tokens == 2
        assert {entry.source for entry in assembly.context_metrics.by_source} == {"identity", "plugin"}

    def test_request_metrics_cover_context_messages_prefill_and_tools(self):
        assembler = ContextAssembler(
            MagicMock(),
            stable_factories=[("project_context", lambda ctx: [_chunk("project_context", "stable", "s" * 8)])],
            dynamic_factories=[],
        )

        assembly = assembler.assemble(
            conversation_history=[{"role": "user", "content": "hello"}],
            prefill_messages=[{"role": "assistant", "content": "prefill"}],
            tool_schemas=[{"name": "shell"}],
        )

        metrics = assembly.request_metrics

        assert metrics.get_bucket("context_project") is not None
        assert metrics.get_bucket("messages_user") is not None
        assert metrics.get_bucket("prefill_messages") is not None
        assert metrics.get_bucket("tool_schemas") is not None

    def test_assemble_stable_remains_compatible(self):
        assembler = ContextAssembler(
            MagicMock(),
            stable_factories=[("identity", lambda ctx: [_chunk("identity", "stable", "stable")])],
            dynamic_factories=[],
            request_factories=[],
        )

        result = assembler.assemble_stable()

        assert [chunk.source for chunk in result.stable_chunks] == ["identity"]
        assert result.dynamic_chunks == []
        assert result.stable_system == "stable"

    def test_assemble_dynamic_remains_compatible(self):
        assembler = ContextAssembler(
            MagicMock(),
            stable_factories=[],
            dynamic_factories=[("plugin", lambda ctx: [_chunk("plugin", "dynamic", "dynamic")])],
            request_factories=[],
        )

        result = assembler.assemble_dynamic(
            conversation_history=[{"role": "user", "content": "hello"}],
        )

        assert result.stable_chunks == []
        assert [chunk.source for chunk in result.dynamic_chunks] == ["plugin"]
        assert result.dynamic_system == "dynamic"
