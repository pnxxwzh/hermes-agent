from pathlib import Path
from unittest.mock import patch

from agent.sparkgraph.config import parse_sparkgraph_config
from hermes_cli.config import ensure_hermes_home, load_config
from hermes_cli.profiles import get_profile_dir


def test_default_profile_db_path_uses_default_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    ensure_hermes_home()

    config = parse_sparkgraph_config(load_config()["sparkgraph"], hermes_home=Path(tmp_path / ".hermes"))
    assert config.db_path == tmp_path / ".hermes" / "sparkgraph" / "default.db"


def test_named_profile_db_path_uses_profile_scoped_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    profile_home = get_profile_dir("coder")
    profile_home.mkdir(parents=True)

    config = parse_sparkgraph_config({}, hermes_home=profile_home)
    assert config.db_path == tmp_path / ".hermes" / "profiles" / "coder" / "sparkgraph" / "default.db"


def test_load_config_exposes_sparkgraph_defaults(tmp_path):
    with patch.dict("os.environ", {"HERMES_HOME": str(tmp_path)}):
        config = load_config()
    assert "sparkgraph" in config
    assert config["sparkgraph"]["mode"] == "flush_integrated"
    assert "db_path" not in config["sparkgraph"]
