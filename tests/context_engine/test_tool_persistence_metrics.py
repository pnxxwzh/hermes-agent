from unittest.mock import patch

from agent.context_engine.request_metrics import build_request_metrics
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


def test_build_request_metrics_buckets_persisted_tool_messages_separately():
    messages = [
        {"role": "tool", "tool_call_id": "call_1", "content": "<persisted-output>\npreview\n</persisted-output>"},
        {"role": "tool", "tool_call_id": "call_2", "content": "normal"},
    ]
    metrics = build_request_metrics(
        messages=messages,
        message_heat_by_index={0: "warm", 1: "hot"},
        message_persistence_by_index={0: "persisted_preview", 1: "inline"},
    )
    assert metrics.get_bucket("messages_tool_persisted").char_count == len(str(messages[0]))
    assert metrics.get_bucket("messages_tool_hot").char_count == len(str(messages[1]))


def test_shape_tool_history_marks_persisted_preview_sidecar():
    shaped = shape_tool_history(
        [
            {"role": "user", "content": "turn 1"},
            {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_1"}]},
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "<persisted-output>\npreview\n</persisted-output>",
            },
        ],
        ToolCompactionConfig(),
    )
    assert shaped.message_persistence_by_index == {2: "persisted_preview"}


def test_final_persistence_alignment_defaults_missing_stubs_to_inline():
    agent = _make_agent()
    final_messages = [
        {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_1"}]},
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "[Result unavailable — see context summary above]",
        },
    ]

    aligned = agent._align_tool_persistence_to_final_messages(
        [{"role": "assistant", "content": "call", "tool_calls": [{"id": "call_1"}]}],
        {},
        final_messages,
    )

    assert aligned == {1: "inline"}
