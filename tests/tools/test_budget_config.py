from tools.budget_config import BudgetConfig, DEFAULT_BUDGET, PINNED_THRESHOLDS
from tools.registry import ToolRegistry


def test_default_budget_matches_upstream_defaults():
    assert DEFAULT_BUDGET.default_result_size == 100_000
    assert DEFAULT_BUDGET.turn_budget == 200_000
    assert DEFAULT_BUDGET.preview_size == 1_500


def test_read_file_threshold_is_pinned():
    config = BudgetConfig(tool_overrides={"read_file": 10})
    assert config.resolve_threshold("read_file") == PINNED_THRESHOLDS["read_file"]


def test_tool_override_precedes_registry_default(monkeypatch):
    registry = ToolRegistry()
    registry.register(
        name="alpha",
        toolset="core",
        schema={"name": "alpha", "parameters": {"type": "object"}},
        handler=lambda args, **kwargs: "{}",
        max_result_size=123,
    )
    monkeypatch.setattr("tools.budget_config.registry", registry, raising=False)
    config = BudgetConfig(default_result_size=999, tool_overrides={"alpha": 456})
    assert config.resolve_threshold("alpha") == 456


def test_registry_default_used_when_no_override(monkeypatch):
    registry = ToolRegistry()
    registry.register(
        name="alpha",
        toolset="core",
        schema={"name": "alpha", "parameters": {"type": "object"}},
        handler=lambda args, **kwargs: "{}",
        max_result_size=123,
    )
    monkeypatch.setattr("tools.budget_config.registry", registry, raising=False)
    config = BudgetConfig(default_result_size=999)
    monkeypatch.setattr("tools.budget_config.registry", registry, raising=False)
    monkeypatch.setattr("tools.registry.registry", registry)
    assert config.resolve_threshold("alpha") == 123


def test_default_used_when_tool_not_registered(monkeypatch):
    monkeypatch.setattr("tools.registry.registry", ToolRegistry())
    config = BudgetConfig(default_result_size=777)
    assert config.resolve_threshold("missing") == 777
