"""Integration-style SparkGraph flush flow tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import run_agent
from tools.sparkgraph_tool import sparkgraph_record_tool, sparkgraph_search_tool, sparkgraph_stats_tool


def _make_tool_defs(*names: str) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": f"{n} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for n in names
    ]


def _chat_response_with_sparkgraph_call():
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="sparkgraph_record",
                                arguments=(
                                    '{"items":[{"summary":"socksio may be required for SOCKS proxy support",'
                                    '"type":"FACT",'
                                    '"evidence":"Check whether socksio is installed when SOCKS proxy errors appear."}]}'
                                ),
                            )
                        )
                    ],
                )
            )
        ]
    )


def _chat_response_with_preference_call():
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="sparkgraph_record",
                                arguments=(
                                    '{"items":[{"summary":"User prefers concise replies",'
                                    '"type":"PREFERENCE",'
                                    '"evidence":"Please keep replies concise going forward."}]}'
                                ),
                            )
                        )
                    ],
                )
            )
        ]
    )


def _chat_response_without_tool_calls():
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="Nothing to save",
                    tool_calls=None,
                )
            )
        ]
    )


def _mock_response(content="Final answer", finish_reason="stop"):
    msg = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], model="test/model", usage=None)


@pytest.fixture()
def sparkgraph_agent():
    def _registry_defs(tool_names, quiet=False):
        names = set(tool_names or [])
        if "sparkgraph_record" not in names:
            return []
        return _make_tool_defs("sparkgraph_record")

    with (
        patch(
            "run_agent.get_tool_definitions",
            return_value=_make_tool_defs("web_search", "memory"),
        ),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
        patch("tools.registry.registry.get_definitions", side_effect=_registry_defs),
    ):
        agent = run_agent.AIAgent(
            api_key="test-key",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        agent.client = MagicMock()
        agent._use_prompt_caching = False
        agent.compression_enabled = False
        agent.save_trajectories = False
        agent.tool_delay = 0
        agent._cached_system_prompt = "You are helpful."
        agent._memory_store = None
        agent._memory_flush_min_turns = 1
        agent._user_turn_count = 4
        return agent


def test_fact_flush_writes_graph_and_later_turn_recalls(sparkgraph_agent):
    agent = sparkgraph_agent
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
        {"role": "user", "content": "Remember the socksio fix"},
    ]

    with patch("agent.auxiliary_client.call_llm", return_value=_chat_response_with_sparkgraph_call()):
        agent.flush_memories(messages, min_turns=0)
        agent._user_turn_count += 1
        agent.flush_memories(messages, min_turns=0)

    rows = agent._sparkgraph_store.search_nodes("socksio", status="active", limit=4)
    assert rows, "Expected SparkGraph flush to create an active node"
    recall_block, _ = agent._sparkgraph_manager.build_recall_block(
        "socksio may be required for SOCKS proxy support"
    )
    assert "[SparkGraph Recall]" in recall_block

    agent.client.chat.completions.create.return_value = _mock_response("Use socksio.")
    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation(
            "socksio may be required for SOCKS proxy support",
            conversation_history=list(messages),
        )

    assert result["completed"] is True
    call_args = agent.client.chat.completions.create.call_args
    api_messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
    assert "[SparkGraph Recall]" in api_messages[0]["content"]
    assert "socksio may be required for SOCKS proxy support" in api_messages[0]["content"]


def test_greeting_flush_writes_no_graph(sparkgraph_agent):
    agent = sparkgraph_agent
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
        {"role": "user", "content": "Just saying thanks"},
    ]

    with patch("agent.auxiliary_client.call_llm", return_value=_chat_response_without_tool_calls()):
        agent.flush_memories(messages, min_turns=0)

    rows = agent._sparkgraph_store.search_nodes("thanks", status="active", limit=4)
    assert rows == []


def test_preference_flush_writes_graph_and_later_turn_recalls(sparkgraph_agent):
    agent = sparkgraph_agent
    messages = [
        {"role": "user", "content": "Can we keep things concise?"},
        {"role": "assistant", "content": "Sure."},
        {"role": "user", "content": "Please keep replies concise going forward."},
    ]

    with patch("agent.auxiliary_client.call_llm", return_value=_chat_response_with_preference_call()):
        agent.flush_memories(messages, min_turns=0)
        agent._user_turn_count += 1
        agent.flush_memories(messages, min_turns=0)

    rows = agent._sparkgraph_store.search_nodes("concise replies", status="active", limit=4)
    assert rows, "Expected SparkGraph flush to create an active preference node"

    recall_block, _ = agent._sparkgraph_manager.build_recall_block("concise replies")
    assert "[SparkGraph Recall]" in recall_block
    assert "User prefers concise replies" in recall_block


def test_query_tools_can_inspect_flush_written_nodes(sparkgraph_agent):
    """sparkgraph_search and sparkgraph_stats are still callable as library functions."""
    agent = sparkgraph_agent
    messages = [
        {"role": "user", "content": "Can we keep things concise?"},
        {"role": "assistant", "content": "Sure."},
        {"role": "user", "content": "Please keep replies concise going forward."},
    ]

    with patch("agent.auxiliary_client.call_llm", return_value=_chat_response_with_preference_call()):
        agent.flush_memories(messages, min_turns=0)
        agent._user_turn_count += 1
        agent.flush_memories(messages, min_turns=0)

    search_payload = sparkgraph_search_tool(
        query="User prefers concise replies",
        store=agent._sparkgraph_store,
        limit=5,
    )
    stats_payload = sparkgraph_stats_tool(store=agent._sparkgraph_store)

    assert '"count": 1' in search_payload
    assert '"PREFERENCE"' in search_payload
    assert '"nodes_total": 1' in stats_payload
