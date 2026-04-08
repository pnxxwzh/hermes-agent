import sys
from types import SimpleNamespace

from hermes_cli.main import main
from hermes_cli.config import load_config
from hermes_cli.setup import run_setup_wizard, setup_sparkgraph


def test_setup_sparkgraph_configures_custom_embedding(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    config = load_config()

    choices = iter([0, 0, 0])  # reconfigure, embedding custom, vendor=openai-compatible
    prompts = iter(
        [
            "3",  # recall max_items
            "2",  # recall max_related
            "1200",  # recall max_chars
            "http://127.0.0.1:8000/v1",
            "text-embedding-3-small",
            "embed-key",
            "25",
        ]
    )

    monkeypatch.setattr("hermes_cli.setup._probe_sparkgraph_embedding_config", lambda config: (True, ""))
    monkeypatch.setattr("hermes_cli.setup.prompt_choice", lambda *a, **kw: next(choices))
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda *a, **kw: next(prompts))
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda *a, **kw: True)

    setup_sparkgraph(config)

    sg = config["sparkgraph"]
    assert sg["recall"]["enabled"] is True
    assert sg["recall"]["max_items"] == 3
    assert sg["recall"]["max_related"] == 2
    assert sg["recall"]["max_chars"] == 1200
    assert "db_path" not in sg
    assert sg["embedding"]["provider"] == "openai-compatible"
    assert sg["embedding"]["model"] == "text-embedding-3-small"
    assert sg["embedding"]["base_url"] == "http://127.0.0.1:8000/v1"
    assert sg["embedding"]["api_key"] == "embed-key"


def test_run_setup_wizard_dispatches_sparkgraph_section(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")

    called = {}

    def _fake_setup_sparkgraph(config):
        called["yes"] = True

    monkeypatch.setattr("hermes_cli.setup.setup_sparkgraph", _fake_setup_sparkgraph)
    monkeypatch.setattr("hermes_cli.setup.is_interactive_stdin", lambda: True)
    monkeypatch.setattr(
        "hermes_cli.setup.SETUP_SECTIONS",
        [
            ("model", "Model & Provider", lambda config: None),
            ("tts", "Text-to-Speech", lambda config: None),
            ("terminal", "Terminal Backend", lambda config: None),
            ("gateway", "Messaging Platforms (Gateway)", lambda config: None),
            ("tools", "Tools", lambda config: None),
            ("sparkgraph", "SparkGraph", _fake_setup_sparkgraph),
            ("agent", "Agent Settings", lambda config: None),
        ],
    )
    monkeypatch.setattr(
        "hermes_cli.setup.prompt_choice",
        lambda question, choices, default=0: 7,  # SparkGraph in returning-user menu
    )
    monkeypatch.setattr("hermes_cli.setup.save_config", lambda config: None)

    run_setup_wizard(SimpleNamespace(section=None, non_interactive=False))

    assert called["yes"] is True


def test_setup_sparkgraph_keep_current_returns_without_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    config = load_config()
    config["sparkgraph"]["recall"]["max_items"] = 7

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", lambda *a, **kw: 2)

    setup_sparkgraph(config)

    assert config["sparkgraph"]["recall"]["max_items"] == 7


def test_setup_sparkgraph_restore_defaults_resets_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    config = load_config()
    config["sparkgraph"]["recall"]["enabled"] = False
    config["sparkgraph"]["db_path"] = str(tmp_path / "external" / "sg.db")

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", lambda *a, **kw: 1)

    setup_sparkgraph(config)

    sg = config["sparkgraph"]
    assert sg["recall"]["enabled"] is True
    assert "db_path" not in sg


def test_setup_sparkgraph_reconfigure_clears_legacy_db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    config = load_config()
    config["sparkgraph"]["db_path"] = str(tmp_path / "external" / "sg.db")

    choices = iter([0, 1])  # reconfigure, disable embedding
    prompts = iter(
        [
            "4",
            "4",
            "1800",
        ]
    )

    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda *a, **kw: True)
    monkeypatch.setattr("hermes_cli.setup.prompt_choice", lambda *a, **kw: next(choices))
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda *a, **kw: next(prompts))

    setup_sparkgraph(config)

    assert "db_path" not in config["sparkgraph"]


def test_setup_sparkgraph_probe_reports_embedding_degraded(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    config = load_config()

    choices = iter([0, 0, 0, 1])  # reconfigure, embedding custom, vendor=openai-compatible, keep broken config
    prompts = iter(
        [
            "3",  # recall max_items
            "2",  # recall max_related
            "1200",  # recall max_chars
            "http://127.0.0.1:8000/v1",
            "text-embedding-3-small",
            "embed-key",
            "25",
        ]
    )
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda *a, **kw: True)
    monkeypatch.setattr("hermes_cli.setup.prompt_choice", lambda *a, **kw: next(choices))
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda *a, **kw: next(prompts))
    monkeypatch.setattr(
        "hermes_cli.setup._probe_sparkgraph_embedding_config",
        lambda config: (False, "probe_failed"),
    )

    setup_sparkgraph(config)

    output = capsys.readouterr().out
    assert "SparkGraph embedding runtime probe failed" in output
    assert config["sparkgraph"]["embedding"]["model"] == "text-embedding-3-small"


def test_setup_sparkgraph_probe_failure_can_restore_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    config = load_config()

    choices = iter([0, 0, 0, 2])  # reconfigure, embedding custom, vendor=openai-compatible, restore defaults
    prompts = iter(
        [
            "4",
            "4",
            "1800",
            "http://127.0.0.1:8000/v1",
            "bge-m3",
            "embed-key",
            "20",
        ]
    )
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda *a, **kw: True)
    monkeypatch.setattr("hermes_cli.setup.prompt_choice", lambda *a, **kw: next(choices))
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda *a, **kw: next(prompts))
    monkeypatch.setattr(
        "hermes_cli.setup._probe_sparkgraph_embedding_config",
        lambda config: (False, "probe_failed"),
    )

    setup_sparkgraph(config)

    # After restoring defaults, embedding returns to the empty opt-in defaults.
    assert config["sparkgraph"]["embedding"]["provider"] == ""
    assert config["sparkgraph"]["embedding"]["model"] == ""
    assert config["sparkgraph"]["embedding"]["base_url"] == ""
    assert config["sparkgraph"]["embedding"]["api_key"] == ""
    assert config["sparkgraph"]["embedding"]["timeout"] == 20


def test_setup_sparkgraph_embedding_prompt_order(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    config = load_config()
    choices = iter([0, 0, 0])  # reconfigure, embedding custom, vendor=openai-compatible
    prompt_labels = []
    prompts = iter(
        [
            "4",
            "4",
            "1800",
            "http://127.0.0.1:8000/v1",
            "bge-m3",
            "embed-key",
            "20",
        ]
    )

    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda *a, **kw: True)
    monkeypatch.setattr("hermes_cli.setup.prompt_choice", lambda *a, **kw: next(choices))
    monkeypatch.setattr(
        "hermes_cli.setup.prompt",
        lambda label, *a, **kw: (prompt_labels.append(label), next(prompts))[1],
    )
    monkeypatch.setattr("hermes_cli.setup._probe_sparkgraph_embedding_config", lambda config: (True, ""))

    setup_sparkgraph(config)

    assert prompt_labels[-4:] == [
        "  Embedding base URL",
        "  Embedding model",
        "  Embedding API key (optional)",
        "  Embedding timeout (seconds)",
    ]


def test_setup_sparkgraph_action_prompt_prefers_keep_current_as_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    config = load_config()
    seen = {}

    def _fake_prompt_choice(question, choices, default=0):
        if question == "SparkGraph setup action:":
            seen["choices"] = list(choices)
            seen["default"] = default
            return 2
        return 2

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", _fake_prompt_choice)

    setup_sparkgraph(config)

    assert seen["choices"][-1].startswith("Keep current settings (")
    assert seen["default"] == len(seen["choices"]) - 1


def test_setup_sparkgraph_probe_failure_prefers_keep_current_over_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    config = load_config()
    choices = iter([0, 0, 0, 1])
    prompts = iter(
        [
            "4",
            "4",
            "1800",
            "",
            "http://127.0.0.1:8000/v1",
            "bge-m3",
            "embed-key",
            "20",
        ]
    )
    failure_prompt = {}

    def _fake_prompt_choice(question, choices, default=0):
        if question == "Embedding probe failed. What would you like to do?":
            failure_prompt["choices"] = list(choices)
            failure_prompt["default"] = default
        return next(choices_iter)

    choices_iter = choices
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda *a, **kw: True)
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda *a, **kw: next(prompts))
    monkeypatch.setattr("hermes_cli.setup.prompt_choice", _fake_prompt_choice)
    monkeypatch.setattr(
        "hermes_cli.setup._probe_sparkgraph_embedding_config",
        lambda config: (False, "probe_failed"),
    )

    setup_sparkgraph(config)

    assert failure_prompt["choices"] == [
        "Reconfigure embedding",
        "Keep this embedding configuration anyway",
        "Restore SparkGraph defaults",
    ]
    assert failure_prompt["default"] == 1


def test_cli_setup_accepts_sparkgraph_section(monkeypatch):
    called = {}

    def fake_run_setup_wizard(args):
        called["section"] = args.section

    monkeypatch.setattr("hermes_cli.setup.run_setup_wizard", fake_run_setup_wizard)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "argv", ["hermes", "setup", "sparkgraph"])

    main()

    assert called["section"] == "sparkgraph"
