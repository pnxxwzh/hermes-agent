"""Tests for context_engine.compat prompt_builder wrappers."""

import pytest
from unittest.mock import patch

from agent.context_engine.compat import (
    wrap_load_soul_md,
    wrap_build_context_files_prompt,
    wrap_build_skills_system_prompt,
)


class TestWrapLoadSoulMd:
    """T5a: wrap_load_soul_md."""

    def test_wrap_load_soul_md_returns_tuple(self):
        """T5a.1: returns (str, str|None)."""
        result = wrap_load_soul_md()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert result[1] is None or isinstance(result[1], str)

    def test_wrap_load_soul_md_none_becomes_empty(self):
        """T5a.2: load_soul_md() returns None -> content="". """
        with patch("agent.prompt_builder.load_soul_md", return_value=None):
            content, err = wrap_load_soul_md()
            assert content == ""
            assert err is None

    def test_wrap_load_soul_md_normal(self):
        """T5a.x: normal call returns content."""
        with patch("agent.prompt_builder.load_soul_md", return_value="you are hermes"):
            content, err = wrap_load_soul_md()
            assert content == "you are hermes"
            assert err is None

    def test_wrap_load_soul_md_exception_caught(self):
        """T5a.x: exception caught, returns error string."""
        with patch("agent.prompt_builder.load_soul_md", side_effect=RuntimeError("soul failed")):
            content, err = wrap_load_soul_md()
            assert content == ""
            assert err == "soul failed"


class TestWrapBuildContextFilesPrompt:
    """T5a: wrap_build_context_files_prompt."""

    def test_wrap_build_context_files_prompt_returns_tuple(self):
        """T5a.x: returns (str, str|None)."""
        result = wrap_build_context_files_prompt(cwd="/tmp")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_wrap_build_context_files_prompt_exception_caught(self):
        """T5a.3: exception caught, returns error str, not thrown."""
        with patch("agent.prompt_builder.build_context_files_prompt", side_effect=OSError("path error")):
            content, err = wrap_build_context_files_prompt(cwd="/tmp")
            assert content == ""
            assert err == "path error"

    def test_wrap_build_context_files_prompt_normal(self):
        """T5a.x: normal call returns content."""
        with patch(
            "agent.prompt_builder.build_context_files_prompt",
            return_value="# Context files",
        ):
            content, err = wrap_build_context_files_prompt(cwd="/tmp")
            assert content == "# Context files"
            assert err is None

    def test_wrap_build_context_files_prompt_skip_soul(self):
        """T5a.x: skip_soul parameter passed through."""
        with patch(
            "agent.prompt_builder.build_context_files_prompt",
            return_value="",
        ) as mock_fn:
            wrap_build_context_files_prompt(cwd="/tmp", skip_soul=True)
            mock_fn.assert_called_once_with(cwd="/tmp", skip_soul=True)


class TestWrapBuildSkillsSystemPrompt:
    """T5a: wrap_build_skills_system_prompt."""

    def test_wrap_build_skills_system_prompt_returns_tuple(self):
        """T5a.x: returns (str, str|None)."""
        result = wrap_build_skills_system_prompt([], set())
        assert isinstance(result, tuple)

    def test_wrap_build_skills_system_prompt_normal(self):
        """T5a.4: normal call returns content."""
        skills_content = "# Skills index"
        with patch(
            "agent.prompt_builder.build_skills_system_prompt",
            return_value=skills_content,
        ):
            content, err = wrap_build_skills_system_prompt(["skills_list"], set())
            assert content == skills_content
            assert err is None

    def test_wrap_build_skills_system_prompt_none_becomes_empty(self):
        """T5a.x: returned None -> "". """
        with patch(
            "agent.prompt_builder.build_skills_system_prompt",
            return_value=None,
        ):
            content, err = wrap_build_skills_system_prompt([], set())
            assert content == ""
            assert err is None

    def test_wrap_build_skills_system_prompt_exception_caught(self):
        """T5a.x: exception caught."""
        with patch(
            "agent.prompt_builder.build_skills_system_prompt",
            side_effect=ValueError("bad tools"),
        ):
            content, err = wrap_build_skills_system_prompt([], set())
            assert content == ""
            assert err == "bad tools"
