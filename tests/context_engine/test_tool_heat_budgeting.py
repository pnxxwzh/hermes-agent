"""Tests for tool-group heat assignment."""

from agent.context_engine.tool_groups import ToolCompactionConfig, ToolGroup, apply_tool_heat_budget


def _group(turn_index, assistant_index, char_count):
    return ToolGroup(
        turn_index=turn_index,
        assistant_index=assistant_index,
        tool_start_index=assistant_index + 1,
        tool_end_index=assistant_index + 1,
        assistant_message={"role": "assistant", "tool_calls": [{"id": f"call_{assistant_index}"}]},
        tool_messages=[{"role": "tool", "tool_call_id": f"call_{assistant_index}", "content": "x" * char_count}],
        char_count=char_count,
    )


class TestToolHeatBudgeting:
    def test_current_turn_budget_demotes_older_groups(self):
        groups = [
            _group(1, 1, 1000),
            _group(2, 3, 5000),
            _group(2, 5, 5000),
            _group(2, 7, 5000),
        ]
        config = ToolCompactionConfig(
            retain_recent_tool_groups_in_turn=2,
            current_turn_tool_budget_chars=9000,
        )

        updated = apply_tool_heat_budget(groups, config)

        assert [group.heat for group in updated] == ["warm", "warm", "hot", "hot"]

    def test_historical_budget_demotes_old_history_to_cold(self):
        groups = [
            _group(1, 1, 8000),
            _group(2, 3, 8000),
            _group(3, 5, 4000),
            _group(4, 7, 4000),
        ]
        config = ToolCompactionConfig(
            retain_recent_user_turns=2,
            historical_tool_budget_chars=7000,
        )

        updated = apply_tool_heat_budget(groups, config)

        assert updated[0].heat == "cold"
        assert updated[1].heat == "cold"
        assert updated[2].heat == "warm"
        assert updated[3].heat == "hot"
