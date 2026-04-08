from types import SimpleNamespace
from unittest.mock import patch


def _make_agent():
    from run_agent import AIAgent

    with (
        patch("run_agent.OpenAI"),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
    ):
        return AIAgent(
            api_key="test-key",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )


def _tool_call(name: str, call_id: str, arguments: str = "{}"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class _Env:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.commands = []

    def execute(self, command, timeout=30):
        self.commands.append(command)
        return {"returncode": self.returncode}


def test_sequential_tool_execution_persists_large_results_before_append():
    agent = _make_agent()
    agent._tool_persistence_section = {
        "enabled": True,
        "default_result_size_chars": 1000,
        "turn_budget_chars": 200000,
        "preview_size_chars": 120,
    }
    assistant = SimpleNamespace(tool_calls=[_tool_call("web_search", "call_1")])
    messages = []
    env = _Env()

    with (
        patch("run_agent.handle_function_call", return_value="A" * 5000),
        patch.object(agent, "_get_tool_persistence_env", return_value=env),
    ):
        agent._execute_tool_calls_sequential(assistant, messages, "task-1")

    assert len(messages) == 1
    assert messages[0]["tool_call_id"] == "call_1"
    assert "<persisted-output>" in messages[0]["content"]
    assert env.commands


def test_concurrent_tool_execution_enforces_turn_budget_before_append():
    agent = _make_agent()
    assistant = SimpleNamespace(
        tool_calls=[
            _tool_call("web_search", "call_1", '{"q":"alpha"}'),
            _tool_call("web_search", "call_2", '{"q":"beta"}'),
        ]
    )
    messages = []
    env = _Env()
    agent._tool_persistence_section = {
        "enabled": True,
        "default_result_size_chars": 100000,
        "turn_budget_chars": 700,
        "preview_size_chars": 80,
    }

    def _fake_handle(name, args, task_id, **kwargs):
        return ("x" * 1200) if args.get("q") == "alpha" else ("y" * 400)

    with (
        patch("run_agent.handle_function_call", side_effect=_fake_handle),
        patch.object(agent, "_get_tool_persistence_env", return_value=env),
    ):
        agent._execute_tool_calls_concurrent(assistant, messages, "task-1")

    assert len(messages) == 2
    assert messages[0]["tool_call_id"] == "call_1"
    assert "<persisted-output>" in messages[0]["content"]
    assert messages[1]["tool_call_id"] == "call_2"


def test_persistence_failure_falls_back_to_inline_truncation():
    agent = _make_agent()
    agent._tool_persistence_section = {
        "enabled": True,
        "default_result_size_chars": 1000,
        "turn_budget_chars": 200000,
        "preview_size_chars": 120,
    }
    assistant = SimpleNamespace(tool_calls=[_tool_call("web_search", "call_1")])
    messages = []
    env = _Env(returncode=1)

    with (
        patch("run_agent.handle_function_call", return_value="A" * 5000),
        patch.object(agent, "_get_tool_persistence_env", return_value=env),
    ):
        agent._execute_tool_calls_sequential(assistant, messages, "task-1")

    assert len(messages) == 1
    assert "<persisted-output>" not in messages[0]["content"]
    assert "Truncated" in messages[0]["content"]
