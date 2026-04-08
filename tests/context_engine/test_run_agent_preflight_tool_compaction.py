"""Integration tests for request-view tool compaction before preflight."""

from types import SimpleNamespace
from unittest.mock import patch


def _mock_response(content="done"):
    msg = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    return SimpleNamespace(choices=[choice], model="test/model", usage=None)


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


class TestRunAgentPreflightToolCompaction:
    def test_preflight_uses_shaped_messages_not_raw_messages(self):
        agent = _make_agent()
        agent.context_compressor.protect_first_n = 0
        agent.context_compressor.protect_last_n = 0
        agent.context_compressor.threshold_tokens = 999999
        conversation_history = [
            {"role": "user", "content": "turn 1"},
            {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "A" * 5000},
            {"role": "user", "content": "turn 2"},
            {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_2"}]},
            {"role": "tool", "tool_call_id": "call_2", "content": "B" * 5000},
        ]
        seen_lengths = []

        def _fake_estimate(messages, **kwargs):
            seen_lengths.append(sum(len(str(message)) for message in messages))
            return 1

        with (
            patch("run_agent.estimate_request_tokens_rough", side_effect=_fake_estimate),
            patch.object(agent, "_interruptible_api_call", return_value=_mock_response()),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
        ):
            agent.run_conversation("current", conversation_history=conversation_history)

        raw_length = sum(len(str(message)) for message in conversation_history + [{"role": "user", "content": "current"}])
        assert seen_lengths
        assert seen_lengths[0] < raw_length
