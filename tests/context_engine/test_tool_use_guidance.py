"""Focused tests for model-specific tool-use guidance injection."""

from unittest.mock import MagicMock

from agent.context_engine.context import AssemblyContext
from agent.context_engine.sources import ToolUseEnforcementSource


def test_gpt_models_receive_openai_codex_guidance_extensions():
    chunks = ToolUseEnforcementSource(True, model="openai/gpt-4.1").collect(
        AssemblyContext(agent=MagicMock())
    )
    assert len(chunks) == 1
    assert chunks[0].source == "tool_use_enforcement"
    assert "<mandatory_tool_use>" in chunks[0].content


def test_non_gpt_models_keep_base_tool_enforcement_without_extensions():
    chunks = ToolUseEnforcementSource(True, model="anthropic/claude-sonnet-4").collect(
        AssemblyContext(agent=MagicMock())
    )
    assert len(chunks) == 1
    assert "<mandatory_tool_use>" not in chunks[0].content
