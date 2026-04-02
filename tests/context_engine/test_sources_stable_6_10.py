"""Tests for context_engine.sources stable 6-10."""

import pytest
from unittest.mock import patch, MagicMock

from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import ContextChunk
from agent.context_engine.sources import (
    MemorySource,
    UserProfileSource,
    SkillsSource,
    ProjectContextSource,
    TimePlatformSource,
)


class TestMemorySource:
    """T7: MemorySource."""

    def test_memory_source_disabled(self):
        """T7.1: memory_enabled=False -> []. """
        src = MemorySource(memory_enabled=False)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_memory_source_no_store(self):
        """T7.2: store=None -> []. """
        src = MemorySource(memory_store=None)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_memory_source_with_content(self):
        """T7.3: normal returns content."""
        mock_store = MagicMock()
        mock_store.format_for_system_prompt.return_value = "# Memory entries"
        src = MemorySource(memory_store=mock_store)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1
        assert chunks[0].content == "# Memory entries"
        mock_store.format_for_system_prompt.assert_called_once_with("memory")

    def test_memory_source_empty_content(self):
        """T7.x: store returns "" -> []. """
        mock_store = MagicMock()
        mock_store.format_for_system_prompt.return_value = ""
        src = MemorySource(memory_store=mock_store)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []


class TestUserProfileSource:
    """T7: UserProfileSource."""

    def test_user_profile_disabled(self):
        """T7.4: user_profile_enabled=False -> []. """
        src = UserProfileSource(user_profile_enabled=False)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_user_profile_no_store(self):
        """T7.x: store=None -> []. """
        src = UserProfileSource(memory_store=None)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_user_profile_with_content(self):
        """T7.x: normal returns content."""
        mock_store = MagicMock()
        mock_store.format_for_system_prompt.return_value = "# User profile"
        src = UserProfileSource(memory_store=mock_store)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1
        assert chunks[0].content == "# User profile"
        mock_store.format_for_system_prompt.assert_called_once_with("user")

    def test_user_profile_slot(self):
        """T7.x: slot is "user"."""
        mock_store = MagicMock()
        mock_store.format_for_system_prompt.return_value = "# User"
        src = UserProfileSource(memory_store=mock_store)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks[0].slot == "user"


class TestSkillsSource:
    """T7: SkillsSource."""

    def test_skills_source_no_skills_tools(self):
        """T7.5: no skills tools -> []. """
        src = SkillsSource(available_tools=["memory", "file_read"])
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks == []

    def test_skills_source_skills_list(self):
        """T7.x: skills_list triggers collection."""
        src = SkillsSource(available_tools=["skills_list"])
        # Use override fn to avoid actual compat call
        src._build_fn = lambda **kw: "# Skills index"
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1
        assert chunks[0].content == "# Skills index"

    def test_skills_source_skill_view(self):
        """T7.x: skill_view also triggers."""
        src = SkillsSource(available_tools=["skill_view"])
        src._build_fn = lambda **kw: "# Skills"
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1

    def test_skills_source_with_override_fn(self):
        """T7.6: build_fn override used."""
        src = SkillsSource(
            available_tools=["skills_list"],
            build_skills_fn=lambda **kw: "override content",
        )
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert chunks[0].content == "override content"

    def test_skills_source_no_override_uses_compat(self):
        """T7.x: no override -> calls compat wrapper."""
        with patch(
            "agent.context_engine.compat.wrap_build_skills_system_prompt",
            return_value=("# Skills compat", None),
        ):
            src = SkillsSource(available_tools=["skills_list"])
            chunks = src.collect(AssemblyContext(agent=MagicMock()))
            assert chunks[0].content == "# Skills compat"


class TestProjectContextSource:
    """T7: ProjectContextSource."""

    def test_project_context_source_empty(self):
        """T7.7: no context files -> []. """
        with patch(
            "agent.context_engine.compat.wrap_build_context_files_prompt",
            return_value=("", None),
        ):
            src = ProjectContextSource(cwd="/tmp")
            chunks = src.collect(AssemblyContext(agent=MagicMock()))
            assert chunks == []

    def test_project_context_source_cwd_fallback(self):
        """T7.8: cwd=None -> falls back to ctx.cwd."""
        with patch(
            "agent.context_engine.compat.wrap_build_context_files_prompt",
            return_value=("# context", None),
        ) as mock_fn:
            ctx = AssemblyContext(agent=MagicMock(), cwd="/workspace")
            src = ProjectContextSource(cwd=None)
            src.collect(ctx)
            mock_fn.assert_called_once_with(cwd="/workspace", skip_soul=False)

    def test_project_context_source_explicit_cwd(self):
        """T7.x: explicit cwd used over ctx.cwd."""
        with patch(
            "agent.context_engine.compat.wrap_build_context_files_prompt",
            return_value=("# files", None),
        ) as mock_fn:
            ctx = AssemblyContext(agent=MagicMock(), cwd="/workspace")
            src = ProjectContextSource(cwd="/explicit")
            src.collect(ctx)
            mock_fn.assert_called_once_with(cwd="/explicit", skip_soul=False)

    def test_project_context_source_with_content(self):
        """T7.x: content returned correctly."""
        with patch(
            "agent.context_engine.compat.wrap_build_context_files_prompt",
            return_value=("# AGENTS.md content", None),
        ):
            src = ProjectContextSource(cwd="/tmp")
            chunks = src.collect(AssemblyContext(agent=MagicMock()))
            assert chunks[0].content == "# AGENTS.md content"


class TestTimePlatformSource:
    """T7: TimePlatformSource."""

    def test_time_platform_no_model_provider(self):
        """T7.9: only timestamp when no model/provider."""
        src = TimePlatformSource(model=None, provider=None)
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert len(chunks) == 1
        assert "Conversation started:" in chunks[0].content
        assert "Model:" not in chunks[0].content

    def test_time_platform_with_session(self):
        """T7.10: pass_session_id=True shows session ID."""
        src = TimePlatformSource(
            model="gpt-4",
            provider="openai",
            session_id="abc123",
            pass_session_id=True,
        )
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        content = chunks[0].content
        assert "Session ID: abc123" in content
        assert "Model: gpt-4" in content
        assert "Provider: openai" in content

    def test_time_platform_no_session_when_disabled(self):
        """T7.x: pass_session_id=False hides session ID."""
        src = TimePlatformSource(
            session_id="abc123",
            pass_session_id=False,
        )
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert "Session ID" not in chunks[0].content

    def test_time_platform_platform_hint_telegram(self):
        """T7.11: telegram -> adds hint."""
        src = TimePlatformSource(platform="telegram")
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert "Telegram" in chunks[0].content

    def test_time_platform_platform_hint_discord(self):
        """T7.x: discord -> adds hint."""
        src = TimePlatformSource(platform="discord")
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert "Discord" in chunks[0].content

    def test_time_platform_unknown_platform(self):
        """T7.12: unknown platform -> no hint."""
        src = TimePlatformSource(platform="unknown_platform")
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        assert "You are communicating via" not in chunks[0].content

    def test_time_platform_all_fields(self):
        """T7.x: all fields present."""
        src = TimePlatformSource(
            model="claude-3",
            provider="anthropic",
            session_id="sess999",
            platform="slack",
            pass_session_id=True,
        )
        chunks = src.collect(AssemblyContext(agent=MagicMock()))
        content = chunks[0].content
        assert "Model: claude-3" in content
        assert "Provider: anthropic" in content
        assert "Session ID: sess999" in content
        assert "Slack" in content
