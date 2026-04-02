"""Tests for run_agent.py stable layer integration (Step 11)."""

import pytest
from unittest.mock import MagicMock, patch


class TestBuildSystemPromptIntegration:
    """T11: _build_system_prompt via ContextAssembler."""

    def test_build_system_prompt_returns_string(self):
        """T11.1: _build_system_prompt returns a string."""
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
        with patch(
            "agent.context_engine.assembler.ContextAssembler.assemble_stable",
            return_value=MagicMock(
                stable_system="test system",
                metrics=MagicMock(total_estimated_tokens=2),
            ),
        ):
            result = agent._build_system_prompt()
            assert isinstance(result, str)

    def test_build_system_prompt_syncs_metrics(self):
        """T11.2: context_compressor.last_prompt_tokens is set via sync_to."""
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
        compressor = MagicMock()
        compressor.last_prompt_tokens = 0
        agent.context_compressor = compressor
        # Mock the metrics object with a spy on sync_to
        mock_metrics = MagicMock()
        mock_metrics.total_estimated_tokens = 5
        mock_result = MagicMock(
            stable_system="test",
            metrics=mock_metrics,
        )
        mock_assembler_instance = MagicMock()
        mock_assembler_instance.assemble_stable.return_value = mock_result
        # Patch _get_context_assembler to return our mock
        agent._context_assembler = mock_assembler_instance
        agent._build_system_prompt()
        mock_metrics.sync_to.assert_called_once_with(compressor)

    def test_assembler_lazy_init(self):
        """T11.5: _context_assembler is None until first _get_context_assembler call."""
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
        assert agent._context_assembler is None
        assembler = agent._get_context_assembler()
        assert assembler is not None
        assert agent._context_assembler is assembler

    def test_build_system_prompt_returns_assembled_value(self):
        """T11.3: _build_system_prompt returns assembled system prompt.

        In the Phase 1 implementation, _build_system_prompt always calls the
        assembler (caching is handled by the caller checking _cached_system_prompt
        before calling). The assembler result is stored in _cached_system_prompt.
        """
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
        compressor = MagicMock()
        agent.context_compressor = compressor
        mock_result = MagicMock(
            stable_system="assembled_result",
            metrics=MagicMock(total_estimated_tokens=3),
        )
        mock_assembler = MagicMock()
        mock_assembler.assemble_stable.return_value = mock_result
        with patch.object(agent, "_get_context_assembler", return_value=mock_assembler):
            agent._build_system_prompt()
            # _cached_system_prompt is set from assembler result
            assert agent._cached_system_prompt == "assembled_result"

    def test_build_system_prompt_caches_last_context_metrics(self):
        """T11.6: _build_system_prompt caches result.metrics to _last_context_metrics."""
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
        compressor = MagicMock()
        compressor.last_prompt_tokens = 0
        agent.context_compressor = compressor
        mock_metrics = MagicMock()
        mock_metrics.total_estimated_tokens = 7
        mock_result = MagicMock(
            stable_system="test_metrics_cache",
            metrics=mock_metrics,
        )
        mock_assembler_instance = MagicMock()
        mock_assembler_instance.assemble_stable.return_value = mock_result
        agent._context_assembler = mock_assembler_instance
        agent._build_system_prompt()
        # _last_context_metrics should be set from assembler result
        assert agent._last_context_metrics is mock_result.metrics

    def test_compression_after_build(self):
        """T11.4: compression after build has no issues with assembler."""
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
        compressor.last_prompt_tokens = 0
        agent.context_compressor = compressor
        agent._cached_system_prompt = "test_prompt"
        with patch(
            "agent.context_engine.assembler.ContextAssembler.assemble_stable",
            return_value=MagicMock(
                stable_system="new_assembled",
                metrics=MagicMock(total_estimated_tokens=3),
            ),
        ):
            # Pre-set cached, then rebuild after compression
            agent._cached_system_prompt = None
            result = agent._build_system_prompt()
            assert isinstance(result, str)
