"""Tests for request-level input sources."""

from agent.context_engine.context import AssemblyContext
from agent.context_engine.input_sources import (
    ConversationMessagesSource,
    PrefillMessagesSource,
    ToolSchemasSource,
)


def _ctx(**kwargs):
    return AssemblyContext(agent=None, **kwargs)


class TestConversationMessagesSource:
    def test_user_message_maps_to_messages_user(self):
        nodes = ConversationMessagesSource().collect(_ctx(
            conversation_history=[{"role": "user", "content": "hello"}],
        ))

        assert [node.name for node in nodes] == ["messages_user"]

    def test_assistant_message_maps_to_messages_assistant(self):
        nodes = ConversationMessagesSource().collect(_ctx(
            conversation_history=[{"role": "assistant", "content": "hello"}],
        ))

        assert [node.name for node in nodes] == ["messages_assistant"]

    def test_assistant_with_tool_calls_still_maps_to_messages_assistant(self):
        nodes = ConversationMessagesSource().collect(_ctx(
            conversation_history=[{
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "1", "type": "function"}],
            }],
        ))

        assert [node.name for node in nodes] == ["messages_assistant"]

    def test_tool_message_maps_to_messages_tool(self):
        nodes = ConversationMessagesSource().collect(_ctx(
            conversation_history=[{"role": "tool", "content": "result", "tool_call_id": "1"}],
        ))

        assert [node.name for node in nodes] == ["messages_tool"]

    def test_missing_role_maps_to_messages_other(self):
        nodes = ConversationMessagesSource().collect(_ctx(
            conversation_history=[{"content": "mystery"}],
        ))

        assert [node.name for node in nodes] == ["messages_other"]

    def test_empty_input_returns_empty(self):
        assert ConversationMessagesSource().collect(_ctx(conversation_history=[])) == []

    def test_structured_content_is_supported(self):
        nodes = ConversationMessagesSource().collect(_ctx(
            conversation_history=[{
                "role": "user",
                "content": [{"type": "text", "text": "hello"}],
            }],
        ))

        assert nodes[0].name == "messages_user"
        assert nodes[0].char_count > 0

    def test_non_dict_message_is_wrapped(self):
        nodes = ConversationMessagesSource().collect(_ctx(
            conversation_history=["raw text"],
        ))

        assert nodes[0].name == "messages_other"
        assert nodes[0].content == {"content": "raw text"}


class TestPrefillMessagesSource:
    def test_prefill_messages_map_to_prefill_bucket(self):
        nodes = PrefillMessagesSource().collect(_ctx(
            prefill_messages=[{"role": "assistant", "content": "prefill"}],
        ))

        assert [node.name for node in nodes] == ["prefill_messages"]

    def test_missing_content_is_supported(self):
        nodes = PrefillMessagesSource().collect(_ctx(
            prefill_messages=[{"role": "assistant"}],
        ))

        assert nodes[0].name == "prefill_messages"
        assert nodes[0].char_count > 0

    def test_empty_prefill_returns_empty(self):
        assert PrefillMessagesSource().collect(_ctx(prefill_messages=[])) == []


class TestToolSchemasSource:
    def test_tools_map_to_tool_schemas(self):
        nodes = ToolSchemasSource().collect(_ctx(
            tool_schemas=[{"name": "shell"}, {"name": "read_file"}],
        ))

        assert [node.name for node in nodes] == ["tool_schemas"]
        assert nodes[0].metadata["count"] == 2

    def test_empty_tools_returns_empty(self):
        assert ToolSchemasSource().collect(_ctx(tool_schemas=[])) == []
