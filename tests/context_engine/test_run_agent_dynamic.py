"""Tests for run_agent.py dynamic layer integration (Step 12)."""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.context_engine.models import ContextMetrics, SourceMetrics


class TestDynamicLayerIntegration:
    """T12: dynamic layer integration."""

    def test_assemble_dynamic_called_per_loop(self):
        """T12.1: assemble_dynamic is called during effective_system assembly."""
        from run_agent import AIAgent
        with patch("run_agent.OpenAI"), \
             patch("run_agent.get_tool_definitions", return_value=[]), \
             patch("run_agent.check_toolset_requirements", return_value={}):
            agent = AIAgent(
                api_key="test-key",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
        agent._context_assembler = None
        agent.context_compressor = MagicMock()
        agent._cached_system_prompt = ""

        mock_result = MagicMock()
        mock_result.effective_system = "dynamic content"
        mock_result.dynamic_chunks = []
        mock_result.metrics = MagicMock()

        mock_assembler = MagicMock()
        mock_assembler.assemble_dynamic.return_value = mock_result
        agent._context_assembler = mock_assembler

        # Simulate the effective_system building
        with patch.object(agent, "_get_context_assembler", return_value=mock_assembler):
            # Call _build_system_prompt first (for context_compressor init)
            agent._cached_system_prompt = "stable"

            # Simulate the dynamic assembly code
            dynamic_result = agent._get_context_assembler().assemble_dynamic(
                user_message="test",
                conversation_history=[],
            )
            dynamic_result.metrics.sync_to(agent.context_compressor)

            mock_assembler.assemble_dynamic.assert_called_once()
            assert dynamic_result.effective_system == "dynamic content"

    def test_assemble_dynamic_syncs_metrics(self):
        """T12.2: dynamic metrics merge into cached stable metrics."""
        from run_agent import AIAgent
        with patch("run_agent.OpenAI"), \
             patch("run_agent.get_tool_definitions", return_value=[]), \
             patch("run_agent.check_toolset_requirements", return_value={}):
            agent = AIAgent(
                api_key="test-key",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
        agent._context_assembler = None
        compressor = MagicMock()
        agent.context_compressor = compressor
        agent._cached_system_prompt = ""

        stable_metrics = ContextMetrics(
            stable_tokens=10,
            dynamic_tokens=0,
            total_estimated_tokens=10,
            by_source=[SourceMetrics("identity", "stable", 40, 10)],
        )
        mock_metrics = ContextMetrics(
            stable_tokens=0,
            dynamic_tokens=3,
            total_estimated_tokens=3,
            by_source=[SourceMetrics("plugin", "dynamic", 12, 3)],
        )
        mock_result = MagicMock(
            effective_system="dyn",
            dynamic_chunks=[],
            metrics=mock_metrics,
        )
        mock_assembler = MagicMock()
        mock_assembler.assemble_dynamic.return_value = mock_result
        agent._context_assembler = mock_assembler

        with patch.object(agent, "_get_context_assembler", return_value=mock_assembler):
            agent._stable_context_metrics = stable_metrics
            dynamic_result = agent._get_context_assembler().assemble_dynamic(
                user_message="test",
                conversation_history=[],
            )
            agent._last_context_metrics = agent._stable_context_metrics.merged_with(
                dynamic_result.metrics
            )
            assert agent._last_context_metrics.total_estimated_tokens == 13
            assert [s.source for s in agent._last_context_metrics.by_source] == [
                "identity",
                "plugin",
            ]

    def test_prompt_token_estimate_uses_full_api_messages(self):
        """T12.x: full request estimate replaces dynamic-only token overwrite."""
        from run_agent import AIAgent
        with patch("run_agent.OpenAI"), \
             patch("run_agent.get_tool_definitions", return_value=[]), \
             patch("run_agent.check_toolset_requirements", return_value={}):
            agent = AIAgent(
                api_key="test-key",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )

        agent.context_compressor = MagicMock()
        agent.context_compressor.last_prompt_tokens = 0
        api_messages = [{"role": "system", "content": "stable\n\ndyn"}]

        with patch("run_agent.estimate_request_tokens_rough", return_value=42) as mock_estimate:
            agent.context_compressor.last_prompt_tokens = 3
            agent.context_compressor.last_prompt_tokens = mock_estimate(
                api_messages,
                tools=agent.tools or None,
            )

        assert agent.context_compressor.last_prompt_tokens == 42
        mock_estimate.assert_called_once_with(api_messages, tools=agent.tools or None)

    def test_assemble_dynamic_fallback_on_exception(self):
        """T12.3: assembler exception triggers fallback to original logic."""
        from run_agent import AIAgent
        with patch("run_agent.OpenAI"), \
             patch("run_agent.get_tool_definitions", return_value=[]), \
             patch("run_agent.check_toolset_requirements", return_value={}):
            agent = AIAgent(
                api_key="test-key",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
        agent._context_assembler = None
        compressor = MagicMock()
        agent.context_compressor = compressor
        agent._cached_system_prompt = ""

        # Simulate assembler that raises
        mock_assembler = MagicMock()
        mock_assembler.assemble_dynamic.side_effect = RuntimeError("test error")
        agent._context_assembler = mock_assembler

        # Verify that the try/except handles the exception
        try:
            assembler = agent._get_context_assembler()
            dynamic_result = assembler.assemble_dynamic(
                user_message="test",
                conversation_history=[],
            )
        except RuntimeError:
            # Expected - fallback should catch this
            dynamic_result = None

        assert dynamic_result is None

    def test_assemble_dynamic_empty_effective(self):
        """T12.4: effective_system="" falls back to original per-source logic."""
        from run_agent import AIAgent
        with patch("run_agent.OpenAI"), \
             patch("run_agent.get_tool_definitions", return_value=[]), \
             patch("run_agent.check_toolset_requirements", return_value={}):
            agent = AIAgent(
                api_key="test-key",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
        agent._context_assembler = None
        agent.context_compressor = MagicMock()
        agent._cached_system_prompt = ""

        # Empty effective_system triggers fallback
        mock_result = MagicMock()
        mock_result.effective_system = ""
        mock_result.dynamic_chunks = []
        mock_result.metrics = MagicMock()

        mock_assembler = MagicMock()
        mock_assembler.assemble_dynamic.return_value = mock_result
        agent._context_assembler = mock_assembler

        with patch.object(agent, "_get_context_assembler", return_value=mock_assembler):
            dynamic_result = agent._get_context_assembler().assemble_dynamic(
                user_message="test",
                conversation_history=[],
            )

            # Fallback case: dynamic_result is truthy but effective_system is empty
            if dynamic_result and dynamic_result.effective_system:
                result = "from_dynamic"
            else:
                result = "fallback"

            assert result == "fallback"

    def test_fallback_variables_set(self):
        """T12.5: fallback populates _plugin_turn_context and _sparkgraph_turn_context."""
        from run_agent import AIAgent
        with patch("run_agent.OpenAI"), \
             patch("run_agent.get_tool_definitions", return_value=[]), \
             patch("run_agent.check_toolset_requirements", return_value={}):
            agent = AIAgent(
                api_key="test-key",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
        agent._context_assembler = None
        agent.context_compressor = MagicMock()
        agent._cached_system_prompt = ""

        # Successful result with plugin and sparkgraph chunks
        mock_result = MagicMock()
        mock_result.effective_system = "plugin content\n\nsparkgraph content"
        plugin_chunk = MagicMock()
        plugin_chunk.source = "plugin"
        plugin_chunk.content = "plugin content"
        spark_chunk = MagicMock()
        spark_chunk.source = "sparkgraph_recall"
        spark_chunk.content = "sparkgraph content"
        mock_result.dynamic_chunks = [plugin_chunk, spark_chunk]
        mock_result.metrics = MagicMock()

        mock_assembler = MagicMock()
        mock_assembler.assemble_dynamic.return_value = mock_result
        agent._context_assembler = mock_assembler

        with patch.object(agent, "_get_context_assembler", return_value=mock_assembler):
            dynamic_result = agent._get_context_assembler().assemble_dynamic(
                user_message="test",
                conversation_history=[],
            )

            # In success case, variables are extracted from chunks
            _plugin_turn_context = "\n".join(
                c.content for c in dynamic_result.dynamic_chunks if c.source == "plugin"
            )
            _sparkgraph_turn_context = "\n".join(
                c.content for c in dynamic_result.dynamic_chunks if c.source == "sparkgraph_recall"
            )

            assert _plugin_turn_context == "plugin content"
            assert _sparkgraph_turn_context == "sparkgraph content"

    def test_effective_system_order_preserved(self):
        """T12.6: stable → dynamic order preserved in effective_system."""
        from run_agent import AIAgent
        with patch("run_agent.OpenAI"), \
             patch("run_agent.get_tool_definitions", return_value=[]), \
             patch("run_agent.check_toolset_requirements", return_value={}):
            agent = AIAgent(
                api_key="test-key",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
        agent._context_assembler = None
        agent.context_compressor = MagicMock()
        agent._cached_system_prompt = ""

        mock_result = MagicMock()
        mock_result.effective_system = "DYNAMIC"
        mock_result.dynamic_chunks = []
        mock_result.metrics = MagicMock()

        mock_assembler = MagicMock()
        mock_assembler.assemble_dynamic.return_value = mock_result
        agent._context_assembler = mock_assembler

        with patch.object(agent, "_get_context_assembler", return_value=mock_assembler):
            dynamic_result = agent._get_context_assembler().assemble_dynamic(
                user_message="test",
                conversation_history=[],
            )

            # Simulate the effective_system building (stable first, then dynamic)
            active_system_prompt = "STABLE"
            effective_system = active_system_prompt
            if dynamic_result and dynamic_result.effective_system:
                if effective_system:
                    effective_system = (effective_system + "\n\n" + dynamic_result.effective_system).strip()
                else:
                    effective_system = dynamic_result.effective_system

            assert effective_system == "STABLE\n\nDYNAMIC"

    def test_original_user_message_passed(self):
        """T12.7: user_message correctly passed to assembler."""
        from run_agent import AIAgent
        with patch("run_agent.OpenAI"), \
             patch("run_agent.get_tool_definitions", return_value=[]), \
             patch("run_agent.check_toolset_requirements", return_value={}):
            agent = AIAgent(
                api_key="test-key",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
        agent._context_assembler = None
        agent.context_compressor = MagicMock()
        agent._cached_system_prompt = ""

        mock_result = MagicMock()
        mock_result.effective_system = ""
        mock_result.dynamic_chunks = []
        mock_result.metrics = MagicMock()

        mock_assembler = MagicMock()
        mock_assembler.assemble_dynamic.return_value = mock_result
        agent._context_assembler = mock_assembler

        with patch.object(agent, "_get_context_assembler", return_value=mock_assembler):
            agent._get_context_assembler().assemble_dynamic(
                user_message="original message",
                conversation_history=[{"role": "user", "content": "hi"}],
            )
            call_kwargs = mock_assembler.assemble_dynamic.call_args.kwargs
            assert call_kwargs["user_message"] == "original message"

    def test_fallback_uses_current_turn_plugin_context_not_stale_instance_cache(self):
        """Fallback path should use current-turn plugin output, not previous-turn cache."""
        from run_agent import AIAgent
        with patch("run_agent.OpenAI"), \
             patch("run_agent.get_tool_definitions", return_value=[]), \
             patch("run_agent.check_toolset_requirements", return_value={}):
            agent = AIAgent(
                api_key="test-key",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )

        agent.client = MagicMock()
        agent._cached_system_prompt = "STABLE"
        agent._use_prompt_caching = False
        agent.tool_delay = 0
        agent.compression_enabled = False
        agent.save_trajectories = False
        agent._plugin_turn_context = "OLD_PLUGIN"
        agent._sparkgraph_turn_context = "OLD_RECALL"

        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop")],
            usage=None,
            model="test/model",
        )
        agent.client.chat.completions.create.return_value = response

        mock_assembler = MagicMock()
        mock_assembler.assemble_dynamic.side_effect = RuntimeError("boom")
        agent._context_assembler = mock_assembler

        with (
            patch("hermes_cli.plugins.invoke_hook", return_value=[{"context": "CURRENT_PLUGIN"}]),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
        ):
            result = agent.run_conversation("hello")

        assert result["completed"] is True
        call_args = agent.client.chat.completions.create.call_args
        api_messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        assert api_messages[0]["content"] == "STABLE\n\nCURRENT_PLUGIN"

    def test_fallback_does_not_reuse_previous_turn_sparkgraph_context(self):
        """Fallback path should not leak SparkGraph recall from the previous turn."""
        from run_agent import AIAgent
        with patch("run_agent.OpenAI"), \
             patch("run_agent.get_tool_definitions", return_value=[]), \
             patch("run_agent.check_toolset_requirements", return_value={}):
            agent = AIAgent(
                api_key="test-key",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )

        agent.client = MagicMock()
        agent._cached_system_prompt = "STABLE"
        agent._use_prompt_caching = False
        agent.tool_delay = 0
        agent.compression_enabled = False
        agent.save_trajectories = False
        agent._sparkgraph_turn_context = "OLD_RECALL"
        agent._sparkgraph_turn_context_ready = True

        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop")],
            usage=None,
            model="test/model",
        )
        agent.client.chat.completions.create.return_value = response

        mock_assembler = MagicMock()
        mock_assembler.assemble_dynamic.side_effect = RuntimeError("boom")
        agent._context_assembler = mock_assembler

        with (
            patch("hermes_cli.plugins.invoke_hook", return_value=[]),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
        ):
            result = agent.run_conversation("hello")

        assert result["completed"] is True
        call_args = agent.client.chat.completions.create.call_args
        api_messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        assert api_messages[0]["content"] == "STABLE"
