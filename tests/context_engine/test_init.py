"""Tests for context_engine __init__.py exports."""

import pytest
from unittest.mock import MagicMock

import agent.context_engine as ce


class TestExports:
    """T10: __init__.py exports."""

    def test_all_exports(self):
        """T10.1: __all__ contains all expected symbols."""
        expected = {
            "ContextChunk",
            "ContextMetrics",
            "SourceMetrics",
            "AssemblyResult",
            "InputNode",
            "InputAssembly",
            "AssemblySnapshots",
            "RequestBucketMetrics",
            "RequestMetrics",
            "TransportAdapter",
            "TransportPayload",
            "ChatCompletionsTransportAdapter",
            "CodexResponsesTransportAdapter",
            "AnthropicMessagesTransportAdapter",
            "AssemblyContext",
            "ConversationMessagesSource",
            "PrefillMessagesSource",
            "ToolSchemasSource",
            "ToolGroup",
            "ToolCompactionConfig",
            "ShapedToolHistory",
            "build_tool_groups",
            "apply_tool_heat_budget",
            "truncate_head_tail",
            "compact_tool_group",
            "shape_tool_history",
            "build_request_metrics",
            "rough_tokens_from_message",
            "rough_tokens_from_text",
            "get_transport_adapter",
            "STABLE_SOURCE_FACTORIES",
            "DYNAMIC_SOURCE_FACTORIES",
            "register_stable",
            "register_dynamic",
            "ContextAssembler",
            "ASSEMBLER",
            "get_assembler",
        }
        assert set(ce.__all__) == expected

    def test_no_circular_import(self):
        """T10.2: import agent.context_engine does not raise."""
        # Re-import to verify no circular import issues
        import importlib
        import agent.context_engine
        importlib.reload(agent.context_engine)

    def test_get_assembler_lazy(self):
        """T10.3: ASSEMBLER is created on first get_assembler call."""
        # Reset global
        original = ce.ASSEMBLER
        ce.ASSEMBLER = None
        try:
            mock_agent = MagicMock()
            assert ce.ASSEMBLER is None
            assembler = ce.get_assembler(mock_agent)
            assert ce.ASSEMBLER is not None
            assert assembler is ce.ASSEMBLER
        finally:
            ce.ASSEMBLER = original

    def test_get_assembler_returns_context_assembler(self):
        """T10.4: get_assembler returns a ContextAssembler."""
        mock_agent = MagicMock()
        # Reset global
        original = ce.ASSEMBLER
        ce.ASSEMBLER = None
        try:
            assembler = ce.get_assembler(mock_agent)
            assert isinstance(assembler, ce.ContextAssembler)
            assert assembler._agent is mock_agent
        finally:
            ce.ASSEMBLER = original

    def test_get_assembler_rebinds_when_agent_changes(self):
        """T10.x: a different agent gets a different bound assembler."""
        original = ce.ASSEMBLER
        ce.ASSEMBLER = None
        try:
            agent_a = MagicMock()
            agent_b = MagicMock()
            assembler_a = ce.get_assembler(agent_a)
            assembler_b = ce.get_assembler(agent_b)
            assert assembler_a is not assembler_b
            assert assembler_b._agent is agent_b
        finally:
            ce.ASSEMBLER = original
