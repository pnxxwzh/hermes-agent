"""Tests for context_engine.sources stable 1-5."""

import pytest
from unittest.mock import patch, MagicMock

from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import ContextChunk
from agent.context_engine.sources import (
    IdentitySource,
    ToolGuidanceSource,
    ToolUseEnforcementSource,
    HonchoStaticSource,
    SystemMessageSource,
)


class TestIdentitySource:
    """T6: IdentitySource."""

    def test_identity_source_with_soul(self):
        """T6.1: with SOUL.md, slot="soul", has_soul=True."""
        with patch(
            "agent.context_engine.sources.wrap_load_soul_md",
            return_value=("you are my agent", None),
        ):
            src = IdentitySource()
            chunks = src.collect(AssemblyContext(agent=MagicMock()))
            assert len(chunks) == 1
            assert chunks[0].slot == "soul"
            assert chunks[0].metadata["has_soul"] is True
            assert chunks[0].content == "you are my agent"

    def test_identity_source_without_soul(self):
        """T6.2: without SOUL.md, slot="default"."""
        with patch("agent.context_engine.sources.wrap_load_soul_md", return_value=("", None)):
            src = IdentitySource()
            chunks = src.collect(AssemblyContext(agent=MagicMock()))
            assert len(chunks) == 1
            assert chunks[0].slot == "default"
            assert chunks[0].metadata["has_soul"] is False
            assert chunks[0].content == "You are Hermes Agent"

    def test_identity_source_ai_peer_substitution(self):
        """T6.3: ai_peer_name replaces default identity."""
        with patch("agent.context_engine.sources.wrap_load_soul_md", return_value=("", None)):
            src = IdentitySource(ai_peer_name="MyBot")
            chunks = src.collect(AssemblyContext(agent=MagicMock()))
            assert chunks[0].content == "You are MyBot"

    def test_identity_source_default_identity_custom(self):
        """T6.x: custom default_identity used."""
        with patch("agent.context_engine.sources.wrap_load_soul_md", return_value=("", None)):
            src = IdentitySource(default_identity="Custom Agent")
            chunks = src.collect(AssemblyContext(agent=MagicMock()))
            assert chunks[0].content == "Custom Agent"

    def test_identity_source_content_not_empty(self):
        """T6.x: content is never empty (falls back to default)."""
        with patch("agent.context_engine.sources.wrap_load_soul_md", return_value=("", None)):
            src = IdentitySource()
            chunks = src.collect(AssemblyContext(agent=MagicMock()))
            assert chunks[0].content != ""


class TestToolGuidanceSource:
    """T6: ToolGuidanceSource."""

    def test_tool_guidance_source_empty(self):
        """T6.4: tool_names=[] returns []. """
        src = ToolGuidanceSource(valid_tool_names=[])
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_tool_guidance_source_memory_only(self):
        """T6.5: memory tool only -> one guidance string."""
        src = ToolGuidanceSource(valid_tool_names=["memory"])
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1
        assert "memory" in chunks[0].content.lower()
        assert "session_search" not in chunks[0].content
        assert "skill" not in chunks[0].content.lower()

    def test_tool_guidance_source_all_three(self):
        """T6.6: all three tools -> three guidance strings joined."""
        src = ToolGuidanceSource(
            valid_tool_names=["memory", "session_search", "skill_manage"]
        )
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1
        assert "memory" in chunks[0].content.lower()
        assert "session_search" in chunks[0].content.lower()
        assert "skill" in chunks[0].content.lower()

    def test_tool_guidance_source_skills_list(self):
        """T6.x: skills_list also triggers skills guidance."""
        src = ToolGuidanceSource(valid_tool_names=["skills_list"])
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1
        assert "skill" in chunks[0].content.lower()


class TestToolUseEnforcementSource:
    """T6: ToolUseEnforcementSource."""

    def test_tool_use_enforcement_true(self):
        """T6.x: True always injects."""
        src = ToolUseEnforcementSource(True, model="claude")
        assert src._should_inject() is True

    def test_tool_use_enforcement_false(self):
        """T6.9: False never injects."""
        src = ToolUseEnforcementSource(False, model="gpt-4")
        assert src._should_inject() is False

    def test_tool_use_enforcement_auto_match(self):
        """T6.7: "auto" + matching model -> inject."""
        src = ToolUseEnforcementSource("auto", model="gpt-4")
        assert src._should_inject() is True

    def test_tool_use_enforcement_auto_no_match(self):
        """T6.x: "auto" + non-matching model -> no inject."""
        src = ToolUseEnforcementSource("auto", model="claude-3")
        assert src._should_inject() is False

    def test_tool_use_enforcement_list_match(self):
        """T6.9: list match -> inject."""
        src = ToolUseEnforcementSource(["custom-model"], model="custom-model-abc")
        assert src._should_inject() is True

    def test_tool_use_enforcement_list_no_match(self):
        """T6.10: list no match -> no inject."""
        src = ToolUseEnforcementSource(["gpt-4"], model="claude-3")
        assert src._should_inject() is False

    def test_tool_use_enforcement_string_always(self):
        """T6.x: "always" string -> inject."""
        src = ToolUseEnforcementSource("always", model="anything")
        assert src._should_inject() is True

    def test_tool_use_enforcement_inject_returns_chunk(self):
        """T6.x: should_inject=True -> returns chunk."""
        src = ToolUseEnforcementSource(True, model="gpt-4")
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1
        assert chunks[0].source == "tool_use_enforcement"

    def test_tool_use_enforcement_no_inject_returns_empty(self):
        """T6.x: should_inject=False -> returns []. """
        src = ToolUseEnforcementSource(False, model="claude")
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []


class TestHonchoStaticSource:
    """T6: HonchoStaticSource."""

    def test_honcho_static_no_manager(self):
        """T6.11: manager=None -> []. """
        src = HonchoStaticSource()
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_honcho_static_with_manager(self):
        """T6.12: with manager -> returns content."""
        mock_config = MagicMock()
        mock_config.memory_mode = "hybrid"
        mock_config.write_frequency = "async"
        mock_config.recall_mode = "hybrid"
        src = HonchoStaticSource(
            honcho_session_manager=MagicMock(),
            honcho_config=mock_config,
        )
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1
        assert chunks[0].source == "honcho_static"
        assert "Honcho" in chunks[0].content

    def test_honcho_static_slot(self):
        """T6.x: slot is "honcho"."""
        mock_config = MagicMock()
        mock_config.memory_mode = "local"
        mock_config.write_frequency = "sync"
        mock_config.recall_mode = "tools"
        src = HonchoStaticSource(
            honcho_session_manager=MagicMock(),
            honcho_config=mock_config,
        )
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks[0].slot == "honcho"


class TestSystemMessageSource:
    """T6: SystemMessageSource."""

    def test_system_message_none(self):
        """T6.13: system_message=None -> []. """
        src = SystemMessageSource()
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_system_message_empty_string(self):
        """T6.x: system_message="" -> []. """
        src = SystemMessageSource("")
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_system_message_with_content(self):
        """T6.x: with content -> returns chunk."""
        src = SystemMessageSource("custom system prompt")
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1
        assert chunks[0].content == "custom system prompt"
        assert chunks[0].source == "system_message"
