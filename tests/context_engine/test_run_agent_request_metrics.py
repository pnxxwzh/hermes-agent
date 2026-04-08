"""Tests for request-level metrics integration in run_agent.py."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.context_engine import InputAssembly, InputNode
from agent.context_engine.models import ContextChunk, ContextMetrics, SourceMetrics


def _mock_assistant_msg(content="Hello", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _mock_response(content="Hello", finish_reason="stop", tool_calls=None):
    msg = _mock_assistant_msg(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], model="test/model", usage=None)


class TestRunAgentRequestMetrics:
    """Request metrics should reflect the final request buckets."""

    def _make_agent(self, *, prefill_messages=None, api_mode=None):
        from run_agent import AIAgent

        with patch("run_agent.OpenAI"), \
             patch("run_agent.get_tool_definitions", return_value=[]), \
             patch("run_agent.check_toolset_requirements", return_value={}):
            return AIAgent(
                api_key="test-key",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
                prefill_messages=prefill_messages,
                api_mode=api_mode,
            )

    def _make_request_assembly(self, *, messages=None, prefill_messages=None, tools=None):
        request_nodes = []
        for message in messages or []:
            role = message.get("role")
            if role == "user":
                name = "messages_user"
            elif role == "assistant":
                name = "messages_assistant"
            elif role == "tool":
                name = "messages_tool"
            else:
                name = "messages_other"
            request_nodes.append(InputNode(
                kind="message",
                name=name,
                stage="request",
                content=message,
            ))
        for message in prefill_messages or []:
            request_nodes.append(InputNode(
                kind="prefill",
                name="prefill_messages",
                stage="request",
                content=message,
            ))
        if tools:
            request_nodes.append(InputNode(
                kind="tool_schema",
                name="tool_schemas",
                stage="request",
                content=list(tools),
            ))
        return InputAssembly(
            request_nodes=request_nodes,
            normalized_messages=list(messages or []),
            prefill_messages=list(prefill_messages or []),
            tool_schemas=list(tools or []),
        )

    def test_input_assembly_request_metrics_fail_soft(self):
        agent = self._make_agent()
        agent._context_assembler = _stable_dynamic_assembler = MagicMock()
        _stable_dynamic_assembler.assemble_stable.return_value = MagicMock(
            stable_system="stable",
            metrics=ContextMetrics(0, 0, 0, []),
            stable_chunks=[],
        )
        _stable_dynamic_assembler.assemble_dynamic.return_value = MagicMock(
            effective_system="",
            dynamic_chunks=[],
            metrics=ContextMetrics(0, 0, 0, []),
        )

        class _BadAssembly:
            @property
            def request_metrics(self):
                raise RuntimeError("boom")

        bad_assembly = _BadAssembly()

        with (
            patch.object(agent, "_assemble_input_graph", return_value=bad_assembly),
            patch.object(agent, "_interruptible_api_call", return_value=_mock_response(content="done")),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
        ):
            result = agent.run_conversation("hello")

        assert result["completed"] is True
        assert agent._last_request_metrics is None

    def test_run_conversation_populates_request_metrics(self):
        agent = self._make_agent(
            prefill_messages=[
                {"role": "user", "content": "few-shot user"},
                {"role": "assistant", "content": "few-shot assistant"},
            ]
        )

        stable_chunk = ContextChunk(
            source="identity",
            stage="stable",
            slot="default",
            priority=1,
            content="stable identity",
        )
        stable_metrics = ContextMetrics(
            stable_tokens=len(stable_chunk.content) // 4,
            dynamic_tokens=0,
            total_estimated_tokens=len(stable_chunk.content) // 4,
            by_source=[SourceMetrics("identity", "stable", len(stable_chunk.content), len(stable_chunk.content) // 4)],
        )
        dynamic_chunk = ContextChunk(
            source="plugin",
            stage="dynamic",
            slot="plugin_context",
            priority=1,
            content="plugin note",
        )
        dynamic_metrics = ContextMetrics(
            stable_tokens=0,
            dynamic_tokens=len(dynamic_chunk.content) // 4,
            total_estimated_tokens=len(dynamic_chunk.content) // 4,
            by_source=[SourceMetrics("plugin", "dynamic", len(dynamic_chunk.content), len(dynamic_chunk.content) // 4)],
        )

        stable_result = MagicMock(
            stable_system="stable identity",
            metrics=stable_metrics,
            stable_chunks=[stable_chunk],
        )
        dynamic_result = MagicMock(
            effective_system="plugin note",
            dynamic_chunks=[dynamic_chunk],
            metrics=dynamic_metrics,
        )
        conversation_history = [
            {"role": "assistant", "content": "tool call pending", "tool_calls": [{"id": "call_1"}]},
        ]
        agent.tools = [{"name": "terminal", "parameters": {"type": "object"}}]
        mock_assembler = MagicMock()
        mock_assembler.assemble_stable.return_value = stable_result
        mock_assembler.assemble_dynamic.return_value = dynamic_result
        mock_assembler.assemble.return_value = self._make_request_assembly(
            messages=[conversation_history[0], {"role": "user", "content": "hello"}, {"role": "tool", "content": "", "tool_call_id": "call_1"}],
            prefill_messages=agent.prefill_messages,
            tools=agent.tools,
        )
        agent._context_assembler = mock_assembler

        captured_api_kwargs = {}

        def _fake_api_call(api_kwargs):
            captured_api_kwargs.update(api_kwargs)
            return _mock_response(content="done", finish_reason="stop")

        with (
            patch.object(agent, "_interruptible_api_call", side_effect=_fake_api_call),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
        ):
            result = agent.run_conversation("hello", conversation_history=conversation_history)

        assert result["completed"] is True
        metrics = agent._last_request_metrics
        assert metrics is not None
        assert result["request_metrics"] is metrics
        assert result["context_metrics"] is agent._last_context_metrics
        assert metrics.get_bucket("context_identity") is not None
        assert metrics.get_bucket("context_plugin") is not None
        assert metrics.get_bucket("prefill_messages") is not None
        assert metrics.get_bucket("tool_schemas") is not None
        assert metrics.get_bucket("messages_user") is not None
        assert metrics.get_bucket("messages_assistant") is not None
        assert metrics.get_bucket("messages_tool_hot") is not None
        assert metrics.total_estimated_tokens >= agent._last_context_metrics.total_estimated_tokens

        sent_messages = captured_api_kwargs["messages"]
        tool_messages = [m for m in sent_messages if m.get("role") == "tool"]
        assert tool_messages, "sanitizer should inject a stub tool result"
        assert metrics.get_bucket("messages_tool_hot").char_count > 0

    def test_request_metrics_ignore_cache_control_transport_wrapping(self):
        agent = self._make_agent()

        stable_chunk = ContextChunk(
            source="identity",
            stage="stable",
            slot="default",
            priority=1,
            content="stable identity",
        )
        stable_metrics = ContextMetrics(
            stable_tokens=len(stable_chunk.content) // 4,
            dynamic_tokens=0,
            total_estimated_tokens=len(stable_chunk.content) // 4,
            by_source=[SourceMetrics("identity", "stable", len(stable_chunk.content), len(stable_chunk.content) // 4)],
        )
        stable_result = MagicMock(
            stable_system="stable identity",
            metrics=stable_metrics,
            stable_chunks=[stable_chunk],
        )
        dynamic_result = MagicMock(
            effective_system="",
            dynamic_chunks=[],
            metrics=ContextMetrics(0, 0, 0, []),
        )
        mock_assembler = MagicMock()
        mock_assembler.assemble_stable.return_value = stable_result
        mock_assembler.assemble_dynamic.return_value = dynamic_result
        mock_assembler.assemble.return_value = self._make_request_assembly(
            messages=[{"role": "user", "content": "hello"}],
            prefill_messages=[],
            tools=[],
        )
        agent._context_assembler = mock_assembler
        agent._use_prompt_caching = True

        cached_payloads = []

        def _fake_cache_control(messages, cache_ttl="5m", native_anthropic=False):
            cached_payloads.append(messages)
            patched = []
            for msg in messages:
                clone = dict(msg)
                clone["cache_control"] = {"type": "ephemeral"}
                patched.append(clone)
            return patched

        def _fake_api_call(api_kwargs):
            return _mock_response(content="done", finish_reason="stop")

        with (
            patch.object(agent, "_interruptible_api_call", side_effect=_fake_api_call),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch("run_agent.apply_anthropic_cache_control", side_effect=_fake_cache_control),
        ):
            result = agent.run_conversation("hello")

        assert result["completed"] is True
        assert cached_payloads, "cache control should have been applied"
        metrics = agent._last_request_metrics
        assert metrics is not None
        assert result["request_metrics"] is metrics
        assert metrics.get_bucket("context_identity") is not None
        assert metrics.get_bucket("messages_user") is not None
        assert metrics.get_bucket("messages_assistant") is None

    def test_error_result_also_carries_metrics_snapshots(self):
        agent = self._make_agent()

        stable_chunk = ContextChunk(
            source="identity",
            stage="stable",
            slot="default",
            priority=1,
            content="stable identity",
        )
        stable_metrics = ContextMetrics(
            stable_tokens=len(stable_chunk.content) // 4,
            dynamic_tokens=0,
            total_estimated_tokens=len(stable_chunk.content) // 4,
            by_source=[SourceMetrics("identity", "stable", len(stable_chunk.content), len(stable_chunk.content) // 4)],
        )
        stable_result = MagicMock(
            stable_system="stable identity",
            metrics=stable_metrics,
            stable_chunks=[stable_chunk],
        )
        dynamic_result = MagicMock(
            effective_system="",
            dynamic_chunks=[],
            metrics=ContextMetrics(0, 0, 0, []),
        )
        mock_assembler = MagicMock()
        mock_assembler.assemble_stable.return_value = stable_result
        mock_assembler.assemble_dynamic.return_value = dynamic_result
        mock_assembler.assemble.return_value = self._make_request_assembly(
            messages=[{"role": "user", "content": "hello"}],
            prefill_messages=[],
            tools=[],
        )
        agent._context_assembler = mock_assembler

        class _BadRequestError(RuntimeError):
            status_code = 400

            def __init__(self):
                super().__init__("Error code: 400 - invalid request")

        with (
            patch.object(agent, "_interruptible_api_call", side_effect=_BadRequestError()),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(agent, "_try_activate_fallback", return_value=False),
            patch.object(agent, "_dump_api_request_debug"),
        ):
            result = agent.run_conversation("hello")

        assert result["completed"] is False
        assert result["failed"] is True
        assert result["request_metrics"] is agent._last_request_metrics
        assert result["context_metrics"] is agent._last_context_metrics

    def test_anthropic_request_metrics_use_transport_visible_metric_view(self):
        from agent.anthropic_adapter import convert_messages_to_anthropic_metric_view

        agent = self._make_agent(api_mode="anthropic_messages")
        request_messages = [
            {
                "role": "assistant",
                "content": "Earlier",
                "reasoning_details": [
                    {"type": "thinking", "thinking": "old chain", "signature": "sig-old"},
                ],
            },
            {"role": "user", "content": "Continue"},
            {
                "role": "assistant",
                "content": "Latest",
                "reasoning_details": [
                    {"type": "thinking", "thinking": "new chain", "signature": "sig-new"},
                ],
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "test", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "payload"},
        ]
        assembly = self._make_request_assembly(messages=request_messages)

        metrics = agent._build_final_request_metrics(
            input_assembly=assembly,
            final_api_messages=list(request_messages),
            effective_system="",
            prefill_messages=[],
            original_request_messages=list(request_messages),
            original_message_heat_by_index={4: "hot"},
            original_message_persistence_by_index={4: "inline"},
        )

        _, metric_messages = convert_messages_to_anthropic_metric_view(request_messages)
        expected_assistant_chars = sum(
            len(str(message)) for message in metric_messages if message.get("role") == "assistant"
        )
        expected_tool_chars = sum(
            len(str(message)) for message in metric_messages if message.get("role") == "tool"
        )

        assert metrics.get_bucket("messages_assistant").char_count == expected_assistant_chars
        assert metrics.get_bucket("messages_tool_hot").char_count == expected_tool_chars
