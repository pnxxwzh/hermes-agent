from types import SimpleNamespace

from hermes_cli.status import show_status


def test_show_status_includes_tavily_key(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-1234567890abcdef")

    show_status(SimpleNamespace(all=False, deep=False))

    output = capsys.readouterr().out
    assert "Tavily" in output
    assert "tvly...cdef" in output


def test_show_status_includes_sparkgraph_section(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    import hermes_cli.status as status_mod
    import hermes_cli.auth as auth_mod

    monkeypatch.setattr(status_mod, "load_config", lambda: {
        "model": {"default": "gpt-test"},
        "sparkgraph": {
            "mode": "flush_integrated",
            "db_path": "",
            "recall": {
                "enabled": True,
                "max_items": 3,
                "max_related": 2,
                "max_chars": 1200,
            },
            "embedding": {
                "provider": "",
                "model": "",
                "base_url": "",
                "api_key": "",
                "timeout": 20,
            },
        },
    }, raising=False)
    monkeypatch.setattr(status_mod, "resolve_requested_provider", lambda requested=None: "auto", raising=False)
    monkeypatch.setattr(status_mod, "resolve_provider", lambda requested=None, **kwargs: "openrouter", raising=False)
    monkeypatch.setattr(status_mod, "provider_label", lambda provider: provider, raising=False)
    monkeypatch.setattr(auth_mod, "get_nous_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(auth_mod, "get_codex_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(
        status_mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="inactive\n", returncode=3),
    )

    show_status(SimpleNamespace(all=False, deep=False))

    output = capsys.readouterr().out
    assert "◆ SparkGraph" in output
    assert "Mode:         flush_integrated" in output
    assert "Recall:" in output
    assert "Embedding:" in output


def test_show_status_deep_enables_sparkgraph_probe(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    import hermes_cli.status as status_mod
    import hermes_cli.auth as auth_mod

    monkeypatch.setattr(status_mod, "load_config", lambda: {"model": {"default": "gpt-test"}, "sparkgraph": {}}, raising=False)
    monkeypatch.setattr(status_mod, "resolve_requested_provider", lambda requested=None: "auto", raising=False)
    monkeypatch.setattr(status_mod, "resolve_provider", lambda requested=None, **kwargs: "openrouter", raising=False)
    monkeypatch.setattr(status_mod, "provider_label", lambda provider: provider, raising=False)
    monkeypatch.setattr(auth_mod, "get_nous_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(auth_mod, "get_codex_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(
        status_mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="inactive\n", returncode=3),
    )

    seen = {}

    def _fake_status(*, probe_enabled=False):
        seen["probe_enabled"] = probe_enabled
        return {
            "healthy": True,
            "degraded": False,
            "embedding": {"enabled": False, "healthy": False, "degraded": False, "reason": "embedding runtime disabled"},
        }

    monkeypatch.setattr("gateway.status.sparkgraph_runtime_status", _fake_status)

    status_mod.show_status(SimpleNamespace(all=False, deep=True))

    assert seen["probe_enabled"] is True


def test_show_status_includes_last_sparkgraph_eval(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    import hermes_cli.status as status_mod
    import hermes_cli.auth as auth_mod

    monkeypatch.setattr(status_mod, "load_config", lambda: {"model": {"default": "gpt-test"}, "sparkgraph": {}}, raising=False)
    monkeypatch.setattr(status_mod, "resolve_requested_provider", lambda requested=None: "auto", raising=False)
    monkeypatch.setattr(status_mod, "resolve_provider", lambda requested=None, **kwargs: "openrouter", raising=False)
    monkeypatch.setattr(status_mod, "provider_label", lambda provider: provider, raising=False)
    monkeypatch.setattr(auth_mod, "get_nous_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(auth_mod, "get_codex_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(
        status_mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="inactive\n", returncode=3),
    )
    monkeypatch.setattr(
        "agent.sparkgraph.flush_eval.load_flush_eval_report",
        lambda **kwargs: {
            "generated_at": "2026-04-02T10:00:00+00:00",
            "summary": {"passed": 4, "total": 5},
        },
    )

    status_mod.show_status(SimpleNamespace(all=False, deep=False))

    output = capsys.readouterr().out
    assert "Last Eval:" in output
    assert "4/5" in output
