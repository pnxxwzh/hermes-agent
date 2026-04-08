import json

from tools.budget_config import BudgetConfig
from tools.tool_result_storage import (
    PERSISTED_OUTPUT_CLOSING_TAG,
    PERSISTED_OUTPUT_TAG,
    enforce_turn_budget,
    generate_preview,
    is_persisted_output,
    maybe_persist_tool_result,
)


class _Env:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.commands = []

    def execute(self, command, timeout=30):
        self.commands.append((command, timeout))
        return {"returncode": self.returncode}


def test_generate_preview_preserves_newline_boundary():
    preview, has_more = generate_preview("line-1\nline-2\nline-3", max_chars=15)
    assert preview.endswith("\n")
    assert has_more is True


def test_maybe_persist_tool_result_persists_large_output_to_env():
    env = _Env()
    content = "A" * 5000
    replacement, state = maybe_persist_tool_result(
        content=content,
        tool_name="web_search",
        tool_use_id="call_1",
        env=env,
        config=BudgetConfig(default_result_size=1000, preview_size=100),
    )
    assert state == "persisted_preview"
    assert PERSISTED_OUTPUT_TAG in replacement
    assert PERSISTED_OUTPUT_CLOSING_TAG in replacement
    assert "/tmp/hermes-results/call_1.txt" in replacement
    assert env.commands


def test_maybe_persist_tool_result_preserves_structured_content_in_persisted_payload():
    env = _Env()
    content = json.dumps(
        {
            "result": "A" * 5000,
            "structuredContent": {"items": [1, 2, 3], "meta": {"source": "mcp"}},
        }
    )
    replacement, state = maybe_persist_tool_result(
        content=content,
        tool_name="mcp_tool",
        tool_use_id="call_structured",
        env=env,
        config=BudgetConfig(default_result_size=1000, preview_size=120),
    )
    assert state == "persisted_preview"
    assert env.commands
    persisted_command, _timeout = env.commands[0]
    assert '"structuredContent"' in persisted_command
    assert '"items": [1, 2, 3]' in persisted_command
    assert PERSISTED_OUTPUT_TAG in replacement


def test_maybe_persist_tool_result_falls_back_to_inline_truncation():
    content = "A" * 5000
    replacement, state = maybe_persist_tool_result(
        content=content,
        tool_name="web_search",
        tool_use_id="call_1",
        env=None,
        config=BudgetConfig(default_result_size=1000, preview_size=100),
    )
    assert state == "inline"
    assert "Truncated" in replacement
    assert PERSISTED_OUTPUT_TAG not in replacement


def test_is_persisted_output_detects_wrapped_content():
    assert is_persisted_output(f"{PERSISTED_OUTPUT_TAG}\nhello\n{PERSISTED_OUTPUT_CLOSING_TAG}")
    assert not is_persisted_output("plain")


def test_enforce_turn_budget_persists_largest_message_first():
    env = _Env()
    tool_messages = [
        {"role": "tool", "tool_call_id": "small", "content": "a" * 200},
        {"role": "tool", "tool_call_id": "large", "content": "b" * 1200},
    ]
    updated, states = enforce_turn_budget(
        tool_messages,
        env=env,
        config=BudgetConfig(default_result_size=5000, turn_budget=700, preview_size=80),
        persistence_by_tool_call_id={"small": "inline", "large": "inline"},
    )
    assert states["large"] == "persisted_budget"
    assert is_persisted_output(updated[1]["content"])


def test_enforce_turn_budget_skips_existing_persisted_messages():
    env = _Env()
    persisted, _ = maybe_persist_tool_result(
        content="A" * 5000,
        tool_name="web_search",
        tool_use_id="call_1",
        env=env,
        config=BudgetConfig(default_result_size=1000, preview_size=100),
    )
    updated, states = enforce_turn_budget(
        [{"role": "tool", "tool_call_id": "call_1", "content": persisted}],
        env=env,
        config=BudgetConfig(default_result_size=1000, turn_budget=10, preview_size=50),
        persistence_by_tool_call_id={"call_1": "persisted_preview"},
    )
    assert updated[0]["content"] == persisted
    assert states["call_1"] == "persisted_preview"


def test_read_file_is_never_persisted():
    env = _Env()
    replacement, state = maybe_persist_tool_result(
        content="A" * 5000,
        tool_name="read_file",
        tool_use_id="call_1",
        env=env,
        config=BudgetConfig(default_result_size=100),
    )
    assert replacement == "A" * 5000
    assert state == "inline"
