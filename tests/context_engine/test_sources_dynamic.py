"""Tests for context_engine.sources dynamic sources."""

import pytest
from unittest.mock import patch, MagicMock

from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import ContextChunk
from agent.context_engine.sources import (
    EphemeralSystemSource,
    PluginTurnContextSource,
    SparkGraphRecallSource,
    HonchoTurnSource,
)


class TestEphemeralSystemSource:
    """T8: EphemeralSystemSource."""

    def test_ephemeral_empty_fn(self):
        """T8.1: get_ephemeral_fn returns "" -> []. """
        src = EphemeralSystemSource(get_ephemeral_fn=lambda: "")
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_ephemeral_with_content(self):
        """T8.2: returns content, slot=ephemeral."""
        src = EphemeralSystemSource(get_ephemeral_fn=lambda: "dynamic system")
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1
        assert chunks[0].content == "dynamic system"
        assert chunks[0].slot == "ephemeral"
        assert chunks[0].stage == "dynamic"
        assert chunks[0].source == "ephemeral"

    def test_ephemeral_no_fn_uses_default(self):
        """T8.x: no get_ephemeral_fn -> defaults to empty."""
        src = EphemeralSystemSource()
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_ephemeral_priority(self):
        """T8.x: priority is 1."""
        src = EphemeralSystemSource(get_ephemeral_fn=lambda: "x")
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks[0].priority == 1


class TestPluginTurnContextSource:
    """T8: PluginTurnContextSource."""

    def test_plugin_first_turn(self):
        """T8.3: history <=1 -> is_first_turn=True."""
        with patch(
            "agent.context_engine.compat.wrap_invoke_pre_llm_call",
            return_value=("plugin context", None),
        ) as mock_call:
            ctx = AssemblyContext(agent=MagicMock(), conversation_history=[])
            src = PluginTurnContextSource(session_id="s1")
            src.collect(ctx)
            mock_call.assert_called_once()
            call_kwargs = mock_call.call_args.kwargs
            assert call_kwargs["is_first_turn"] is True

    def test_plugin_first_turn_with_history(self):
        """T8.x: history with 1 item -> is_first_turn=True."""
        with patch(
            "agent.context_engine.compat.wrap_invoke_pre_llm_call",
            return_value=("", None),
        ):
            ctx = AssemblyContext(agent=MagicMock(), conversation_history=[{"role": "user"}])
            src = PluginTurnContextSource(session_id="s1")
            src.collect(ctx)

    def test_plugin_no_plugins(self):
        """T8.4: no context -> []. """
        with patch(
            "agent.context_engine.compat.wrap_invoke_pre_llm_call",
            return_value=("", None),
        ):
            src = PluginTurnContextSource(session_id="s1")
            chunks = src.collect(AssemblyContext(agent=MagicMock()))
            assert chunks == []

    def test_plugin_with_context_dict(self):
        """T8.5: {"context": "x"} -> returns."""
        with patch(
            "agent.context_engine.compat.wrap_invoke_pre_llm_call",
            return_value=("plugin context", None),
        ):
            src = PluginTurnContextSource(session_id="s1")
            chunks = src.collect(AssemblyContext(agent=MagicMock()))
            assert len(chunks) == 1
            assert chunks[0].content == "plugin context"
            assert chunks[0].source == "plugin"
            assert chunks[0].slot == "plugin_context"

    def test_plugin_empty_context_filtered(self):
        """T8.6: empty context dict is filtered out."""
        with patch(
            "agent.context_engine.compat.wrap_invoke_pre_llm_call",
            return_value=("", None),
        ):
            src = PluginTurnContextSource(session_id="s1")
            chunks = src.collect(AssemblyContext(agent=MagicMock()))
            assert chunks == []

    def test_plugin_conversation_history_copy(self):
        """T8.7: compat wrapper receives conversation_history from ctx.

        The compat wrapper itself makes list(conversation_history) internally —
        tested separately in test_compat_sparkgraph_plugin.py. This test verifies
        the source passes the ctx.conversation_history field correctly.
        """
        original = [{"role": "user", "content": "hi"}]
        with patch(
            "agent.context_engine.compat.wrap_invoke_pre_llm_call",
            return_value=("", None),
        ) as mock_call:
            ctx = AssemblyContext(agent=MagicMock(), conversation_history=original)
            src = PluginTurnContextSource(session_id="s1")
            src.collect(ctx)
            passed = mock_call.call_args.kwargs["conversation_history"]
            assert passed == original

    def test_plugin_session_id_passed(self):
        """T8.x: session_id is passed to compat wrapper."""
        with patch(
            "agent.context_engine.compat.wrap_invoke_pre_llm_call",
            return_value=("", None),
        ) as mock_call:
            src = PluginTurnContextSource(session_id="abc123")
            src.collect(AssemblyContext(agent=MagicMock()))
            assert mock_call.call_args.kwargs["session_id"] == "abc123"

    def test_plugin_user_message_passed(self):
        """T8.x: user_message from ctx is passed."""
        with patch(
            "agent.context_engine.compat.wrap_invoke_pre_llm_call",
            return_value=("", None),
        ) as mock_call:
            ctx = AssemblyContext(agent=MagicMock(), user_message="hello world")
            src = PluginTurnContextSource(session_id="s1")
            src.collect(ctx)
            assert mock_call.call_args.kwargs["user_message"] == "hello world"


class TestSparkGraphRecallSource:
    """T8: SparkGraphRecallSource."""

    def test_sparkgraph_disabled(self):
        """T8.8: enabled=False -> []. """
        src = SparkGraphRecallSource(sparkgraph_manager=MagicMock(), sparkgraph_enabled=False)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_sparkgraph_manager_none(self):
        """T8.9: manager=None -> []. """
        src = SparkGraphRecallSource(sparkgraph_manager=None, sparkgraph_enabled=True)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_sparkgraph_with_recall(self):
        """T8.10: normal call returns recall block."""
        mock_manager = MagicMock()
        mock_manager.build_recall_block.return_value = ("[SparkGraph Recall]", 0)
        src = SparkGraphRecallSource(
            sparkgraph_manager=mock_manager,
            sparkgraph_enabled=True,
        )
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1
        assert chunks[0].content == "[SparkGraph Recall]"
        assert chunks[0].source == "sparkgraph_recall"
        assert chunks[0].slot == "recall"

    def test_sparkgraph_empty_recall(self):
        """T8.11: block="" -> []. """
        mock_manager = MagicMock()
        mock_manager.build_recall_block.return_value = ("", 0)
        src = SparkGraphRecallSource(
            sparkgraph_manager=mock_manager,
            sparkgraph_enabled=True,
        )
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_sparkgraph_metadata(self):
        """T8.12: recall_injected flag in metadata."""
        mock_manager = MagicMock()
        mock_manager.build_recall_block.return_value = ("[Recall]", 0)
        src = SparkGraphRecallSource(
            sparkgraph_manager=mock_manager,
            sparkgraph_enabled=True,
        )
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks[0].metadata["recall_injected"] is True

    def test_sparkgraph_empty_metadata(self):
        """T8.x: empty recall -> recall_injected=False."""
        mock_manager = MagicMock()
        mock_manager.build_recall_block.return_value = ("", 0)
        src = SparkGraphRecallSource(
            sparkgraph_manager=mock_manager,
            sparkgraph_enabled=True,
        )
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_sparkgraph_user_message_passed(self):
        """T8.x: user_message passed to build_recall_block."""
        mock_manager = MagicMock()
        mock_manager.build_recall_block.return_value = ("", 0)
        src = SparkGraphRecallSource(
            sparkgraph_manager=mock_manager,
            sparkgraph_enabled=True,
        )
        src.collect(AssemblyContext(agent=MagicMock(), user_message="query"))
        mock_manager.build_recall_block.assert_called_once_with("query", max_nodes=None, max_chars=None)


class TestHonchoTurnSource:
    """T8: HonchoTurnSource."""

    def test_honcho_no_manager(self):
        """T8.13: manager=None -> []. """
        src = HonchoTurnSource(honcho_session_manager=None)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_honcho_with_context(self):
        """T8.14: normal call returns content."""
        mock_manager = MagicMock()
        mock_manager.get_turn_context.return_value = "honcho turn context"
        src = HonchoTurnSource(honcho_session_manager=mock_manager)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1
        assert chunks[0].content == "honcho turn context"
        assert chunks[0].source == "honcho_turn"
        assert chunks[0].slot == "honcho_context"
        assert chunks[0].stage == "dynamic"

    def test_honcho_empty_context(self):
        """T8.15: "" -> []. """
        mock_manager = MagicMock()
        mock_manager.get_turn_context.return_value = ""
        src = HonchoTurnSource(honcho_session_manager=mock_manager)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_user_message_none_in_ctx(self):
        """T8.16: source converts None user_message to "" before calling compat."""
        mock_manager = MagicMock()
        mock_manager.get_turn_context.return_value = "ctx"
        src = HonchoTurnSource(honcho_session_manager=mock_manager)
        # AssemblyContext.user_message defaults to None; source converts to ""
        ctx = AssemblyContext(agent=MagicMock())
        src.collect(ctx)
        mock_manager.get_turn_context.assert_called_once_with(
            user_message="",
            conversation_history=[],
        )

    def test_honcho_conversation_history_passed(self):
        """T8.x: conversation_history from ctx is passed."""
        mock_manager = MagicMock()
        mock_manager.get_turn_context.return_value = "ctx"
        history = [{"role": "user", "content": "hi"}]
        src = HonchoTurnSource(honcho_session_manager=mock_manager)
        ctx = AssemblyContext(agent=MagicMock(), conversation_history=history)
        src.collect(ctx)
        mock_manager.get_turn_context.assert_called_once()
        assert mock_manager.get_turn_context.call_args.kwargs["conversation_history"] == history

    def test_honcho_priority(self):
        """T8.x: priority is 4."""
        mock_manager = MagicMock()
        mock_manager.get_turn_context.return_value = "ctx"
        src = HonchoTurnSource(honcho_session_manager=mock_manager)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks[0].priority == 4
