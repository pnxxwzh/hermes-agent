"""Gateway inactivity timeout and staged warning regression tests."""

import asyncio
import threading
import time
from unittest.mock import patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import (
    GatewayInactivityPolicy,
    GatewayRunner,
    _AGENT_PENDING_SENTINEL,
    _resolve_gateway_inactivity_policy,
)
from gateway.session import SessionSource


class _FakeAdapter:
    def __init__(self):
        self.sent_messages = []
        self._pending_messages = {}

    async def send(self, chat_id, text, **kwargs):
        self.sent_messages.append((chat_id, text, kwargs))


class _FakeIdleAgent:
    def __init__(self, idle_seconds=0.0, desc="tool_call", current_tool=None):
        self.idle_seconds = idle_seconds
        self.desc = desc
        self.current_tool = current_tool
        self.interrupt_message = None

    def get_activity_summary(self):
        return {
            "seconds_since_activity": self.idle_seconds,
            "last_activity_desc": self.desc,
            "current_tool": self.current_tool,
            "api_call_count": 3,
            "max_iterations": 90,
        }

    def interrupt(self, msg):
        self.interrupt_message = msg


class _TrackerlessAgent:
    interrupt_message = None

    def interrupt(self, msg):
        self.interrupt_message = msg


def _make_runner():
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    runner.adapters = {Platform.TELEGRAM: _FakeAdapter()}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._voice_mode = {}
    runner._background_tasks = set()
    runner._is_user_authorized = lambda _source: True
    return runner


def _make_source(chat_id="12345"):
    return SessionSource(platform=Platform.TELEGRAM, chat_id=chat_id, chat_type="dm")


def _make_event(text="hello", chat_id="12345"):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=_make_source(chat_id),
    )


def test_resolve_gateway_inactivity_policy_defaults(monkeypatch):
    monkeypatch.delenv("HERMES_AGENT_TIMEOUT", raising=False)
    monkeypatch.delenv("HERMES_AGENT_TIMEOUT_WARNING", raising=False)

    policy = _resolve_gateway_inactivity_policy()

    assert policy.hard_timeout_seconds == 1800.0
    assert policy.warning_timeout_seconds == 900.0
    assert policy.stale_eviction_wall_ttl_seconds == 18000.0


def test_resolve_gateway_inactivity_policy_disables_warning_for_unlimited(monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_TIMEOUT", "0")
    monkeypatch.setenv("HERMES_AGENT_TIMEOUT_WARNING", "900")

    policy = _resolve_gateway_inactivity_policy()

    assert policy.hard_timeout_seconds is None
    assert policy.warning_timeout_seconds is None
    assert policy.stale_eviction_wall_ttl_seconds == float("inf")


def test_stale_running_agent_eviction_uses_idle_but_preserves_fresh_sentinel():
    runner = _make_runner()
    policy = GatewayInactivityPolicy(
        hard_timeout_seconds=30.0,
        warning_timeout_seconds=15.0,
        stale_eviction_wall_ttl_seconds=300.0,
    )

    runner._running_agents["fresh"] = _AGENT_PENDING_SENTINEL
    runner._running_agents_ts["fresh"] = time.time() - 2.0
    assert not runner._maybe_evict_stale_running_agent("fresh", policy)
    assert "fresh" in runner._running_agents

    stale_agent = _FakeIdleAgent(idle_seconds=45.0, current_tool="browser_snapshot")
    runner._running_agents["stale"] = stale_agent
    runner._running_agents_ts["stale"] = time.time() - 120.0
    assert runner._maybe_evict_stale_running_agent("stale", policy)
    assert "stale" not in runner._running_agents
    assert "stale" not in runner._running_agents_ts


@pytest.mark.asyncio
async def test_handle_message_eviction_unlocks_stale_session():
    runner = _make_runner()
    source = _make_source()
    event = _make_event()
    stale_agent = _FakeIdleAgent(idle_seconds=1900.0, current_tool="terminal")
    session_key = runner._session_key_for_source(source)
    runner._running_agents[session_key] = stale_agent
    runner._running_agents_ts[session_key] = time.time() - 50.0

    async def mock_inner(self_inner, ev, src, qk):
        return "ok"

    with patch.object(GatewayRunner, "_handle_message_with_agent", mock_inner):
        result = await runner._handle_message(event)

    assert result == "ok"
    assert session_key not in runner._running_agents
    assert session_key not in runner._running_agents_ts


@pytest.mark.asyncio
async def test_staged_warning_fires_once_before_timeout(monkeypatch):
    runner = _make_runner()
    source = _make_source()
    state = {"idle": 0.0}
    agent = _FakeIdleAgent(idle_seconds=0.0, current_tool="terminal")
    agent_holder = [agent]
    result_holder = [{"messages": [{"role": "assistant", "content": "partial"}], "api_calls": 4}]
    tools_holder = [[{"name": "terminal"}]]
    stop_event = threading.Event()

    def run_sync():
        stop_event.wait(1.0)
        return {"final_response": "completed unexpectedly"}

    async def fake_wait(tasks, timeout):
        await asyncio.sleep(0)
        state["idle"] += 1.0
        agent.idle_seconds = state["idle"]
        return set(), set()

    monkeypatch.setattr(
        "gateway.run._resolve_gateway_inactivity_policy",
        lambda: GatewayInactivityPolicy(
            hard_timeout_seconds=4.0,
            warning_timeout_seconds=2.0,
            stale_eviction_wall_ttl_seconds=120.0,
            poll_interval_seconds=0.01,
            long_running_notify_interval_seconds=600.0,
        ),
    )

    original_wait = asyncio.wait
    with patch("gateway.run.asyncio.wait", side_effect=fake_wait):
        response = await runner._await_run_sync_with_inactivity_policy(
            loop=asyncio.get_running_loop(),
            run_sync=run_sync,
            source=source,
            session_key="sess",
            agent_holder=agent_holder,
            result_holder=result_holder,
            tools_holder=tools_holder,
            status_thread_metadata=None,
        )

    stop_event.set()
    await asyncio.sleep(0)

    adapter = runner.adapters[Platform.TELEGRAM]
    warning_messages = [msg for _, msg, _ in adapter.sent_messages if "No activity" in msg]
    assert len(warning_messages) == 1
    assert "Agent inactive for 1 min" in response["final_response"]
    assert response["failed"] is True
    assert agent.interrupt_message == "Execution timed out (inactivity)"


@pytest.mark.asyncio
async def test_warning_rearms_after_activity_resumes(monkeypatch):
    runner = _make_runner()
    source = _make_source()
    agent = _FakeIdleAgent(idle_seconds=0.0)
    agent_holder = [agent]
    result_holder = [{"messages": [], "api_calls": 0}]
    tools_holder = [[]]
    stop_event = threading.Event()

    def run_sync():
        stop_event.wait(1.0)
        return {"final_response": "completed unexpectedly"}

    idle_sequence = iter([0.0, 2.0, 0.0, 2.0, 4.0])

    async def fake_wait(tasks, timeout):
        await asyncio.sleep(0)
        try:
            agent.idle_seconds = next(idle_sequence)
        except StopIteration:
            agent.idle_seconds = 4.0
        return set(), set()

    monkeypatch.setattr(
        "gateway.run._resolve_gateway_inactivity_policy",
        lambda: GatewayInactivityPolicy(
            hard_timeout_seconds=4.0,
            warning_timeout_seconds=2.0,
            stale_eviction_wall_ttl_seconds=120.0,
            poll_interval_seconds=0.01,
            long_running_notify_interval_seconds=600.0,
        ),
    )

    with patch("gateway.run.asyncio.wait", side_effect=fake_wait):
        await runner._await_run_sync_with_inactivity_policy(
            loop=asyncio.get_running_loop(),
            run_sync=run_sync,
            source=source,
            session_key="sess",
            agent_holder=agent_holder,
            result_holder=result_holder,
            tools_holder=tools_holder,
            status_thread_metadata=None,
        )

    stop_event.set()
    await asyncio.sleep(0)

    adapter = runner.adapters[Platform.TELEGRAM]
    warning_messages = [msg for _, msg, _ in adapter.sent_messages if "No activity" in msg]
    assert len(warning_messages) == 2


@pytest.mark.asyncio
async def test_trackerless_agent_does_not_timeout_immediately(monkeypatch):
    runner = _make_runner()
    source = _make_source()
    agent = _TrackerlessAgent()
    agent_holder = [agent]
    result_holder = [{"messages": [], "api_calls": 0}]
    tools_holder = [[]]

    def run_sync():
        time.sleep(0.02)
        return {"final_response": "Recovered"}

    monkeypatch.setattr(
        "gateway.run._resolve_gateway_inactivity_policy",
        lambda: GatewayInactivityPolicy(
            hard_timeout_seconds=1.0,
            warning_timeout_seconds=0.5,
            stale_eviction_wall_ttl_seconds=120.0,
            poll_interval_seconds=0.01,
            long_running_notify_interval_seconds=600.0,
        ),
    )

    response = await runner._await_run_sync_with_inactivity_policy(
        loop=asyncio.get_running_loop(),
        run_sync=run_sync,
        source=source,
        session_key="sess",
        agent_holder=agent_holder,
        result_holder=result_holder,
        tools_holder=tools_holder,
        status_thread_metadata=None,
    )

    assert response["final_response"] == "Recovered"
    assert not runner.adapters[Platform.TELEGRAM].sent_messages
