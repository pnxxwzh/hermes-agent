"""Tests for unified-input payload comparison helpers."""

from types import SimpleNamespace
from unittest.mock import patch


def _make_agent():
    from run_agent import AIAgent

    with (
        patch("run_agent.OpenAI"),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
    ):
        return AIAgent(
            api_key="test-key",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )


class TestRequestEquivalence:
    def test_compare_returns_none_when_payloads_match(self):
        agent = _make_agent()
        payload = SimpleNamespace(
            api_messages=[{"role": "user", "content": "hello"}],
            api_kwargs={"messages": [{"role": "user", "content": "hello"}]},
        )

        diff = agent._compare_engine_and_legacy_payload(
            legacy_api_messages=[{"role": "user", "content": "hello"}],
            legacy_api_kwargs={"messages": [{"role": "user", "content": "hello"}]},
            engine_payload=payload,
        )

        assert diff is None

    def test_compare_returns_precise_difference_path(self):
        agent = _make_agent()
        payload = SimpleNamespace(
            api_messages=[{"role": "user", "content": "hello"}],
            api_kwargs={"messages": [{"role": "user", "content": "hello"}]},
        )

        diff = agent._compare_engine_and_legacy_payload(
            legacy_api_messages=[{"role": "user", "content": "hi"}],
            legacy_api_kwargs={"messages": [{"role": "user", "content": "hello"}]},
            engine_payload=payload,
        )

        assert diff == "api_messages[0].content: 'hi' != 'hello'"
