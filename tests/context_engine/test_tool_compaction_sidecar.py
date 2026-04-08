"""Tests for sidecar-only heat metadata."""

from unittest.mock import patch

from agent.context_engine import InputAssembly
from agent.context_engine.tool_compaction import shape_tool_history
from agent.context_engine.tool_groups import ToolCompactionConfig


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


class TestToolCompactionSidecar:
    def test_shape_tool_history_does_not_pollute_messages_with_heat_fields(self):
        shaped = shape_tool_history(
            [
                {"role": "user", "content": "turn 1"},
                {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_1"}]},
                {"role": "tool", "tool_call_id": "call_1", "content": "A" * 5000},
                {"role": "user", "content": "turn 2"},
                {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_2"}]},
                {"role": "tool", "tool_call_id": "call_2", "content": "B" * 5000},
            ],
            ToolCompactionConfig(
                retain_recent_tool_groups_in_turn=1,
                current_turn_tool_budget_chars=1000,
                historical_tool_budget_chars=1000,
            ),
        )

        assert shaped.message_heat_by_index
        assert all("_tool_heat" not in message for message in shaped.shaped_messages)
        assert all("heat" not in message for message in shaped.shaped_messages)

    def test_sanitize_api_messages_with_heat_keeps_sidecar_aligned(self):
        agent = _make_agent()
        messages = [
            {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "tool_call_id": "orphan", "content": "drop me"},
        ]

        sanitized, sidecar = agent._sanitize_api_messages_with_heat(messages, {1: "warm"})

        assert len(sanitized) == 2
        assert sanitized[1]["tool_call_id"] == "call_1"
        assert sidecar == {1: "hot"}

    def test_final_heat_alignment_defaults_injected_stub_to_hot(self):
        agent = _make_agent()
        final_messages = [
            {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_1"}]},
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "[Result unavailable — see context summary above]",
            },
        ]

        aligned = agent._align_tool_heat_to_final_messages(
            [{"role": "assistant", "content": "call", "tool_calls": [{"id": "call_1"}]}],
            {},
            final_messages,
        )

        assert aligned == {1: "hot"}
