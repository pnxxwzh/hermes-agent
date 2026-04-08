"""Tests for context_engine.compat sparkgraph and plugin wrappers."""

import pytest
from unittest.mock import MagicMock, patch

from agent.context_engine.compat import (
    wrap_sparkgraph_build_recall,
    wrap_invoke_pre_llm_call,
)


class TestWrapSparkgraphBuildRecall:
    """T5c: wrap_sparkgraph_build_recall."""

    def test_wrap_sparkgraph_disabled(self):
        """T5c.1: enabled=False returns ("", None)."""
        content, err = wrap_sparkgraph_build_recall(
            MagicMock(), False, "query"
        )
        assert content == ""
        assert err is None

    def test_wrap_sparkgraph_manager_none(self):
        """T5c.2: manager=None returns ("", None)."""
        content, err = wrap_sparkgraph_build_recall(
            None, True, "query"
        )
        assert content == ""
        assert err is None

    def test_wrap_sparkgraph_exception_caught(self):
        """T5c.3: exception caught, returns error string."""
        mock_manager = MagicMock()
        mock_manager.build_recall_block.side_effect = RuntimeError("SG error")
        content, err = wrap_sparkgraph_build_recall(
            mock_manager, True, "query"
        )
        assert content == ""
        assert err == "SG error"

    def test_wrap_sparkgraph_returns_string(self):
        """T5c.4: normal call returns block string."""
        mock_manager = MagicMock()
        mock_manager.build_recall_block.return_value = ("[SparkGraph Recall]", 0)
        content, err = wrap_sparkgraph_build_recall(
            mock_manager, True, "query"
        )
        assert content == "[SparkGraph Recall]"
        assert err is None

    def test_wrap_sparkgraph_returns_empty_tuple(self):
        """T5c.x: empty tuple (block="", token=0) -> "". """
        mock_manager = MagicMock()
        mock_manager.build_recall_block.return_value = ("", 0)
        content, err = wrap_sparkgraph_build_recall(
            mock_manager, True, "query"
        )
        assert content == ""
        assert err is None

    def test_wrap_sparkgraph_passed_args(self):
        """T5c.x: user_message, max_nodes, max_chars passed through."""
        mock_manager = MagicMock()
        mock_manager.build_recall_block.return_value = ("", 0)
        wrap_sparkgraph_build_recall(
            mock_manager, True, "my query",
            session_id="sess-1", max_nodes=5, max_chars=1000,
        )
        mock_manager.build_recall_block.assert_called_once_with(
            "my query", session_id="sess-1", max_nodes=5, max_chars=1000,
        )


class TestWrapInvokePreLlmCall:
    """T5c: wrap_invoke_pre_llm_call."""

    def test_wrap_invoke_pre_llm_call_no_plugins(self):
        """T5c.5: no plugins returns ("", None)."""
        with patch("hermes_cli.plugins.invoke_hook", return_value=None):
            content, err = wrap_invoke_pre_llm_call("sess1", "hello", [], True)
            assert content == ""
            assert err is None

    def test_wrap_invoke_pre_llm_call_empty_results(self):
        """T5c.x: empty list returns ("", None)."""
        with patch("hermes_cli.plugins.invoke_hook", return_value=[]):
            content, err = wrap_invoke_pre_llm_call("sess1", "hello", [], True)
            assert content == ""
            assert err is None

    def test_wrap_invoke_pre_llm_call_dict_context(self):
        """T5c.6: {"context": "x"} adds to parts."""
        with patch(
            "hermes_cli.plugins.invoke_hook",
            return_value=[{"context": "plugin context"}],
        ):
            content, err = wrap_invoke_pre_llm_call("sess1", "hello", [], False)
            assert content == "plugin context"
            assert err is None

    def test_wrap_invoke_pre_llm_call_string_result(self):
        """T5c.x: string result added to parts."""
        with patch(
            "hermes_cli.plugins.invoke_hook",
            return_value=["plain string result"],
        ):
            content, err = wrap_invoke_pre_llm_call("sess1", "hello", [], False)
            assert content == "plain string result"
            assert err is None

    def test_wrap_invoke_pre_llm_call_empty_context_filtered(self):
        """T5c.7: empty context dict is filtered out (strict None/empty check).

        {"context": ""} is falsy, filtered out.
        {"context": "  "} is non-empty string, NOT filtered (whitespace is content).
        """
        with patch(
            "hermes_cli.plugins.invoke_hook",
            return_value=[{"context": ""}, {"context": "  "}],
        ):
            content, err = wrap_invoke_pre_llm_call("sess1", "hello", [], False)
            # "  " is non-empty, so it passes the check and gets added
            assert content == "  "
            assert err is None

    def test_wrap_invoke_pre_llm_call_multiple_parts_joined(self):
        """T5c.x: multiple parts joined with double newlines."""
        with patch(
            "hermes_cli.plugins.invoke_hook",
            return_value=[
                {"context": "part one"},
                {"context": "part two"},
            ],
        ):
            content, err = wrap_invoke_pre_llm_call("sess1", "hello", [], False)
            assert content == "part one\n\npart two"
            assert err is None

    def test_wrap_invoke_pre_llm_call_exception_caught(self):
        """T5c.8: exception caught, returns error string."""
        with patch(
            "hermes_cli.plugins.invoke_hook",
            side_effect=OSError("plugin system error"),
        ):
            content, err = wrap_invoke_pre_llm_call("sess1", "hello", [], False)
            assert content == ""
            assert err == "plugin system error"

    def test_wrap_invoke_pre_llm_call_passed_args(self):
        """T5c.x: session_id, user_message, conversation_history, is_first_turn passed."""
        history = [{"role": "user"}]
        with patch(
            "hermes_cli.plugins.invoke_hook",
            return_value=[],
        ) as mock_hook:
            wrap_invoke_pre_llm_call(
                "sess2",
                "query",
                history,
                True,
                model="gpt-test",
                platform="discord",
            )
            mock_hook.assert_called_once_with(
                "pre_llm_call",
                session_id="sess2",
                user_message="query",
                conversation_history=history,
                is_first_turn=True,
                model="gpt-test",
                platform="discord",
            )

    def test_wrap_invoke_pre_llm_call_conversation_history_copy(self):
        """T5c.x: conversation_history passed as list() copy, not modified."""
        original = [{"role": "user", "content": "hi"}]
        with patch(
            "hermes_cli.plugins.invoke_hook",
            return_value=[],
        ) as mock_hook:
            wrap_invoke_pre_llm_call("sess1", "query", original, False)
            # Verify a copy was passed (original unchanged)
            call_args = mock_hook.call_args
            passed_history = call_args.kwargs["conversation_history"]
            assert passed_history == original
            assert passed_history is not original
