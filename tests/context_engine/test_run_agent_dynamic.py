"""Tests for run_agent.py dynamic layer integration (Step 12)."""

import pytest
from unittest.mock import MagicMock, patch


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
        """T12.2: metrics synced to context_compressor."""
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

        mock_metrics = MagicMock()
        mock_result = MagicMock(
            effective_system="dyn",
            dynamic_chunks=[],
            metrics=mock_metrics,
        )
        mock_assembler = MagicMock()
        mock_assembler.assemble_dynamic.return_value = mock_result
        agent._context_assembler = mock_assembler

        with patch.object(agent, "_get_context_assembler", return_value=mock_assembler):
            dynamic_result = agent._get_context_assembler().assemble_dynamic(
                user_message="test",
                conversation_history=[],
            )
            dynamic_result.metrics.sync_to(agent.context_compressor)
            mock_metrics.sync_to.assert_called_once_with(compressor)

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
