"""Tests for context_engine.context."""

import pytest

from agent.context_engine.context import AssemblyContext


class TestAssemblyContext:
    """T2: AssemblyContext defaults."""

    def test_assembly_context_defaults(self):
        """T2.1: default values are correct."""
        # Use None agent for testing defaults
        ctx = AssemblyContext(agent=None)
        assert ctx.agent is None
        assert ctx.system_message is None
        assert ctx.user_message is None
        assert ctx.cwd is None

    def test_assembly_context_conversation_history_empty_list(self):
        """T2.2: conversation_history defaults to [] not None."""
        ctx = AssemblyContext(agent=None)
        assert ctx.conversation_history == []
        assert isinstance(ctx.conversation_history, list)
        assert ctx.prefill_messages == []
        assert ctx.tool_schemas == []

    def test_assembly_context_all_fields_set(self):
        """T2.x: all fields can be set."""
        ctx = AssemblyContext(
            agent="fake_agent",
            system_message="sys",
            user_message="hello",
            cwd="/tmp",
            conversation_history=[{"role": "user", "content": "hi"}],
            prefill_messages=[{"role": "assistant", "content": "prefill"}],
            tool_schemas=[{"name": "shell"}],
        )
        assert ctx.agent == "fake_agent"
        assert ctx.system_message == "sys"
        assert ctx.user_message == "hello"
        assert ctx.cwd == "/tmp"
        assert len(ctx.conversation_history) == 1
        assert len(ctx.prefill_messages) == 1
        assert len(ctx.tool_schemas) == 1
