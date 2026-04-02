"""Tests for flush_memories() working correctly across all provider modes.

Catches the bug where Codex mode called chat.completions.create on a
Responses-only client, which would fail silently or with a 404.
"""

import json
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, call

import pytest

sys.modules.setdefault("fire", types.SimpleNamespace(Fire=lambda *a, **k: None))
sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
sys.modules.setdefault("fal_client", types.SimpleNamespace())

import run_agent


class _FakeOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.api_key = kwargs.get("api_key", "test")
        self.base_url = kwargs.get("base_url", "http://test")

    def close(self):
        pass


def _make_agent(monkeypatch, api_mode="chat_completions", provider="openrouter"):
    """Build an AIAgent with mocked internals, ready for flush_memories testing."""
    monkeypatch.setattr(run_agent, "get_tool_definitions", lambda **kw: [
        {
            "type": "function",
            "function": {
                "name": "memory",
                "description": "Manage memories.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "target": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
            },
        },
    ])
    monkeypatch.setattr(run_agent, "check_toolset_requirements", lambda: {})
    monkeypatch.setattr(run_agent, "OpenAI", _FakeOpenAI)

    agent = run_agent.AIAgent(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        provider=provider,
        api_mode=api_mode,
        max_iterations=4,
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )
    # Give it a valid memory store
    agent._memory_store = MagicMock()
    agent._memory_flush_min_turns = 1
    agent._user_turn_count = 5
    return agent


def _chat_response_with_memory_call():
    """Simulated chat completions response with a memory tool call."""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=None,
                tool_calls=[SimpleNamespace(
                    function=SimpleNamespace(
                        name="memory",
                        arguments=json.dumps({
                            "action": "add",
                            "target": "notes",
                            "content": "User prefers dark mode.",
                        }),
                    ),
                )],
            ),
        )],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120),
    )


def _chat_response_with_sparkgraph_call():
    """Simulated chat completions response with a sparkgraph_record tool call."""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=None,
                tool_calls=[SimpleNamespace(
                    function=SimpleNamespace(
                        name="sparkgraph_record",
                        arguments=json.dumps({
                            "items": [
                                {
                                    "summary": "socksio may be required for SOCKS proxy support",
                                    "type": "FACT",
                                    "evidence": "Check whether socksio is installed when SOCKS proxy errors appear.",
                                }
                            ]
                        }),
                    ),
                )],
            ),
        )],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120),
    )


def _chat_response_with_memory_and_sparkgraph_calls():
    """Simulated chat completions response with both flush tool calls."""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        function=SimpleNamespace(
                            name="memory",
                            arguments=json.dumps({
                                "action": "add",
                                "target": "notes",
                                "content": "User prefers dark mode.",
                            }),
                        ),
                    ),
                    SimpleNamespace(
                        function=SimpleNamespace(
                            name="sparkgraph_record",
                            arguments=json.dumps({
                                "items": [
                                    {
                                        "summary": "socksio may be required for SOCKS proxy support",
                                        "type": "FACT",
                                        "evidence": "Check whether socksio is installed when SOCKS proxy errors appear.",
                                    }
                                ]
                            }),
                        ),
                    ),
                ],
            ),
        )],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30, total_tokens=150),
    )


class TestFlushMemoriesUsesAuxiliaryClient:
    """When an auxiliary client is available, flush_memories should use it
    instead of self.client -- especially critical in Codex mode."""

    def test_flush_uses_auxiliary_when_available(self, monkeypatch):
        agent = _make_agent(monkeypatch, api_mode="codex_responses", provider="openai-codex")
        agent._sparkgraph_enabled = True
        agent._sparkgraph_store = MagicMock()

        mock_response = _chat_response_with_memory_call()

        with patch("agent.auxiliary_client.call_llm", return_value=mock_response) as mock_call:
            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
                {"role": "user", "content": "Remember this"},
            ]
            with patch("tools.memory_tool.memory_tool", return_value="Saved.") as mock_memory:
                agent.flush_memories(messages)

        mock_call.assert_called_once()
        call_kwargs = mock_call.call_args
        assert call_kwargs.kwargs.get("task") == "flush_memories"
        tool_names = [t["function"]["name"] for t in call_kwargs.kwargs.get("tools", [])]
        assert tool_names == ["memory", "sparkgraph_record"]

    def test_flush_uses_main_client_when_no_auxiliary(self, monkeypatch):
        """Non-Codex mode with no auxiliary falls back to self.client."""
        agent = _make_agent(monkeypatch, api_mode="chat_completions", provider="openrouter")
        agent.client = MagicMock()
        agent.client.chat.completions.create.return_value = _chat_response_with_memory_call()

        with patch("agent.auxiliary_client.call_llm", side_effect=RuntimeError("no provider")):
            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
                {"role": "user", "content": "Save this"},
            ]
            with patch("tools.memory_tool.memory_tool", return_value="Saved."):
                agent.flush_memories(messages)

        agent.client.chat.completions.create.assert_called_once()

    def test_flush_executes_memory_tool_calls(self, monkeypatch):
        """Verify that memory tool calls from the flush response actually get executed."""
        agent = _make_agent(monkeypatch, api_mode="chat_completions", provider="openrouter")
        agent._sparkgraph_enabled = True
        agent._sparkgraph_store = MagicMock()

        mock_response = _chat_response_with_memory_call()

        with patch("agent.auxiliary_client.call_llm", return_value=mock_response):
            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Note this"},
            ]
            with patch("tools.memory_tool.memory_tool", return_value="Saved.") as mock_memory:
                agent.flush_memories(messages)

        mock_memory.assert_called_once()
        call_kwargs = mock_memory.call_args
        assert call_kwargs.kwargs["action"] == "add"
        assert call_kwargs.kwargs["target"] == "notes"
        assert "dark mode" in call_kwargs.kwargs["content"]

    def test_flush_executes_sparkgraph_tool_calls(self, monkeypatch):
        """Verify that sparkgraph tool calls from the flush response get executed."""
        agent = _make_agent(monkeypatch, api_mode="chat_completions", provider="openrouter")
        agent._sparkgraph_enabled = True
        agent._sparkgraph_store = MagicMock()

        mock_response = _chat_response_with_sparkgraph_call()

        with patch("agent.auxiliary_client.call_llm", return_value=mock_response):
            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Remember the socksio fix"},
            ]
            with patch("tools.sparkgraph_tool.sparkgraph_record_tool", return_value=json.dumps({"success": True})) as mock_record:
                agent.flush_memories(messages)

        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["session_id"] == agent.session_id
        assert call_kwargs["turn_index"] == agent._user_turn_count
        assert call_kwargs["source_kind"] == "flush"
        assert call_kwargs["store"] is agent._sparkgraph_store

    def test_flush_executes_memory_and_sparkgraph_together(self, monkeypatch):
        """Memory and SparkGraph should coexist in the same flush pass."""
        agent = _make_agent(monkeypatch, api_mode="chat_completions", provider="openrouter")
        agent._sparkgraph_enabled = True
        agent._sparkgraph_store = MagicMock()

        mock_response = _chat_response_with_memory_and_sparkgraph_calls()

        with patch("agent.auxiliary_client.call_llm", return_value=mock_response):
            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Remember the socksio fix and my preference"},
            ]
            with (
                patch("tools.memory_tool.memory_tool", return_value="Saved.") as mock_memory,
                patch("tools.sparkgraph_tool.sparkgraph_record_tool", return_value=json.dumps({"success": True})) as mock_record,
            ):
                agent.flush_memories(messages)

        mock_memory.assert_called_once()
        mock_record.assert_called_once()

    def test_flush_runs_sparkgraph_maintenance_after_graph_write(self, monkeypatch):
        """A successful SparkGraph write should trigger flush-aligned maintenance."""
        agent = _make_agent(monkeypatch, api_mode="chat_completions", provider="openrouter")
        agent._sparkgraph_enabled = True
        agent._sparkgraph_store = MagicMock()

        mock_response = _chat_response_with_sparkgraph_call()

        with patch("agent.auxiliary_client.call_llm", return_value=mock_response):
            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Remember the socksio fix"},
            ]
            with (
                patch("tools.sparkgraph_tool.sparkgraph_record_tool", return_value=json.dumps({"success": True})) as mock_record,
                patch("agent.sparkgraph.maintenance.run_flush_maintenance", return_value={"scanned": 1, "deprecated": 0}) as mock_maintenance,
            ):
                agent.flush_memories(messages)

        mock_record.assert_called_once()
        mock_maintenance.assert_called_once()
        assert mock_maintenance.call_args.args == (agent._sparkgraph_store,)
        assert "embedding_config" in mock_maintenance.call_args.kwargs

    def test_flush_strips_artifacts_from_messages(self, monkeypatch):
        """After flush, the flush prompt and any response should be removed from messages."""
        agent = _make_agent(monkeypatch, api_mode="chat_completions", provider="openrouter")

        mock_response = _chat_response_with_memory_call()

        with patch("agent.auxiliary_client.call_llm", return_value=mock_response):
            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Remember X"},
            ]
            original_len = len(messages)
            with patch("tools.memory_tool.memory_tool", return_value="Saved."):
                agent.flush_memories(messages)

        # Messages should not grow from the flush
        assert len(messages) <= original_len
        # No flush sentinel should remain
        for msg in messages:
            assert "_flush_sentinel" not in msg

    def test_flush_can_run_with_sparkgraph_only(self, monkeypatch):
        """SparkGraph flush should still run when local memory is unavailable."""
        agent = _make_agent(monkeypatch, api_mode="chat_completions", provider="openrouter")
        agent._memory_store = None
        agent._sparkgraph_enabled = True
        agent._sparkgraph_store = MagicMock()

        mock_response = _chat_response_with_sparkgraph_call()

        with patch("agent.auxiliary_client.call_llm", return_value=mock_response) as mock_call:
            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Remember the socksio fix"},
            ]
            with patch("tools.sparkgraph_tool.sparkgraph_record_tool", return_value=json.dumps({"success": True})) as mock_record:
                agent.flush_memories(messages)

        mock_call.assert_called_once()
        tool_names = [t["function"]["name"] for t in mock_call.call_args.kwargs.get("tools", [])]
        assert tool_names == ["sparkgraph_record"]
        mock_record.assert_called_once()


class TestFlushMemoriesCodexFallback:
    """When no auxiliary client exists and we're in Codex mode, flush should
    use the Codex Responses API path instead of chat.completions."""

    def test_codex_mode_no_aux_uses_responses_api(self, monkeypatch):
        agent = _make_agent(monkeypatch, api_mode="codex_responses", provider="openai-codex")

        codex_response = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call_1",
                    name="memory",
                    arguments=json.dumps({
                        "action": "add",
                        "target": "notes",
                        "content": "Codex flush test",
                    }),
                ),
            ],
            usage=SimpleNamespace(input_tokens=50, output_tokens=10, total_tokens=60),
            status="completed",
            model="gpt-5-codex",
        )

        with patch("agent.auxiliary_client.call_llm", side_effect=RuntimeError("no provider")), \
             patch.object(agent, "_run_codex_stream", return_value=codex_response) as mock_stream, \
             patch.object(agent, "_build_api_kwargs") as mock_build, \
             patch("tools.memory_tool.memory_tool", return_value="Saved.") as mock_memory:
            mock_build.return_value = {
                "model": "gpt-5-codex",
                "instructions": "test",
                "input": [],
                "tools": [],
                "max_output_tokens": 4096,
            }
            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Save this"},
            ]
            agent.flush_memories(messages)

        mock_stream.assert_called_once()
        mock_memory.assert_called_once()
        assert mock_memory.call_args.kwargs["content"] == "Codex flush test"

    def test_codex_mode_can_execute_sparkgraph_tool_calls(self, monkeypatch):
        """Codex flush fallback should execute sparkgraph tool calls too."""
        agent = _make_agent(monkeypatch, api_mode="codex_responses", provider="openai-codex")
        agent._sparkgraph_enabled = True
        agent._sparkgraph_store = MagicMock()

        codex_response = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call_1",
                    name="sparkgraph_record",
                    arguments=json.dumps({
                        "items": [
                            {
                                "summary": "socksio may be required for SOCKS proxy support",
                                "type": "FACT",
                                "evidence": "Check whether socksio is installed when SOCKS proxy errors appear.",
                            }
                        ]
                    }),
                ),
            ],
            usage=SimpleNamespace(input_tokens=50, output_tokens=10, total_tokens=60),
            status="completed",
            model="gpt-5-codex",
        )

        with (
            patch("agent.auxiliary_client.call_llm", side_effect=RuntimeError("no provider")),
            patch.object(agent, "_run_codex_stream", return_value=codex_response) as mock_stream,
            patch.object(agent, "_build_api_kwargs") as mock_build,
            patch("tools.sparkgraph_tool.sparkgraph_record_tool", return_value=json.dumps({"success": True})) as mock_record,
        ):
            mock_build.return_value = {
                "model": "gpt-5-codex",
                "instructions": "test",
                "input": [],
                "tools": [],
                "max_output_tokens": 4096,
            }
            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Remember the socksio fix"},
            ]
            agent.flush_memories(messages)

        mock_stream.assert_called_once()
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["store"] is agent._sparkgraph_store
        assert call_kwargs["source_kind"] == "flush"
