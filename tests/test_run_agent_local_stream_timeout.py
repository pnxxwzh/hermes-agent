"""Tests for local-provider stale stream timeout policy."""

from run_agent import _resolve_stream_timeout_policy


def test_local_endpoint_disables_stale_timeout_when_not_explicit(monkeypatch):
    monkeypatch.delenv("HERMES_STREAM_STALE_TIMEOUT", raising=False)

    policy = _resolve_stream_timeout_policy(
        api_kwargs={"messages": [{"role": "user", "content": "hello"}]},
        base_url="http://localhost:11434/v1",
    )

    assert policy.disabled_for_local is True
    assert policy.is_user_explicit is False
    assert policy.stale_timeout_seconds == 0.0


def test_local_endpoint_honors_explicit_timeout(monkeypatch):
    monkeypatch.setenv("HERMES_STREAM_STALE_TIMEOUT", "123")

    policy = _resolve_stream_timeout_policy(
        api_kwargs={"messages": [{"role": "user", "content": "hello"}]},
        base_url="http://127.0.0.1:11434/v1",
    )

    assert policy.disabled_for_local is False
    assert policy.is_user_explicit is True
    assert policy.stale_timeout_seconds == 123.0


def test_remote_endpoint_keeps_scaled_timeout(monkeypatch):
    monkeypatch.delenv("HERMES_STREAM_STALE_TIMEOUT", raising=False)

    policy = _resolve_stream_timeout_policy(
        api_kwargs={"messages": [{"role": "user", "content": "x" * 410_000}]},
        base_url="https://api.openai.com/v1",
    )

    assert policy.disabled_for_local is False
    assert policy.is_user_explicit is False
    assert policy.stale_timeout_seconds == 300.0
