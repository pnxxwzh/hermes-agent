from gateway.status import sparkgraph_runtime_status


def test_sparkgraph_runtime_status_reads_current_profile_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_cli.config import ensure_hermes_home, save_config

    ensure_hermes_home()
    save_config(
        {
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
        }
    )

    status = sparkgraph_runtime_status()

    assert status is not None
    assert status["healthy"] is True
    assert status["degraded"] is False
    assert status["embedding"]["enabled"] is False


def test_sparkgraph_runtime_status_does_not_probe_network(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_cli.config import ensure_hermes_home, save_config

    ensure_hermes_home()
    save_config(
        {
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
        }
    )

    def _probe_should_not_run(*args, **kwargs):
        raise AssertionError("gateway status must not run network probes")

    monkeypatch.setattr("agent.sparkgraph.runtime._probe_models_endpoint", _probe_should_not_run)

    status = sparkgraph_runtime_status()

    assert status is not None
    assert status["healthy"] is True
    assert status["degraded"] is False
    assert status["embedding"]["enabled"] is True
    assert status["embedding"]["healthy"] is True


def test_sparkgraph_runtime_status_can_probe_when_explicitly_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_cli.config import ensure_hermes_home, save_config

    ensure_hermes_home()
    save_config(
        {
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
        }
    )

    monkeypatch.setattr(
        "agent.sparkgraph.runtime._probe_models_endpoint",
        lambda **kwargs: (True, {"models": ["text-embedding-3-small"], "probed_url": "http://localhost:8000/v1/models"}),
    )

    status = sparkgraph_runtime_status(probe_enabled=True)

    assert status is not None
    assert status["healthy"] is True
    assert status["embedding"]["healthy"] is True
