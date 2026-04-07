from pathlib import Path

import pytest

from agent.sparkgraph.config import (
    DEFAULT_SPARKGRAPH_CONFIG,
    SparkGraphConfigError,
    parse_sparkgraph_config,
    resolve_sparkgraph_db_path,
    sparkgraph_home,
)


def test_parse_sparkgraph_config_uses_profile_scoped_defaults(tmp_path):
    config = parse_sparkgraph_config({}, hermes_home=tmp_path)

    assert config.mode == "flush_integrated"
    assert config.db_path == tmp_path / "sparkgraph" / "default.db"
    assert config.recall.enabled is True


def test_parse_sparkgraph_config_honors_custom_db_path(tmp_path):
    custom = tmp_path / "custom" / "sparkgraph.db"
    config = parse_sparkgraph_config({"db_path": str(custom)}, hermes_home=tmp_path)

    assert config.db_path == custom.resolve()


def test_parse_sparkgraph_config_rejects_invalid_mode(tmp_path):
    with pytest.raises(SparkGraphConfigError, match="flush_integrated"):
        parse_sparkgraph_config({"mode": "review_integrated"}, hermes_home=tmp_path)


def test_parse_sparkgraph_config_rejects_invalid_recall_numbers(tmp_path):
    with pytest.raises(SparkGraphConfigError, match="recall.max_items"):
        parse_sparkgraph_config({"recall": {"max_items": 0}}, hermes_home=tmp_path)


def test_parse_sparkgraph_config_rejects_invalid_timeout_type(tmp_path):
    with pytest.raises(SparkGraphConfigError, match="embedding.timeout"):
        parse_sparkgraph_config({"embedding": {"timeout": "fast"}}, hermes_home=tmp_path)


def test_parse_sparkgraph_config_rejects_non_boolean_recall_enabled(tmp_path):
    with pytest.raises(SparkGraphConfigError, match="recall.enabled"):
        parse_sparkgraph_config({"recall": {"enabled": "yes"}}, hermes_home=tmp_path)


def test_parse_sparkgraph_config_rejects_non_mapping_section(tmp_path):
    with pytest.raises(SparkGraphConfigError, match="sparkgraph.embedding"):
        parse_sparkgraph_config({"embedding": "local"}, hermes_home=tmp_path)


def test_path_helpers_follow_profile_root(tmp_path):
    assert sparkgraph_home(tmp_path) == tmp_path / "sparkgraph"
    assert resolve_sparkgraph_db_path("", hermes_home=tmp_path) == tmp_path / "sparkgraph" / "default.db"


def test_default_config_shape_matches_v2_minimum_contract():
    assert DEFAULT_SPARKGRAPH_CONFIG["mode"] == "flush_integrated"
    assert DEFAULT_SPARKGRAPH_CONFIG["recall"]["max_items"] == 4
    assert "shadow_extractor" not in DEFAULT_SPARKGRAPH_CONFIG
