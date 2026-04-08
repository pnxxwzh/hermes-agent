"""Tests for unified-input shadow compare migration in run_agent."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.context_engine import InputAssembly
from agent.context_engine.models import ContextChunk, ContextMetrics, SourceMetrics
from agent.context_engine.transport import TransportPayload


def _mock_response(content="Hello", finish_reason="stop"):
    msg = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], model="test/model", usage=None)


def _make_agent():
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
            model="openai/gpt-4.1",
        )
    agent._unified_input_shadow_compare = True
    agent._unified_input_engine = False
    agent._unified_input_compare_fail_fast = False
    return agent


def _stable_dynamic_assembler():
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
    assembler = MagicMock()
    assembler.assemble_stable.return_value = stable_result
    assembler.assemble_dynamic.return_value = dynamic_result
    return assembler


class TestUnifiedEngineMigration:
    def test_shadow_compare_does_not_change_sent_request(self):
        agent = _make_agent()
        agent._context_assembler = _stable_dynamic_assembler()

        captured_api_kwargs = {}

        def _fake_api_call(api_kwargs):
            captured_api_kwargs.update(api_kwargs)
            return _mock_response("done")

        with (
            patch.object(agent, "_interruptible_api_call", side_effect=_fake_api_call),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(agent, "_assemble_input_graph", return_value=MagicMock()),
            patch.object(
                agent,
                "_build_transport_payload",
                return_value=TransportPayload(api_messages=[{"role": "user", "content": "shadow"}], api_kwargs={"messages": [{"role": "user", "content": "shadow"}]}),
            ),
            patch.object(agent, "_compare_engine_and_legacy_payload", return_value=None) as compare_mock,
        ):
            result = agent.run_conversation("hello")

        assert result["completed"] is True
        assert compare_mock.called
        assert captured_api_kwargs["messages"][0]["role"] == "system"
        assert captured_api_kwargs["messages"][1]["role"] == "user"
        assert captured_api_kwargs["messages"][1]["content"] == "hello"
        assert result["request_metrics"] is agent._last_request_metrics

    def test_shadow_compare_diff_falls_back_to_legacy_request(self):
        agent = _make_agent()
        agent._context_assembler = _stable_dynamic_assembler()

        captured_api_kwargs = {}

        def _fake_api_call(api_kwargs):
            captured_api_kwargs.update(api_kwargs)
            return _mock_response("done")

        diff = "api_messages[1].content: 'legacy' != 'engine'"
        with (
            patch.object(agent, "_interruptible_api_call", side_effect=_fake_api_call),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(agent, "_assemble_input_graph", return_value=MagicMock()),
            patch.object(
                agent,
                "_build_transport_payload",
                return_value=TransportPayload(api_messages=[{"role": "user", "content": "shadow"}], api_kwargs={"messages": [{"role": "user", "content": "shadow"}]}),
            ),
            patch.object(agent, "_compare_engine_and_legacy_payload", return_value=diff),
        ):
            result = agent.run_conversation("hello")

        assert result["completed"] is True
        assert agent._last_unified_input_compare_diff == diff
        assert captured_api_kwargs["messages"][1]["role"] == "user"
        assert captured_api_kwargs["messages"][1]["content"] == "hello"

    def test_compare_fail_fast_raises_only_when_enabled(self):
        agent = _make_agent()
        agent._context_assembler = _stable_dynamic_assembler()
        agent._unified_input_compare_fail_fast = True

        with (
            patch.object(agent, "_interruptible_api_call", side_effect=AssertionError("should not send request")),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(agent, "_assemble_input_graph", return_value=MagicMock()),
            patch.object(
                agent,
                "_build_transport_payload",
                return_value=TransportPayload(api_messages=[{"role": "user", "content": "shadow"}], api_kwargs={"messages": [{"role": "user", "content": "shadow"}]}),
            ),
            patch.object(agent, "_compare_engine_and_legacy_payload", return_value="api_messages[1].content: 'legacy' != 'engine'"),
        ):
            with patch("run_agent.logger.warning"):
                try:
                    agent.run_conversation("hello")
                except RuntimeError as exc:
                    assert "Unified input shadow compare mismatch" in str(exc)
                else:
                    raise AssertionError("Expected RuntimeError when compare_fail_fast is enabled")

    def test_unified_input_engine_uses_engine_payload_for_request(self):
        agent = _make_agent()
        agent._context_assembler = _stable_dynamic_assembler()
        agent._unified_input_engine = True
        agent._unified_input_shadow_compare = False

        captured_api_kwargs = {}

        def _fake_api_call(api_kwargs):
            captured_api_kwargs.update(api_kwargs)
            return _mock_response("done")

        input_assembly = InputAssembly(
            normalized_messages=[{"role": "user", "content": "hello"}],
        )

        with (
            patch.object(agent, "_interruptible_api_call", side_effect=_fake_api_call),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(agent, "_assemble_input_graph", return_value=input_assembly),
            patch.object(
                agent,
                "_build_transport_payload",
                return_value=TransportPayload(
                    api_messages=[{"role": "user", "content": "engine-message"}],
                    api_kwargs={"messages": [{"role": "user", "content": "engine-message"}]},
                ),
            ),
        ):
            result = agent.run_conversation("hello")

        assert result["completed"] is True
        assert captured_api_kwargs["messages"][0]["content"] == "engine-message"
        assert result["context_metrics"] == input_assembly.context_metrics
        assert result["request_metrics"] is not None

    def test_unified_input_engine_falls_back_to_legacy_on_engine_failure(self):
        agent = _make_agent()
        agent._context_assembler = _stable_dynamic_assembler()
        agent._unified_input_engine = True
        agent._unified_input_shadow_compare = False

        captured_api_kwargs = {}

        def _fake_api_call(api_kwargs):
            captured_api_kwargs.update(api_kwargs)
            return _mock_response("done")

        with (
            patch.object(agent, "_interruptible_api_call", side_effect=_fake_api_call),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(agent, "_assemble_input_graph", side_effect=RuntimeError("engine boom")),
        ):
            result = agent.run_conversation("hello")

        assert result["completed"] is True
        assert captured_api_kwargs["messages"][0]["role"] == "system"
        assert captured_api_kwargs["messages"][1]["content"] == "hello"
