from pathlib import Path
import importlib.util
from types import SimpleNamespace


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "sparkgraph_flush_eval.py"
)


def _load_script_module():
    spec = importlib.util.spec_from_file_location("sparkgraph_flush_eval_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_resolve_eval_runtime_uses_current_config(monkeypatch):
    module = _load_script_module()
    monkeypatch.setattr(
        module,
        "load_config",
        lambda: {"model": {"default": "cfg-model"}},
    )
    monkeypatch.setattr(
        module,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "custom",
            "api_mode": "chat_completions",
            "base_url": "http://localhost:8000/v1",
            "api_key": "cfg-key",
            "source": "env/config",
        },
    )

    runtime = module.resolve_eval_runtime()

    assert runtime["model"] == "cfg-model"
    assert runtime["base_url"] == "http://localhost:8000/v1"
    assert runtime["api_key"] == "cfg-key"
    assert runtime["provider"] == "custom"


def test_resolve_eval_runtime_explicit_args_override(monkeypatch):
    module = _load_script_module()
    seen = {}
    monkeypatch.setattr(
        module,
        "load_config",
        lambda: {"model": {"default": "cfg-model"}},
    )

    def _fake_resolve_runtime_provider(**kwargs):
        seen.update(kwargs)
        return {
            "provider": "custom",
            "api_mode": "chat_completions",
            "base_url": kwargs["explicit_base_url"],
            "api_key": kwargs["explicit_api_key"],
            "source": "explicit",
        }

    monkeypatch.setattr(module, "resolve_runtime_provider", _fake_resolve_runtime_provider)

    runtime = module.resolve_eval_runtime(
        model="cli-model",
        base_url="http://127.0.0.1:9000/v1",
        api_key="cli-key",
        provider="custom",
    )

    assert seen["requested"] == "custom"
    assert seen["explicit_base_url"] == "http://127.0.0.1:9000/v1"
    assert seen["explicit_api_key"] == "cli-key"
    assert runtime["model"] == "cli-model"
    assert runtime["base_url"] == "http://127.0.0.1:9000/v1"
    assert runtime["api_key"] == "cli-key"


def test_resolve_eval_runtime_normalizes_openai_compatible_to_custom(monkeypatch):
    module = _load_script_module()
    seen = {}
    monkeypatch.setattr(module, "load_config", lambda: {"model": {"default": "cfg-model"}})

    def _fake_resolve_runtime_provider(**kwargs):
        seen.update(kwargs)
        return {
            "provider": "custom",
            "api_mode": "chat_completions",
            "base_url": "http://127.0.0.1:8000/v1",
            "api_key": "cfg-key",
            "source": "explicit",
        }

    monkeypatch.setattr(module, "resolve_runtime_provider", _fake_resolve_runtime_provider)

    runtime = module.resolve_eval_runtime(
        provider="openai-compatible",
        base_url="http://127.0.0.1:8000/v1",
        api_key="cfg-key",
    )

    assert seen["requested"] == "custom"
    assert runtime["provider"] == "custom"


def test_resolve_eval_runtime_requires_model(monkeypatch):
    module = _load_script_module()
    monkeypatch.setattr(module, "load_config", lambda: {})
    monkeypatch.setattr(
        module,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "custom",
            "api_mode": "chat_completions",
            "base_url": "http://localhost:8000/v1",
            "api_key": "cfg-key",
            "source": "env/config",
        },
    )

    try:
        module.resolve_eval_runtime()
    except ValueError as exc:
        assert "No model configured" in str(exc)
    else:
        raise AssertionError("resolve_eval_runtime() should require a model")


def test_resolve_eval_runtime_requires_base_url(monkeypatch):
    module = _load_script_module()
    monkeypatch.setattr(module, "load_config", lambda: {"model": {"default": "cfg-model"}})
    monkeypatch.setattr(
        module,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "custom",
            "api_mode": "chat_completions",
            "base_url": "",
            "api_key": "cfg-key",
            "source": "env/config",
        },
    )

    try:
        module.resolve_eval_runtime()
    except ValueError as exc:
        assert "No runtime base URL resolved" in str(exc)
    else:
        raise AssertionError("resolve_eval_runtime() should require a base URL")


def test_default_report_path_uses_current_profile_home(monkeypatch, tmp_path):
    module = _load_script_module()
    monkeypatch.setattr(module, "default_flush_eval_report_path", lambda: tmp_path / "sparkgraph" / "evals" / "flush-last.json")

    assert module.default_report_path() == tmp_path / "sparkgraph" / "evals" / "flush-last.json"


def test_write_report_creates_parent_dirs(tmp_path):
    module = _load_script_module()
    path = tmp_path / "sparkgraph" / "evals" / "flush-last.json"
    module.write_report({"summary": {"passed": 1, "total": 1}}, path)

    assert path.exists()
    assert '"passed": 1' in path.read_text(encoding="utf-8")


def test_load_report_returns_none_for_missing_file(tmp_path):
    module = _load_script_module()
    assert module.load_report(tmp_path / "missing.json") is None


def test_compare_summaries_reports_delta():
    module = _load_script_module()
    text = module.compare_summaries(
        {"passed": 4, "total": 5},
        {"summary": {"passed": 3, "total": 5}},
    )
    assert "passed 3->4 (+1)" in text
    assert "total 5->5 (+0)" in text


def test_main_writes_report_and_compares_last(monkeypatch, tmp_path, capsys):
    module = _load_script_module()

    monkeypatch.setattr(
        module,
        "resolve_eval_runtime",
        lambda **kwargs: {
            "model": "test-model",
            "base_url": "http://localhost:8000/v1",
            "api_key": "test-key",
            "provider": "custom",
            "api_mode": "chat_completions",
            "source": "test",
        },
    )
    monkeypatch.setattr(
        module,
        "load_cases",
        lambda: [
            module.FlushEvalCase(
                name="case-ok",
                messages=[{"role": "user", "content": "Remember this fact"}],
                min_items=1,
                max_items=1,
            )
        ],
    )

    class _FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create)
            )

        @staticmethod
        def _create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            tool_calls=[
                                SimpleNamespace(
                                    function=SimpleNamespace(
                                        name="sparkgraph_record",
                                        arguments='{"items":[{"summary":"Remembered fact","type":"FACT","evidence":"Remember this fact"}]}',
                                    )
                                )
                            ]
                        )
                    )
                ]
            )

    report_path = tmp_path / "sparkgraph" / "evals" / "flush-last.json"
    module.write_report({"summary": {"passed": 0, "total": 1}}, report_path)
    monkeypatch.setattr(module, "OpenAI", _FakeClient)
    monkeypatch.setattr(
        "sys.argv",
        [
            "sparkgraph_flush_eval.py",
            "--write-report",
            "--compare-last",
            "--report-path",
            str(report_path),
        ],
    )

    rc = module.main()
    out = capsys.readouterr().out

    assert rc == 0
    assert "Summary: 1/1 passed" in out
    assert "Comparison to previous report: passed 0->1 (+1)" in out
    assert report_path.exists()


def test_main_returns_nonzero_when_case_fails(monkeypatch):
    module = _load_script_module()
    monkeypatch.setattr(
        module,
        "resolve_eval_runtime",
        lambda **kwargs: {
            "model": "test-model",
            "base_url": "http://localhost:8000/v1",
            "api_key": "test-key",
            "provider": "custom",
            "api_mode": "chat_completions",
            "source": "test",
        },
    )
    monkeypatch.setattr(
        module,
        "load_cases",
        lambda: [
            module.FlushEvalCase(
                name="case-fail",
                messages=[{"role": "user", "content": "Hi"}],
                min_items=0,
                max_items=0,
                allowed_empty=True,
            )
        ],
    )

    class _FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create)
            )

        @staticmethod
        def _create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            tool_calls=[
                                SimpleNamespace(
                                    function=SimpleNamespace(
                                        name="sparkgraph_record",
                                        arguments='{"items":[{"summary":"hallucinated","type":"PREFERENCE","evidence":"Hi"}]}',
                                    )
                                )
                            ]
                        )
                    )
                ]
            )

    monkeypatch.setattr(module, "OpenAI", _FakeClient)
    monkeypatch.setattr("sys.argv", ["sparkgraph_flush_eval.py"])

    assert module.main() == 1
