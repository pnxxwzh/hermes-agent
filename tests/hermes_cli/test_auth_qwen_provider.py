"""Tests for Qwen OAuth provider authentication helpers."""

from __future__ import annotations

import json
import stat
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli.auth import (
    AuthError,
    QWEN_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
    _qwen_access_token_is_expiring,
    _qwen_cli_auth_path,
    _read_qwen_cli_tokens,
    _refresh_qwen_cli_tokens,
    _save_qwen_cli_tokens,
    get_qwen_auth_status,
    resolve_qwen_runtime_credentials,
)


def _make_qwen_tokens(
    access_token: str = "test-access-token",
    refresh_token: str = "test-refresh-token",
    expiry_date: int | None = None,
    **extra,
):
    if expiry_date is None:
        expiry_date = int((time.time() + 3600) * 1000)
    data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expiry_date": expiry_date,
        "resource_url": "portal.qwen.ai",
    }
    data.update(extra)
    return data


@pytest.fixture()
def qwen_env(tmp_path, monkeypatch):
    creds_path = tmp_path / ".qwen" / "oauth_creds.json"
    monkeypatch.setattr("hermes_cli.auth._qwen_cli_auth_path", lambda: creds_path)
    return creds_path


def test_qwen_cli_auth_path_returns_expected_location():
    assert _qwen_cli_auth_path() == Path.home() / ".qwen" / "oauth_creds.json"


def test_read_qwen_cli_tokens_success(qwen_env):
    qwen_env.parent.mkdir(parents=True, exist_ok=True)
    qwen_env.write_text(json.dumps(_make_qwen_tokens(access_token="my-access")), encoding="utf-8")
    result = _read_qwen_cli_tokens()
    assert result["access_token"] == "my-access"
    assert result["refresh_token"] == "test-refresh-token"


def test_read_qwen_cli_tokens_missing_file(qwen_env):
    with pytest.raises(AuthError) as exc:
        _read_qwen_cli_tokens()
    assert exc.value.code == "qwen_auth_missing"


def test_read_qwen_cli_tokens_invalid_json(qwen_env):
    qwen_env.parent.mkdir(parents=True, exist_ok=True)
    qwen_env.write_text("not json{{{", encoding="utf-8")
    with pytest.raises(AuthError) as exc:
        _read_qwen_cli_tokens()
    assert exc.value.code == "qwen_auth_read_failed"


def test_save_qwen_cli_tokens_roundtrip_and_permissions(qwen_env):
    saved_path = _save_qwen_cli_tokens(_make_qwen_tokens(access_token="saved-token"))
    loaded = json.loads(saved_path.read_text(encoding="utf-8"))
    assert loaded["access_token"] == "saved-token"
    mode = saved_path.stat().st_mode
    assert mode & stat.S_IRUSR
    assert mode & stat.S_IWUSR
    assert not (mode & stat.S_IRGRP)
    assert not (mode & stat.S_IROTH)


def test_qwen_access_token_is_expiring_handles_thresholds():
    future_ms = int((time.time() + 3600) * 1000)
    past_ms = int((time.time() - 3600) * 1000)
    near_ms = int((time.time() + QWEN_ACCESS_TOKEN_REFRESH_SKEW_SECONDS - 5) * 1000)
    assert not _qwen_access_token_is_expiring(future_ms)
    assert _qwen_access_token_is_expiring(past_ms)
    assert _qwen_access_token_is_expiring(near_ms)
    assert _qwen_access_token_is_expiring(None)
    assert _qwen_access_token_is_expiring("not-a-number")


def test_refresh_qwen_cli_tokens_success(qwen_env):
    qwen_env.parent.mkdir(parents=True, exist_ok=True)
    qwen_env.write_text(json.dumps(_make_qwen_tokens(refresh_token="old-refresh")), encoding="utf-8")
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 7200,
    }
    with patch("hermes_cli.auth.httpx.post", return_value=resp):
        result = _refresh_qwen_cli_tokens(_make_qwen_tokens(refresh_token="old-refresh"))
    assert result["access_token"] == "new-access"
    assert result["refresh_token"] == "new-refresh"
    assert "expiry_date" in result


def test_refresh_qwen_cli_tokens_missing_refresh_token():
    with pytest.raises(AuthError) as exc:
        _refresh_qwen_cli_tokens({"access_token": "at", "refresh_token": ""})
    assert exc.value.code == "qwen_refresh_token_missing"


def test_refresh_qwen_cli_tokens_http_error():
    resp = MagicMock()
    resp.status_code = 401
    resp.text = "unauthorized"
    with patch("hermes_cli.auth.httpx.post", return_value=resp):
        with pytest.raises(AuthError) as exc:
            _refresh_qwen_cli_tokens(_make_qwen_tokens())
    assert exc.value.code == "qwen_refresh_failed"


def test_resolve_qwen_runtime_credentials(qwen_env, monkeypatch):
    qwen_env.parent.mkdir(parents=True, exist_ok=True)
    qwen_env.write_text(json.dumps(_make_qwen_tokens(access_token="qwen-at")), encoding="utf-8")
    monkeypatch.delenv("HERMES_QWEN_BASE_URL", raising=False)
    creds = resolve_qwen_runtime_credentials(refresh_if_expiring=False)
    assert creds["provider"] == "qwen-oauth"
    assert creds["api_key"] == "qwen-at"
    assert creds["source"] == "qwen-cli"
    assert creds["auth_file"] == str(qwen_env)


def test_get_qwen_auth_status_returns_error_when_missing(qwen_env):
    status = get_qwen_auth_status()
    assert status["logged_in"] is False
    assert "error" in status
