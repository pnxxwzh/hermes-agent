"""Focused tests for prompt-builder tool guidance constants."""

from agent.prompt_builder import OPENAI_CODEX_TOOL_USE_GUIDANCE


def test_openai_codex_tool_guidance_contains_mandatory_tool_use_block():
    assert "<mandatory_tool_use>" in OPENAI_CODEX_TOOL_USE_GUIDANCE


def test_openai_codex_tool_guidance_contains_act_dont_ask_block():
    assert "<act_dont_ask>" in OPENAI_CODEX_TOOL_USE_GUIDANCE
