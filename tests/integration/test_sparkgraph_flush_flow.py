"""Integration-style SparkGraph flush flow tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import run_agent
from agent.sparkgraph.scoring import evidence_based_promotion
from agent.sparkgraph.types import NodeStatus
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
    with (
        patch(
            "run_agent.get_tool_definitions",
            return_value=_make_tool_defs("web_search", "memory"),
        ),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
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
    recall_block = agent._sparkgraph_manager.build_recall_block(
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

    recall_block = agent._sparkgraph_manager.build_recall_block("concise replies")
    assert "[SparkGraph Recall]" in recall_block
    assert "User prefers concise replies" in recall_block


def test_query_tools_can_inspect_flush_written_nodes(sparkgraph_agent):
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


# ─── B1 fix: three-layer recall integration tests ─────────────────────────────────


def test_b1_candidate_with_evidence_recalled_via_l1_direct(sparkgraph_agent):
    """TC-R-002 integration: CANDIDATE with evidence>=1 is recalled via L1 DIRECT (B1 fix).

    Core B1 fix: after a CANDIDATE accumulates evidence (simulating prior recall),
    it becomes L1 DIRECT-eligible and appears in the recall block.
    """
    agent = sparkgraph_agent
    # Write a CANDIDATE node
    result = sparkgraph_record_tool(
        items=[{
            "summary": "docker compose port forwarding not accessible from host",
            "type": "ISSUE",
            "evidence": "Ports exposed via docker compose expose: are not reachable on localhost",
        }],
        store=agent._sparkgraph_store,
        session_id="test-session-b1b",
        turn_index=1,
        source_kind="flush",
    )
    import json
    resp = json.loads(result)
    assert resp.get("success") or "created" in str(resp), f"record failed: {result}"

    rows = agent._sparkgraph_store.search_nodes("docker compose port forwarding", status=None, limit=4)
    assert rows, "Node should exist after record"
    node = rows[0]
    node_id = node["id"]
    assert node["status"] == NodeStatus.CANDIDATE.value

    # Simulate prior recall: add evidence to the node
    agent._sparkgraph_store.append_evidence(
        node_id=node_id,
        session_id="prior-session",
        turn_index=5,
        source_text="docker compose port forwarding issue",
        source_kind="recall",
    )

    # Now L1 DIRECT recall: candidate with evidence>=1 should appear
    recall_block = agent._sparkgraph_manager.build_recall_block(
        "docker compose port forwarding not accessible"
    )
    assert "[SparkGraph Recall]" in recall_block, "Recall block should be generated"
    assert "docker compose port forwarding" in recall_block, (
        "CANDIDATE with evidence>=1 should appear in L1 DIRECT recall block (B1 fix)"
    )


def test_b1_evidence_based_promotion_upgrades_stale_candidate(sparkgraph_agent):
    """TC-M-001 integration: STALE CANDIDATE with evidence>=2 is promoted to ACTIVE.

    run_flush_maintenance processes stale candidates (not recently updated).
    To test promotion, we backdate the node's updated_at to make it appear stale.
    """
    agent = sparkgraph_agent
    import time
    import json

    # Write a low-confidence CANDIDATE
    result = sparkgraph_record_tool(
        items=[{
            "summary": "redis connection refused error on remote clients",
            "type": "ISSUE",
            "evidence": "Remote redis-cli clients get connection refused when bind is set to 127.0.0.1",
        }],
        store=agent._sparkgraph_store,
        session_id="test-session-b1c",
        turn_index=1,
        source_kind="flush",
    )
    resp = json.loads(result)
    assert resp.get("success") or "created" in str(resp), f"record failed: {result}"

    rows = agent._sparkgraph_store.search_nodes("redis connection refused", status=None, limit=4)
    node_id = rows[0]["id"]

    # Add 2 evidence rows (simulating 2 successful recalls)
    agent._sparkgraph_store.append_evidence(
        node_id=node_id, session_id="s1", turn_index=1,
        source_text="redis connection refused issue session 1", source_kind="recall",
    )
    agent._sparkgraph_store.append_evidence(
        node_id=node_id, session_id="s2", turn_index=1,
        source_text="redis connection refused issue session 2", source_kind="recall",
    )

    # Backdate the node to make it "stale" (so maintenance processes it)
    # STALE_CANDIDATE_DAYS = 14, so updated_at must be >14 days old
    stale_ts = int(time.time()) - (15 * 24 * 60 * 60)
    agent._sparkgraph_store._conn.execute(
        "UPDATE sg_nodes SET updated_at = ? WHERE id = ?",
        (stale_ts, node_id),
    )
    agent._sparkgraph_store._conn.commit()

    # Run maintenance — evidence_based_promotion sees evidence=2 → ACTIVE
    from agent.sparkgraph.maintenance import run_flush_maintenance
    result_stats = run_flush_maintenance(
        agent._sparkgraph_store,
        now_ts=int(time.time()),
    )
    assert result_stats.get("deprecated", 0) == 0, "Should not deprecate evidence-rich node"

    # Verify node is now ACTIVE
    node_after = agent._sparkgraph_store.get_node(node_id)
    assert node_after["status"] == NodeStatus.ACTIVE.value, (
        f"CANDIDATE with evidence>=2 should be promoted to ACTIVE, got {node_after['status']}"
    )


def test_b1_evidence_based_promotion_via_direct_function(sparkgraph_agent):
    """TC-M-001 unit-style test: evidence_based_promotion() with evidence=2 → ACTIVE."""
    # Direct function test — verifies the promotion logic without maintenance
    node = {
        "status": NodeStatus.CANDIDATE.value,
        "confidence": 0.55,
        "stability": 0.50,
        "_evidence_count": 2,
    }
    result = evidence_based_promotion(node)
    assert result == NodeStatus.ACTIVE, (
        f"evidence=2 should return ACTIVE, got {result.value}"
    )

    # Evidence=1 + confidence>=0.65 → ACTIVE
    node2 = {
        "status": NodeStatus.CANDIDATE.value,
        "confidence": 0.68,
        "stability": 0.50,
        "_evidence_count": 1,
    }
    result2 = evidence_based_promotion(node2)
    assert result2 == NodeStatus.ACTIVE, f"evidence=1 + conf>=0.65 → ACTIVE, got {result2.value}"

    # Evidence=1 + confidence<0.65 → CANDIDATE
    node3 = {
        "status": NodeStatus.CANDIDATE.value,
        "confidence": 0.55,
        "stability": 0.50,
        "_evidence_count": 1,
    }
    result3 = evidence_based_promotion(node3)
    assert result3 == NodeStatus.CANDIDATE, f"evidence=1 + conf<0.65 → CANDIDATE, got {result3.value}"


def test_b1_full_cycle_candidate_to_active_via_recall(sparkgraph_agent):
    """B1 full cycle integration: CANDIDATE → recalled → promoted ACTIVE.

    End-to-end verification:
    1. Flush creates low-confidence CANDIDATE
    2. L1 DIRECT: with evidence>=1, node appears in recall block (B1 fix verified)
    3. Maintenance on stale node: evidence>=2 → promoted ACTIVE
    """
    import time
    import json

    agent = sparkgraph_agent

    # Step 1: Flush creates low-confidence CANDIDATE
    result = sparkgraph_record_tool(
        items=[{
            "summary": "docker compose network isolation not working",
            "type": "ISSUE",
            "evidence": "Containers in the same docker compose service cannot reach each other",
        }],
        store=agent._sparkgraph_store,
        session_id="test-session-b1d",
        turn_index=1,
        source_kind="flush",
    )
    resp = json.loads(result)
    assert resp.get("success") or "created" in str(resp), f"record failed: {result}"

    rows = agent._sparkgraph_store.search_nodes("docker compose network isolation", status=None, limit=4)
    node = rows[0]
    node_id = node["id"]
    assert node["status"] == NodeStatus.CANDIDATE.value

    # Step 2: Add evidence (simulating prior recall)
    agent._sparkgraph_store.append_evidence(
        node_id=node_id, session_id="recall-session-1", turn_index=1,
        source_text="docker compose network isolation issue", source_kind="recall",
    )

    # Step 3: L1 DIRECT recall — with evidence>=1, node should appear
    recall_block = agent._sparkgraph_manager.build_recall_block("docker compose network isolation")
    assert "[SparkGraph Recall]" in recall_block, (
        "Node with evidence>=1 should appear in L1 DIRECT recall block (B1 fix)"
    )
    assert "docker compose network isolation" in recall_block, (
        "CANDIDATE with evidence>=1 should appear in recall block"
    )

    # Step 4: Add second evidence and backdate → maintenance promotes
    agent._sparkgraph_store.append_evidence(
        node_id=node_id, session_id="recall-session-2", turn_index=1,
        source_text="docker compose network isolation issue session 2", source_kind="recall",
    )
    stale_ts = int(time.time()) - (15 * 24 * 60 * 60)
    agent._sparkgraph_store._conn.execute(
        "UPDATE sg_nodes SET updated_at = ? WHERE id = ?",
        (stale_ts, node_id),
    )
    agent._sparkgraph_store._conn.commit()

    from agent.sparkgraph.maintenance import run_flush_maintenance
    run_flush_maintenance(agent._sparkgraph_store, now_ts=int(time.time()))

    node_final = agent._sparkgraph_store.get_node(node_id)
    assert node_final["status"] == NodeStatus.ACTIVE.value, (
        f"After 2 recalls (evidence=2): CANDIDATE should be ACTIVE, got {node_final['status']}"
    )
