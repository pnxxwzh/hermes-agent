"""Tests for context_engine.compat memory_store and honcho wrappers."""

import pytest
from unittest.mock import MagicMock, patch

from agent.context_engine.compat import (
    wrap_memory_store_format,
    wrap_honcho_static_block,
    wrap_honcho_get_turn_context,
)


class TestWrapMemoryStoreFormat:
    """T5b: wrap_memory_store_format."""

    def test_wrap_memory_store_format_none(self):
        """T5b.1: memory_store=None returns ("", None), not an error."""
        content, err = wrap_memory_store_format(None, "memory")
        assert content == ""
        assert err is None

    def test_wrap_memory_store_format_exception_caught(self):
        """T5b.2: exception caught, returns error string."""
        mock_store = MagicMock()
        mock_store.format_for_system_prompt.side_effect = OSError("disk error")
        content, err = wrap_memory_store_format(mock_store, "memory")
        assert content == ""
        assert err == "disk error"

    def test_wrap_memory_store_format_user_type(self):
        """T5b.x: memory_type="user" passed through."""
        mock_store = MagicMock()
        mock_store.format_for_system_prompt.return_value = "# User profile"
        content, err = wrap_memory_store_format(mock_store, "user")
        assert content == "# User profile"
        mock_store.format_for_system_prompt.assert_called_once_with("user")

    def test_wrap_memory_store_format_normal(self):
        """T5b.x: normal call returns content."""
        mock_store = MagicMock()
        mock_store.format_for_system_prompt.return_value = "# Memory entries"
        content, err = wrap_memory_store_format(mock_store, "memory")
        assert content == "# Memory entries"
        assert err is None

    def test_wrap_memory_store_format_returns_none(self):
        """T5b.x: store returns None -> "". """
        mock_store = MagicMock()
        mock_store.format_for_system_prompt.return_value = None
        content, err = wrap_memory_store_format(mock_store, "memory")
        assert content == ""
        assert err is None


class TestWrapHonchoStaticBlock:
    """T5b: wrap_honcho_static_block."""

    def test_wrap_honcho_static_block_no_manager(self):
        """T5b.3: manager=None returns ("", None)."""
        content, err = wrap_honcho_static_block(None, MagicMock(), None)
        assert content == ""
        assert err is None

    def test_wrap_honcho_static_block_no_config(self):
        """T5b.x: config=None returns ("", None)."""
        content, err = wrap_honcho_static_block(MagicMock(), None, None)
        assert content == ""
        assert err is None

    def test_wrap_honcho_static_block_hybrid_mode(self):
        """T5b.4: hybrid mode includes honcho tools."""
        mock_config = MagicMock()
        mock_config.memory_mode = "hybrid"
        mock_config.write_frequency = "async"
        mock_config.recall_mode = "hybrid"
        content, err = wrap_honcho_static_block(MagicMock(), mock_config, None)
        assert "Honcho tools:" in content
        assert err is None

    def test_wrap_honcho_static_block_context_mode(self):
        """T5b.5: context mode includes inject说明."""
        mock_config = MagicMock()
        mock_config.memory_mode = "hybrid"
        mock_config.write_frequency = "async"
        mock_config.recall_mode = "context"
        content, err = wrap_honcho_static_block(MagicMock(), mock_config, None)
        assert "injected into this system prompt" in content
        assert err is None

    def test_wrap_honcho_static_block_ai_peer_name(self):
        """T5b.x: ai_peer_name substitutes in identity."""
        mock_config = MagicMock()
        mock_config.memory_mode = "local"
        mock_config.write_frequency = "sync"
        mock_config.recall_mode = "tools"
        content, err = wrap_honcho_static_block(MagicMock(), mock_config, "MyAgent")
        assert "You are MyAgent" in content
        assert err is None

    def test_wrap_honcho_static_block_ai_peer_hermes_ignored(self):
        """T5b.x: ai_peer_name="hermes" uses default identity."""
        mock_config = MagicMock()
        mock_config.memory_mode = "local"
        mock_config.write_frequency = "sync"
        mock_config.recall_mode = "tools"
        content, err = wrap_honcho_static_block(MagicMock(), mock_config, "hermes")
        assert "You are Hermes Agent" in content
        assert err is None

    def test_wrap_honcho_static_block_exception_caught(self):
        """T5b.x: exception caught during block construction."""
        class BadConfig:
            @property
            def memory_mode(self):
                raise RuntimeError("honcho config error")

        mock_manager = MagicMock()
        content, err = wrap_honcho_static_block(mock_manager, BadConfig(), None)
        assert content == ""
        assert err == "honcho config error"


class TestWrapHonchoGetTurnContext:
    """T5b: wrap_honcho_get_turn_context."""

    def test_wrap_honcho_get_turn_context_none(self):
        """T5b.6: manager=None returns ("", None)."""
        content, err = wrap_honcho_get_turn_context(None, "hello", [])
        assert content == ""
        assert err is None

    def test_wrap_honcho_get_turn_context_exception_caught(self):
        """T5b.7: exception caught, returns error string."""
        mock_manager = MagicMock()
        mock_manager.get_turn_context.side_effect = RuntimeError("honcho down")
        content, err = wrap_honcho_get_turn_context(mock_manager, "hello", [])
        assert content == ""
        assert err == "honcho down"

    def test_wrap_honcho_get_turn_context_string_result(self):
        """T5b.x: manager returns string -> content."""
        mock_manager = MagicMock()
        mock_manager.get_turn_context.return_value = "honcho context block"
        content, err = wrap_honcho_get_turn_context(mock_manager, "hello", [])
        assert content == "honcho context block"
        assert err is None

    def test_wrap_honcho_get_turn_context_none_result(self):
        """T5b.x: manager returns None -> "". """
        mock_manager = MagicMock()
        mock_manager.get_turn_context.return_value = None
        content, err = wrap_honcho_get_turn_context(mock_manager, "hello", [])
        assert content == ""
        assert err is None

    def test_wrap_honcho_get_turn_context_passed_args(self):
        """T5b.x: user_message and conversation_history passed through."""
        mock_manager = MagicMock()
        mock_manager.get_turn_context.return_value = ""
        wrap_honcho_get_turn_context(mock_manager, "query", [{"role": "user"}])
        mock_manager.get_turn_context.assert_called_once_with(
            user_message="query",
            conversation_history=[{"role": "user"}],
        )
