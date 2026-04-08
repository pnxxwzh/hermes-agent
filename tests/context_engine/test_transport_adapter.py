"""Tests for transport adapters and legacy payload equivalence."""

from unittest.mock import patch

from agent.context_engine import (
    AnthropicMessagesTransportAdapter,
    ChatCompletionsTransportAdapter,
    CodexResponsesTransportAdapter,
    InputAssembly,
    get_transport_adapter,
)
from agent.context_engine.tool_compaction import ShapedToolHistory


def _make_agent(*, api_mode="chat_completions", model="openai/gpt-4.1", base_url="https://openrouter.ai/api/v1"):
    from run_agent import AIAgent

    with (
        patch("run_agent.OpenAI"),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
    ):
        agent = AIAgent(
            api_key="test-key",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            api_mode=api_mode,
            model=model,
            base_url=base_url,
        )
    return agent


def _assembly(
    *,
    effective_system="SYSTEM",
    messages=None,
    prefill=None,
    tools=None,
):
    return InputAssembly(
        effective_system=effective_system,
        normalized_messages=list(messages or []),
        prefill_messages=list(prefill or []),
        tool_schemas=list(tools or []),
    )


def _legacy_api_messages(agent, assembly):
    from agent.prompt_caching import apply_anthropic_cache_control

    api_messages = []
    for msg in assembly.normalized_messages:
        api_msg = dict(msg)
        if msg.get("role") == "assistant":
            reasoning_text = msg.get("reasoning")
            if reasoning_text:
                api_msg["reasoning_content"] = reasoning_text
        api_msg.pop("reasoning", None)
        api_msg.pop("finish_reason", None)
        if "api.mistral.ai" in getattr(agent, "_base_url_lower", ""):
            agent._sanitize_tool_calls_for_strict_api(api_msg)
        api_messages.append(api_msg)

    if assembly.effective_system:
        api_messages = [{"role": "system", "content": assembly.effective_system}] + api_messages

    if assembly.prefill_messages:
        sys_offset = 1 if assembly.effective_system else 0
        for idx, pfm in enumerate(assembly.prefill_messages):
            api_messages.insert(sys_offset + idx, dict(pfm))

    if getattr(agent, "_use_prompt_caching", False):
        api_messages = apply_anthropic_cache_control(
            api_messages,
            cache_ttl=getattr(agent, "_cache_ttl", None),
            native_anthropic=(getattr(agent, "api_mode", "") == "anthropic_messages"),
        )

    return agent._sanitize_api_messages(api_messages)


def _legacy_api_kwargs(agent, assembly):
    api_messages = _legacy_api_messages(agent, assembly)
    original_tools = getattr(agent, "tools", None)
    try:
        agent.tools = list(assembly.tool_schemas or [])
        return agent._build_api_kwargs(api_messages)
    finally:
        agent.tools = original_tools


class TestTransportAdapterSelection:
    def test_get_transport_adapter_returns_expected_types(self):
        assert isinstance(get_transport_adapter("chat_completions"), ChatCompletionsTransportAdapter)
        assert isinstance(get_transport_adapter("codex_responses"), CodexResponsesTransportAdapter)
        assert isinstance(get_transport_adapter("anthropic_messages"), AnthropicMessagesTransportAdapter)

    def test_run_agent_lazily_binds_transport_adapter(self):
        agent = _make_agent(api_mode="codex_responses")

        adapter = agent._get_transport_adapter()

        assert isinstance(adapter, CodexResponsesTransportAdapter)
        assert agent._get_transport_adapter() is adapter


class TestTransportAdapterEquivalence:
    def test_chat_completions_payload_equivalence(self):
        agent = _make_agent(api_mode="chat_completions")
        assembly = _assembly(
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi", "reasoning": "think", "finish_reason": "stop"},
                {"role": "tool", "content": "{}", "tool_call_id": "call_1"},
            ],
            prefill=[{"role": "assistant", "content": "few-shot"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "shell",
                    "description": "run shell",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        )

        adapter = ChatCompletionsTransportAdapter()

        assert adapter.build_api_messages(assembly, agent) == _legacy_api_messages(agent, assembly)
        assert adapter.build_api_kwargs(assembly, agent) == _legacy_api_kwargs(agent, assembly)

    def test_codex_payload_equivalence(self):
        agent = _make_agent(api_mode="codex_responses", model="openai/gpt-5")
        assembly = _assembly(
            messages=[
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": "working",
                    "tool_calls": [{
                        "id": "call_1",
                        "function": {"name": "shell", "arguments": "{\"cmd\":\"pwd\"}"},
                    }],
                },
                {"role": "tool", "content": "{\"ok\":true}", "tool_call_id": "call_1"},
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": "shell",
                    "description": "run shell",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        )

        adapter = CodexResponsesTransportAdapter()

        assert adapter.build_api_messages(assembly, agent) == _legacy_api_messages(agent, assembly)
        assert adapter.build_api_kwargs(assembly, agent) == _legacy_api_kwargs(agent, assembly)

    def test_anthropic_payload_equivalence(self):
        agent = _make_agent(
            api_mode="anthropic_messages",
            model="claude-3-7-sonnet-20250219",
            base_url="https://api.anthropic.com/v1",
        )
        assembly = _assembly(
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": "shell",
                    "description": "run shell",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        )

        adapter = AnthropicMessagesTransportAdapter()

        assert adapter.build_api_messages(assembly, agent) == _legacy_api_messages(agent, assembly)
        assert adapter.build_api_kwargs(assembly, agent) == _legacy_api_kwargs(agent, assembly)

    def test_anthropic_multimodal_flatten_equivalence(self):
        agent = _make_agent(
            api_mode="anthropic_messages",
            model="claude-3-7-sonnet-20250219",
            base_url="https://api.anthropic.com/v1",
        )
        assembly = _assembly(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                    {"type": "text", "text": "Describe this"},
                ],
            }],
        )
        adapter = AnthropicMessagesTransportAdapter()

        with patch.object(
            agent,
            "_describe_image_for_anthropic_fallback",
            return_value="[image description]",
        ):
            assert adapter.build_api_kwargs(assembly, agent) == _legacy_api_kwargs(agent, assembly)

    def test_cache_control_insertion_order_equivalence(self):
        agent = _make_agent(
            api_mode="anthropic_messages",
            model="claude-3-7-sonnet-20250219",
            base_url="https://api.anthropic.com/v1",
        )
        agent._use_prompt_caching = True
        assembly = _assembly(
            messages=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "second"},
                {"role": "user", "content": "third"},
            ],
        )
        adapter = AnthropicMessagesTransportAdapter()

        assert adapter.build_api_messages(assembly, agent) == _legacy_api_messages(agent, assembly)

    def test_prefill_insertion_order_equivalence(self):
        agent = _make_agent(api_mode="chat_completions")
        assembly = _assembly(
            messages=[{"role": "user", "content": "live"}],
            prefill=[
                {"role": "user", "content": "few-shot u"},
                {"role": "assistant", "content": "few-shot a"},
            ],
        )
        adapter = ChatCompletionsTransportAdapter()
        actual = adapter.build_api_messages(assembly, agent)

        assert actual[:4] == [
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "few-shot u"},
            {"role": "assistant", "content": "few-shot a"},
            {"role": "user", "content": "live"},
        ]
        assert actual == _legacy_api_messages(agent, assembly)

    def test_transport_payload_does_not_include_tool_heat_metadata(self):
        agent = _make_agent(api_mode="chat_completions")
        assembly = InputAssembly(
            normalized_messages=[
                {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_1"}]},
                {"role": "tool", "content": "trimmed", "tool_call_id": "call_1"},
            ],
            tool_compaction_snapshot=ShapedToolHistory(
                shaped_messages=[
                    {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_1"}]},
                    {"role": "tool", "content": "trimmed", "tool_call_id": "call_1"},
                ],
                hot_groups=[],
                warm_groups=[],
                cold_groups=[],
                message_heat_by_index={1: "warm"},
            ),
        )
        adapter = ChatCompletionsTransportAdapter()
        api_messages = adapter.build_api_messages(assembly, agent)

        assert api_messages == [
            {"role": "assistant", "content": "call", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "content": "trimmed", "tool_call_id": "call_1"},
        ]
        assert "_tool_heat" not in api_messages[1]
        assert "heat" not in api_messages[1]

    def test_strict_field_stripping_hook_matches_legacy(self):
        agent = _make_agent(
            api_mode="chat_completions",
            model="mistral-large-latest",
            base_url="https://api.mistral.ai/v1",
        )
        assembly = _assembly(
            messages=[{
                "role": "assistant",
                "content": "working",
                "tool_calls": [{
                    "id": "call_1",
                    "call_id": "call_1",
                    "response_item_id": "fc_1",
                    "function": {"name": "shell", "arguments": "{}"},
                }],
                "codex_reasoning_items": [{"type": "reasoning", "encrypted_content": "abc"}],
            }],
        )
        adapter = ChatCompletionsTransportAdapter()

        actual = adapter.build_api_messages(assembly, agent)

        assert actual == _legacy_api_messages(agent, assembly)
        tool_call = actual[1]["tool_calls"][0]
        assert "call_id" not in tool_call
        assert "response_item_id" not in tool_call
