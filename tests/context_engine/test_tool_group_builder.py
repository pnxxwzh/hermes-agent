"""Tests for tool-group extraction."""

from agent.context_engine.tool_groups import build_tool_groups


class TestToolGroupBuilder:
    def test_build_tool_groups_groups_contiguous_tool_results(self):
        messages = [
            {"role": "user", "content": "find it"},
            {"role": "assistant", "content": "working", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result 1"},
            {"role": "assistant", "content": "done"},
        ]

        groups = build_tool_groups(messages)

        assert len(groups) == 1
        assert groups[0].turn_index == 1
        assert groups[0].assistant_index == 1
        assert groups[0].tool_start_index == 2
        assert groups[0].tool_end_index == 2
        assert groups[0].char_count == len("result 1")

    def test_build_tool_groups_keeps_multi_result_assistant_together(self):
        messages = [
            {"role": "user", "content": "run tools"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "call_1"}, {"id": "call_2"}],
                "content": "running",
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "alpha"},
            {"role": "tool", "tool_call_id": "call_2", "content": "beta"},
        ]

        groups = build_tool_groups(messages)

        assert len(groups) == 1
        assert len(groups[0].tool_messages) == 2
        assert groups[0].char_count == len("alpha") + len("beta")

    def test_build_tool_groups_keeps_missing_result_group(self):
        messages = [
            {"role": "user", "content": "run tools"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "call_1"}],
                "content": "running",
            },
            {"role": "assistant", "content": "fallback"},
        ]

        groups = build_tool_groups(messages)

        assert len(groups) == 1
        assert groups[0].tool_messages == []
        assert groups[0].tool_start_index == 2
        assert groups[0].tool_end_index == 1
