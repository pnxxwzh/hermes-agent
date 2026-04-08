"""Tests for head-tail tool compaction."""

from agent.context_engine.tool_compaction import shape_tool_history, truncate_head_tail
from agent.context_engine.tool_groups import ToolCompactionConfig


class TestHeadTailToolCompaction:
    def test_truncate_head_tail_preserves_head_and_tail(self):
        text = "A" * 50 + "B" * 50 + "C" * 50

        result = truncate_head_tail(text, head_chars=20, tail_chars=10)

        assert result.startswith("A" * 20)
        assert result.endswith("C" * 10)
        assert "[truncated " in result

    def test_shape_tool_history_compacts_older_turn_tool_messages(self):
        messages = [
            {"role": "user", "content": "turn 1"},
            {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "A" * 4000},
            {"role": "user", "content": "turn 2"},
            {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_2"}]},
            {"role": "tool", "tool_call_id": "call_2", "content": "B" * 4000},
            {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_3"}]},
            {"role": "tool", "tool_call_id": "call_3", "content": "C" * 4000},
        ]
        config = ToolCompactionConfig(
            retain_recent_tool_groups_in_turn=2,
            current_turn_tool_budget_chars=9000,
            historical_tool_budget_chars=2000,
            warm_head_chars=100,
            warm_tail_chars=50,
            cold_head_chars=20,
            cold_tail_chars=10,
        )

        shaped = shape_tool_history(messages, config)

        assert shaped.message_heat_by_index[2] == "cold"
        assert shaped.message_heat_by_index[5] == "hot"
        assert shaped.message_heat_by_index[7] == "hot"
        assert len(shaped.shaped_messages[2]["content"]) < len(messages[2]["content"])
        assert shaped.shaped_messages[5]["content"] == messages[5]["content"]
