from agent.sparkgraph.config import parse_sparkgraph_config
from agent.sparkgraph.runtime import build_runtime_snapshot, embedding_runtime_health


def test_runtime_snapshot_defaults_to_non_degraded_when_optional_runtimes_unset(tmp_path):
    config = parse_sparkgraph_config({}, hermes_home=tmp_path)

    snapshot = build_runtime_snapshot(config)

    assert snapshot.healthy is True
    assert snapshot.degraded is False
    assert snapshot.embedding.enabled is False


def test_embedding_runtime_health_marks_partial_config_degraded(tmp_path):
    config = parse_sparkgraph_config(
        {
            "embedding": {
                "provider": "openai-compatible",
                "model": "text-embedding-3-small",
            }
        },
        hermes_home=tmp_path,
    )

    health = embedding_runtime_health(config)

    assert health.enabled is True
    assert health.healthy is False
    assert health.degraded is True
    assert "partially configured" in health.reason


def test_runtime_snapshot_uses_probe_for_fully_configured_embedding(tmp_path):
    config = parse_sparkgraph_config(
        {
            "embedding": {
                "provider": "openai-compatible",
                "model": "text-embedding-3-small",
                "base_url": "http://localhost:8000",
                "timeout": 10,
            },
        },
        hermes_home=tmp_path,
    )

    def _probe_ok(**kwargs):
        return True, {"models": ["ok-model"], "probed_url": kwargs["base_url"]}

    snapshot = build_runtime_snapshot(config, probe_fn=_probe_ok)

    assert snapshot.healthy is True
    assert snapshot.degraded is False
    assert snapshot.embedding.healthy is True


def test_runtime_snapshot_marks_probe_failure_degraded(tmp_path):
    config = parse_sparkgraph_config(
        {
            "embedding": {
                "provider": "openai-compatible",
                "model": "text-embedding-3-small",
                "base_url": "http://localhost:8000",
                "timeout": 10,
            }
        },
        hermes_home=tmp_path,
    )

    def _probe_fail(**kwargs):
        return False, {"reason": "probe_failed", "probed_url": kwargs["base_url"]}

    snapshot = build_runtime_snapshot(config, probe_fn=_probe_fail)

    assert snapshot.healthy is False
    assert snapshot.degraded is True
    assert snapshot.embedding.degraded is True


def test_runtime_snapshot_can_skip_probe_for_fully_configured_components(tmp_path):
    config = parse_sparkgraph_config(
        {
            "embedding": {
                "provider": "openai-compatible",
                "model": "text-embedding-3-small",
                "base_url": "http://localhost:8000",
                "timeout": 10,
            }
        },
        hermes_home=tmp_path,
    )

    def _probe_should_not_run(**kwargs):
        raise AssertionError("probe should have been skipped")

    snapshot = build_runtime_snapshot(config, probe_fn=_probe_should_not_run, probe_enabled=False)

    assert snapshot.healthy is True
    assert snapshot.degraded is False
    assert snapshot.embedding.healthy is True
    assert snapshot.embedding.details["probe_skipped"] is True
