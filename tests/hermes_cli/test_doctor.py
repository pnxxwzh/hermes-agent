"""Tests for hermes_cli.doctor."""

import os
import sys
import types
from argparse import Namespace
from types import SimpleNamespace

import pytest

import hermes_cli.doctor as doctor
import hermes_cli.gateway as gateway_cli
from hermes_cli import doctor as doctor_mod
from hermes_cli.doctor import _has_provider_env_config


class TestProviderEnvDetection:
    def test_detects_openai_api_key(self):
        content = "OPENAI_BASE_URL=http://localhost:1234/v1\nOPENAI_API_KEY=***"
        assert _has_provider_env_config(content)

    def test_detects_custom_endpoint_without_openrouter_key(self):
        content = "OPENAI_BASE_URL=http://localhost:8080/v1\n"
        assert _has_provider_env_config(content)

    def test_returns_false_when_no_provider_settings(self):
        content = "TERMINAL_ENV=local\n"
        assert not _has_provider_env_config(content)


class TestDoctorToolAvailabilityOverrides:
    def test_marks_honcho_available_when_configured(self, monkeypatch):
        monkeypatch.setattr(doctor, "_honcho_is_configured_for_doctor", lambda: True)

        available, unavailable = doctor._apply_doctor_tool_availability_overrides(
            [],
            [{"name": "honcho", "env_vars": [], "tools": ["query_user_context"]}],
        )

        assert available == ["honcho"]
        assert unavailable == []

    def test_leaves_honcho_unavailable_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(doctor, "_honcho_is_configured_for_doctor", lambda: False)

        honcho_entry = {"name": "honcho", "env_vars": [], "tools": ["query_user_context"]}
        available, unavailable = doctor._apply_doctor_tool_availability_overrides(
            [],
            [honcho_entry],
        )

        assert available == []
        assert unavailable == [honcho_entry]


class TestHonchoDoctorConfigDetection:
    def test_reports_configured_when_enabled_with_api_key(self, monkeypatch):
        fake_config = SimpleNamespace(enabled=True, api_key="***")

        monkeypatch.setattr(
            "honcho_integration.client.HonchoClientConfig.from_global_config",
            lambda: fake_config,
        )

        assert doctor._honcho_is_configured_for_doctor()

    def test_reports_not_configured_without_api_key(self, monkeypatch):
        fake_config = SimpleNamespace(enabled=True, api_key="")

        monkeypatch.setattr(
            "honcho_integration.client.HonchoClientConfig.from_global_config",
            lambda: fake_config,
        )

        assert not doctor._honcho_is_configured_for_doctor()


def test_run_doctor_sets_interactive_env_for_tool_checks(monkeypatch, tmp_path):
    """Doctor should present CLI-gated tools as available in CLI context."""
    project_root = tmp_path / "project"
    hermes_home = tmp_path / ".hermes"
    project_root.mkdir()
    hermes_home.mkdir()

    monkeypatch.setattr(doctor_mod, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(doctor_mod, "HERMES_HOME", hermes_home)
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)

    seen = {}

    def fake_check_tool_availability(*args, **kwargs):
        seen["interactive"] = os.getenv("HERMES_INTERACTIVE")
        raise SystemExit(0)

    fake_model_tools = types.SimpleNamespace(
        check_tool_availability=fake_check_tool_availability,
        TOOLSET_REQUIREMENTS={},
    )
    monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

    with pytest.raises(SystemExit):
        doctor_mod.run_doctor(Namespace(fix=False))

    assert seen["interactive"] == "1"


def test_check_gateway_service_linger_warns_when_disabled(monkeypatch, tmp_path, capsys):
    unit_path = tmp_path / "hermes-gateway.service"
    unit_path.write_text("[Unit]\n")

    monkeypatch.setattr(gateway_cli, "is_linux", lambda: True)
    monkeypatch.setattr(gateway_cli, "get_systemd_unit_path", lambda: unit_path)
    monkeypatch.setattr(gateway_cli, "get_systemd_linger_status", lambda: (False, ""))

    issues = []
    doctor._check_gateway_service_linger(issues)

    out = capsys.readouterr().out
    assert "Gateway Service" in out
    assert "Systemd linger disabled" in out
    assert "loginctl enable-linger" in out
    assert issues == [
        "Enable linger for the gateway user service: sudo loginctl enable-linger $USER"
    ]


def test_check_gateway_service_linger_skips_when_service_not_installed(monkeypatch, tmp_path, capsys):
    unit_path = tmp_path / "missing.service"

    monkeypatch.setattr(gateway_cli, "is_linux", lambda: True)
    monkeypatch.setattr(gateway_cli, "get_systemd_unit_path", lambda: unit_path)

    issues = []
    doctor._check_gateway_service_linger(issues)

    out = capsys.readouterr().out
    assert out == ""
    assert issues == []


def test_check_sparkgraph_reports_ready_without_network_probe(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(doctor_mod, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(doctor_mod, "_DHH", "~/.hermes")
    monkeypatch.setattr(
        doctor_mod,
        "load_config",
        lambda: {
            "sparkgraph": {
                "mode": "flush_integrated",
                "db_path": "",
                "recall": {
                    "enabled": True,
                    "max_items": 4,
                    "max_related": 4,
                    "budget_ratio": 0.12,
                    "max_chars": 1800,
                },
                "embedding": {
                    "provider": "",
                    "model": "",
                    "base_url": "",
                    "api_key": "",
                    "timeout": 20,
                },
            }
        },
    )
    monkeypatch.setattr(
        "gateway.status.sparkgraph_runtime_status",
        lambda **kwargs: {
            "healthy": True,
            "degraded": False,
            "embedding": {
                "enabled": False,
                "healthy": False,
                "degraded": False,
                "reason": "embedding runtime disabled",
            },
        },
    )

    issues = []
    doctor_mod._check_sparkgraph(issues)

    out = capsys.readouterr().out
    assert "◆ SparkGraph" in out
    assert "SparkGraph config parsed" in out
    assert "SparkGraph runtime ready" in out
    assert "Embedding runtime disabled" in out
    assert issues == []


def test_check_sparkgraph_runtime_ready_when_db_parent_exists(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(doctor_mod, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(doctor_mod, "_DHH", "~/.hermes")
    (tmp_path / "sparkgraph").mkdir(parents=True)
    monkeypatch.setattr(
        doctor_mod,
        "load_config",
        lambda: {
            "sparkgraph": {
                "mode": "flush_integrated",
                "db_path": "",
                "recall": {
                    "enabled": True,
                    "max_items": 4,
                    "max_related": 4,
                    "budget_ratio": 0.12,
                    "max_chars": 1800,
                },
                "embedding": {
                    "provider": "",
                    "model": "",
                    "base_url": "",
                    "api_key": "",
                    "timeout": 20,
                },
            }
        },
    )
    monkeypatch.setattr(
        "gateway.status.sparkgraph_runtime_status",
        lambda **kwargs: {
            "healthy": True,
            "degraded": False,
            "embedding": {
                "enabled": False,
                "healthy": False,
                "degraded": False,
                "reason": "embedding runtime disabled",
            },
        },
    )

    issues = []
    doctor_mod._check_sparkgraph(issues)

    out = capsys.readouterr().out
    assert "SparkGraph DB parent exists" in out
    assert "SparkGraph runtime ready" in out
    assert issues == []


def test_check_sparkgraph_reports_invalid_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(doctor_mod, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(doctor_mod, "_DHH", "~/.hermes")
    monkeypatch.setattr(doctor_mod, "load_config", lambda: {"sparkgraph": {"mode": "bad-mode"}})

    issues = []
    doctor_mod._check_sparkgraph(issues)

    out = capsys.readouterr().out
    assert "SparkGraph config invalid" in out
    assert issues == ["Fix sparkgraph config in config.yaml"]


def test_check_sparkgraph_enables_probe_when_requested(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(doctor_mod, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(doctor_mod, "_DHH", "~/.hermes")
    monkeypatch.setattr(
        doctor_mod,
        "load_config",
        lambda: {
            "sparkgraph": {
                "mode": "flush_integrated",
                "db_path": "",
                "recall": {
                    "enabled": True,
                    "max_items": 4,
                    "max_related": 4,
                    "budget_ratio": 0.12,
                    "max_chars": 1800,
                },
                "embedding": {
                    "provider": "openai-compatible",
                    "model": "text-embedding-3-small",
                    "base_url": "http://localhost:8000",
                    "api_key": "",
                    "timeout": 20,
                },
            }
        },
    )

    seen = {}

    def _fake_status(*, probe_enabled=False):
        seen["probe_enabled"] = probe_enabled
        return {
            "healthy": True,
            "degraded": False,
            "embedding": {
                "enabled": True,
                "healthy": True,
                "degraded": False,
                "reason": "",
            },
        }

    monkeypatch.setattr("gateway.status.sparkgraph_runtime_status", _fake_status)

    issues = []
    doctor_mod._check_sparkgraph(issues, probe_enabled=True)

    out = capsys.readouterr().out
    assert "Embedding runtime configured" in out
    assert seen["probe_enabled"] is True


def test_check_sparkgraph_warns_on_last_failed_eval(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(doctor_mod, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(doctor_mod, "_DHH", "~/.hermes")
    monkeypatch.setattr(
        doctor_mod,
        "load_config",
        lambda: {
            "sparkgraph": {
                "mode": "flush_integrated",
                "db_path": "",
                "recall": {
                    "enabled": True,
                    "max_items": 4,
                    "max_related": 4,
                    "budget_ratio": 0.12,
                    "max_chars": 1800,
                },
                "embedding": {
                    "provider": "",
                    "model": "",
                    "base_url": "",
                    "api_key": "",
                    "timeout": 20,
                },
            }
        },
    )
    monkeypatch.setattr(
        "gateway.status.sparkgraph_runtime_status",
        lambda **kwargs: {
            "healthy": True,
            "degraded": False,
            "embedding": {
                "enabled": False,
                "healthy": False,
                "degraded": False,
                "reason": "embedding runtime disabled",
            },
        },
    )
    monkeypatch.setattr(
        "agent.sparkgraph.flush_eval.load_flush_eval_report",
        lambda **kwargs: {
            "generated_at": "2026-04-02T10:00:00+00:00",
            "summary": {"passed": 4, "total": 5},
        },
    )

    issues = []
    doctor_mod._check_sparkgraph(issues)

    out = capsys.readouterr().out
    assert "Last SparkGraph flush eval has failures" in out
    assert "Rerun SparkGraph flush eval and inspect failing fixtures" in issues
