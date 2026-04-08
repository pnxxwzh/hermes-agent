"""Integration tests for current-turn tool compaction on AIAgent."""

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


class TestRunAgentCurrentTurnToolBudget:
    def test_shape_messages_compacts_older_groups_in_current_turn(self):
        agent = _make_agent()
        agent._tool_compaction_section = {
            "enabled": True,
            "retain_recent_tool_groups_in_turn": 1,
            "current_turn_tool_budget_chars": 1000,
            "historical_tool_budget_chars": 1000,
            "warm_head_chars": 100,
            "warm_tail_chars": 20,
            "cold_head_chars": 20,
            "cold_tail_chars": 10,
        }
        messages = [
            {"role": "user", "content": "turn 1"},
            {"role": "assistant", "content": "call 1", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "A" * 5000},
            {"role": "assistant", "content": "call 2", "tool_calls": [{"id": "call_2"}]},
            {"role": "tool", "tool_call_id": "call_2", "content": "B" * 5000},
        ]

        shaped = agent._shape_messages_for_request(messages)

        assert shaped.message_heat_by_index[2] == "warm"
        assert shaped.message_heat_by_index[4] == "hot"
        assert len(shaped.shaped_messages[2]["content"]) < len(messages[2]["content"])
        assert shaped.shaped_messages[4]["content"] == messages[4]["content"]
