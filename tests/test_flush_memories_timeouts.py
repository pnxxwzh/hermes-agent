"""Regression tests for flush_memories timeout resolution."""

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import run_agent


sys.modules.setdefault("fire", types.SimpleNamespace(Fire=lambda *a, **k: None))
sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
sys.modules.setdefault("fal_client", types.SimpleNamespace())


class _FakeOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.api_key = kwargs.get("api_key", "test")
        self.base_url = kwargs.get("base_url", "http://test")

    def close(self):
        pass


def _make_agent(monkeypatch, api_mode="chat_completions", provider="openrouter"):
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
    agent._memory_store = MagicMock()
    agent._memory_flush_min_turns = 1
    agent._user_turn_count = 5
    return agent


def _chat_response_with_memory_call():
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
                            "content": "Configured timeout path",
                        }),
                    ),
                )],
            ),
        )],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120),
    )


def test_flush_memories_auxiliary_uses_configured_timeout(monkeypatch):
    agent = _make_agent(monkeypatch)
    mock_response = _chat_response_with_memory_call()

    with (
        patch("agent.auxiliary_client.call_llm", return_value=mock_response) as mock_call,
        patch("agent.auxiliary_client._get_task_timeout", return_value=91.5) as mock_get_timeout,
        patch("tools.memory_tool.memory_tool", return_value="Saved."),
    ):
        agent.flush_memories(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Remember this"},
            ]
        )

    mock_get_timeout.assert_called_once_with("flush_memories")
    assert mock_call.call_args.kwargs["timeout"] == 91.5


def test_flush_memories_direct_fallback_uses_configured_timeout(monkeypatch):
    agent = _make_agent(monkeypatch, api_mode="chat_completions", provider="openrouter")
    agent.client = MagicMock()
    agent.client.chat.completions.create.return_value = _chat_response_with_memory_call()

    with (
        patch("agent.auxiliary_client.call_llm", side_effect=RuntimeError("no provider")),
        patch("agent.auxiliary_client._get_task_timeout", return_value=47.25) as mock_get_timeout,
        patch("tools.memory_tool.memory_tool", return_value="Saved."),
    ):
        agent.flush_memories(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Remember this"},
            ]
        )

    mock_get_timeout.assert_called_once_with("flush_memories")
    assert agent.client.chat.completions.create.call_args.kwargs["timeout"] == 47.25
