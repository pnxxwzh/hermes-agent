"""Tests for CLI session-boundary plugin hooks."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call


def _make_cli():
    clean_config = {
        "model": {
            "default": "anthropic/claude-opus-4.6",
            "base_url": "https://openrouter.ai/api/v1",
            "provider": "auto",
        },
        "display": {"compact": False, "tool_progress": "all"},
        "agent": {},
        "terminal": {"env_type": "local"},
    }
    clean_env = {"LLM_MODEL": "", "HERMES_MAX_ITERATIONS": ""}
    prompt_toolkit_stubs = {
        "prompt_toolkit": MagicMock(),
        "prompt_toolkit.history": MagicMock(),
        "prompt_toolkit.styles": MagicMock(),
        "prompt_toolkit.patch_stdout": MagicMock(),
        "prompt_toolkit.application": MagicMock(),
        "prompt_toolkit.layout": MagicMock(),
        "prompt_toolkit.layout.processors": MagicMock(),
        "prompt_toolkit.filters": MagicMock(),
        "prompt_toolkit.layout.dimension": MagicMock(),
        "prompt_toolkit.layout.menus": MagicMock(),
        "prompt_toolkit.widgets": MagicMock(),
        "prompt_toolkit.key_binding": MagicMock(),
        "prompt_toolkit.completion": MagicMock(),
        "prompt_toolkit.formatted_text": MagicMock(),
        "prompt_toolkit.auto_suggest": MagicMock(),
    }
    with patch.dict(sys.modules, prompt_toolkit_stubs), patch.dict(
        "os.environ", clean_env, clear=False
    ):
        import cli as cli_mod

        cli_mod = importlib.reload(cli_mod)
        with patch.object(cli_mod, "get_tool_definitions", return_value=[]), patch.dict(
            cli_mod.__dict__, {"CLI_CONFIG": clean_config}
        ):
            return cli_mod.HermesCLI()


def _fake_agent(session_id: str):
    agent = MagicMock()
    agent.session_id = session_id
    agent.model = "anthropic/claude-opus-4.6"
    agent.platform = "cli"
    agent.flush_memories = MagicMock()
    agent.reset_session_state = MagicMock()
    agent._invalidate_system_prompt = MagicMock()
    return agent


def test_session_hooks_are_valid():
    from hermes_cli.plugins import VALID_HOOKS

    assert "on_session_finalize" in VALID_HOOKS
    assert "on_session_reset" in VALID_HOOKS


@patch("hermes_cli.plugins.invoke_hook")
def test_new_session_fires_finalize_then_reset(mock_invoke_hook):
    cli = _make_cli()
    old_session_id = cli.session_id
    cli.agent = _fake_agent(old_session_id)
    cli.conversation_history = [{"role": "user", "content": "hello"}]

    cli.new_session(silent=True)

    assert mock_invoke_hook.mock_calls[:2] == [
        call("on_session_finalize", session_id=old_session_id, platform="cli"),
        call("on_session_reset", session_id=cli.session_id, platform="cli"),
    ]


@patch("hermes_cli.plugins.invoke_hook")
def test_exit_hooks_fire_finalize_and_interrupt_end(mock_invoke_hook):
    cli = _make_cli()
    cli.agent = _fake_agent("exit-session")
    cli._agent_running = True

    cli._run_session_exit_hooks()

    assert mock_invoke_hook.mock_calls == [
        call(
            "on_session_end",
            session_id="exit-session",
            completed=False,
            interrupted=True,
            model="anthropic/claude-opus-4.6",
            platform="cli",
        ),
        call("on_session_finalize", session_id="exit-session", platform="cli"),
    ]


@patch("hermes_cli.plugins.invoke_hook", side_effect=RuntimeError("hook failed"))
def test_session_boundary_hook_failures_do_not_break_cli_flow(_mock_invoke_hook):
    cli = _make_cli()
    cli.agent = _fake_agent(cli.session_id)
    cli.conversation_history = [{"role": "user", "content": "hello"}]

    cli.new_session(silent=True)
    cli._run_session_exit_hooks()
