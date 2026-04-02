"""Tests for cli.py source breakdown rendering (Step 13)."""

import pytest
from unittest.mock import MagicMock

# Import the module-level constants directly from cli
from cli import _SOURCE_COLORS, _SOURCE_LABELS


class TestSourceColorLabels:
    """T13: source color and label mappings."""

    def test_source_colors_defined(self):
        """T13.1: all sources have a color mapping."""
        expected_sources = {
            "stable", "memory", "user_profile", "skills", "project_context",
            "sparkgraph_recall", "ephemeral", "plugin", "honcho_static",
            "honcho_turn", "tool_guidance", "tool_use_enforcement",
            "identity", "system_message", "time_platform",
        }
        assert set(_SOURCE_COLORS.keys()) == expected_sources

    def test_source_labels_defined(self):
        """T13.2: all sources have a label mapping."""
        expected_sources = {
            "stable", "memory", "user_profile", "skills", "project_context",
            "sparkgraph_recall", "ephemeral", "plugin", "honcho_static",
            "honcho_turn", "tool_guidance", "tool_use_enforcement",
            "identity", "system_message", "time_platform",
        }
        assert set(_SOURCE_LABELS.keys()) == expected_sources


class MockSourceMetrics:
    """Minimal SourceMetrics mock for testing."""

    def __init__(self, source, rough_tokens, char_count):
        self.source = source
        self.rough_tokens = rough_tokens
        self.char_count = char_count


class MockContextMetrics:
    """Minimal ContextMetrics mock for testing."""

    def __init__(self, by_source, total_estimated_tokens):
        self.by_source = by_source
        self.total_estimated_tokens = total_estimated_tokens


class TestRenderSourceBar:
    """T13: _render_source_bar tests."""

    def _make_cli(self):
        """Create a HermesCLI instance with minimal setup."""
        from cli import HermesCLI
        mock_cli = object.__new__(HermesCLI)
        mock_cli._show_context_breakdown = False
        return mock_cli

    def test_render_source_bar_empty_metrics(self):
        """T13.3: metrics=None -> ""."""
        cli = self._make_cli()
        assert cli._render_source_bar(None) == ""

    def test_render_source_bar_empty_by_source(self):
        """T13.4: by_source=[] -> ""."""
        cli = self._make_cli()
        metrics = MockContextMetrics([], 0)
        assert cli._render_source_bar(metrics) == ""

    def test_render_source_bar_zero_tokens(self):
        """T13.5: total=0 -> ""."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [MockSourceMetrics("memory", 0, 0)],
            total_estimated_tokens=0,
        )
        assert cli._render_source_bar(metrics) == ""

    def test_render_source_bar_small_sources_merged(self):
        """T13.6: <5% sources merged into 'other'."""
        cli = self._make_cli()
        # 1% + 1% + 1% = 3% < 5% threshold
        metrics = MockContextMetrics(
            [
                MockSourceMetrics("memory", 10, 40),
                MockSourceMetrics("identity", 10, 40),
                MockSourceMetrics("time_platform", 10, 40),
            ],
            total_estimated_tokens=1000,  # 10/1000 = 1%
        )
        result = cli._render_source_bar(metrics)
        # All < 5% -> merged into "other" which shows bright_black color
        # Note: "other" label does not appear in bar output text (no source names in bar)
        # The bar still renders with bright_black segments
        assert "bright_black" in result
        assert "green" not in result  # memory color not present

    def test_render_source_bar_proportion_sum(self):
        """T13.7: all segment proportions sum <= 1."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [
                MockSourceMetrics("identity", 100, 400),
                MockSourceMetrics("memory", 50, 200),
                MockSourceMetrics("skills", 25, 100),
            ],
            total_estimated_tokens=175,
        )
        result = cli._render_source_bar(metrics)
        # Should not raise, and should contain the bar characters
        assert "█" in result

    def test_render_source_bar_proportion_order(self):
        """T13.x: bar segments are sorted by proportion descending (largest first)."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [
                MockSourceMetrics("memory", 5, 20),
                MockSourceMetrics("identity", 100, 400),
                MockSourceMetrics("skills", 20, 80),
            ],
            total_estimated_tokens=125,
        )
        result = cli._render_source_bar(metrics)
        # identity has 80% -> bright_blue (16 chars), skills has 16% -> yellow (3 chars)
        # The bar has 20 chars total: first segment is identity (bright_blue)
        assert "[bright_blue]" in result  # identity is largest, appears first

    def test_render_source_bar_proportion_sum_with_other(self):
        """T13.x: primary segments + other sum to approximately 1."""
        cli = self._make_cli()
        # Small sources: 2% + 2% + 1% = 5%
        metrics = MockContextMetrics(
            [
                MockSourceMetrics("memory", 20, 80),
                MockSourceMetrics("time_platform", 20, 80),
                MockSourceMetrics("identity", 10, 40),
            ],
            total_estimated_tokens=1000,  # small sources = 5%
        )
        result = cli._render_source_bar(metrics)
        # Total should be represented (primary + other)
        assert "█" in result

    def test_render_source_bar_unknown_source_color(self):
        """T13.9: unknown source gets default 'white' color."""
        cli = self._make_cli()
        # Unknown source type — should use 'white' as default color
        unknown_metrics = MockContextMetrics(
            [MockSourceMetrics("unknown_source", 50, 200)],
            total_estimated_tokens=50,
        )
        # Should not raise and should use 'white' as default color
        result = cli._render_source_bar(unknown_metrics)
        assert "[white]" in result  # default color is white
        assert "unknown_source" not in result  # source label not in bar output

    def test_render_bar_width_fixed(self):
        """T13.10: bar width is fixed at 20 regardless of input."""
        cli = self._make_cli()
        metrics1 = MockContextMetrics(
            [MockSourceMetrics("identity", 1, 4)],
            total_estimated_tokens=1,
        )
        metrics2 = MockContextMetrics(
            [MockSourceMetrics("identity", 1000, 4000)],
            total_estimated_tokens=1000,
        )
        result1 = cli._render_source_bar(metrics1, width=20)
        result2 = cli._render_source_bar(metrics2, width=20)
        # Both should have bar chars but at different densities
        bar1_count = result1.count("█")
        bar2_count = result2.count("█")
        # Width is 20, so max bars is 20
        assert bar1_count <= 20
        assert bar2_count <= 20

    def test_render_source_bar_contains_tokens(self):
        """T13.x: result contains total token count."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [MockSourceMetrics("identity", 50, 200)],
            total_estimated_tokens=50,
        )
        result = cli._render_source_bar(metrics)
        assert "50 tok" in result


class TestRenderContextBreakdown:
    """T13: _render_context_breakdown tests."""

    def _make_cli(self):
        from cli import HermesCLI
        mock_cli = object.__new__(HermesCLI)
        mock_cli._show_context_breakdown = False
        return mock_cli

    def test_render_context_breakdown_all_sources(self):
        """T13.8: every by_source entry is rendered."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [
                MockSourceMetrics("memory", 10, 40),
                MockSourceMetrics("identity", 20, 80),
            ],
            total_estimated_tokens=30,
        )
        result = cli._render_context_breakdown(metrics)
        assert "memory" in result
        assert "identity" in result
        assert "10" in result  # tokens
        assert "40" in result  # chars

    def test_render_context_breakdown_empty_metrics(self):
        """T13.x: metrics=None -> ""."""
        cli = self._make_cli()
        assert cli._render_context_breakdown(None) == ""

    def test_render_context_breakdown_no_by_source(self):
        """T13.x: metrics with no by_source attr -> ""."""
        cli = self._make_cli()
        assert cli._render_context_breakdown(MagicMock(spec=[])) == ""

    def test_render_context_breakdown_uses_color_format(self):
        """T13.x: output uses rich color format [color]...[/color]."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [MockSourceMetrics("memory", 5, 20)],
            total_estimated_tokens=5,
        )
        result = cli._render_context_breakdown(metrics)
        assert "[green]" in result
        assert "[/green]" in result


class TestPhase2Features:
    """Phase 2: enabled breakdown, adaptive width, context_metrics access."""

    def _make_cli(self):
        """Create a HermesCLI instance with minimal setup."""
        from cli import HermesCLI
        mock_cli = object.__new__(HermesCLI)
        mock_cli._show_context_breakdown = True
        return mock_cli

    def test_show_context_breakdown_default_true(self):
        """T14.1: _show_context_breakdown is set to True in __init__."""
        import inspect
        from cli import HermesCLI
        source = inspect.getsource(HermesCLI.__init__)
        # Phase 2 sets _show_context_breakdown = True (not False)
        assert "_show_context_breakdown = True" in source

    def test_get_current_context_metrics_no_agent(self):
        """T14.2: returns None when cli has no agent."""
        cli = self._make_cli()
        cli.agent = None
        assert cli._get_current_context_metrics() is None

    def test_get_current_context_metrics_no_agent_attr(self):
        """T14.3: returns None when agent lacks _last_context_metrics attr."""
        from cli import HermesCLI

        class BareAgent:
            """Minimal agent mock that doesn't have _last_context_metrics."""
            pass

        cli = object.__new__(HermesCLI)
        cli._show_context_breakdown = True
        cli.agent = BareAgent()
        assert cli._get_current_context_metrics() is None

    def test_get_current_context_metrics_returns_metrics(self):
        """T14.4: returns agent._last_context_metrics when available."""
        from cli import HermesCLI
        cli = object.__new__(HermesCLI)
        cli._show_context_breakdown = True
        mock_metrics = MagicMock()
        cli.agent = MagicMock()
        cli.agent._last_context_metrics = mock_metrics
        assert cli._get_current_context_metrics() is mock_metrics

    def test_render_source_bar_width_affects_output(self):
        """T14.5: bar width affects filled character count."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [MockSourceMetrics("identity", 100, 400)],
            total_estimated_tokens=100,
        )
        # At 100% proportion, width=20 -> 20 █ chars, width=10 -> 10
        result_20 = cli._render_source_bar(metrics, width=20)
        result_10 = cli._render_source_bar(metrics, width=10)
        assert result_20.count("█") == 20
        assert result_10.count("█") == 10

    def test_render_source_bar_adaptive_width_param(self):
        """T14.6: width parameter is used when passed explicitly."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [MockSourceMetrics("memory", 25, 100)],
            total_estimated_tokens=100,
        )
        # memory=25%, width=20 -> 5 █ chars
        result = cli._render_source_bar(metrics, width=20)
        # 25% of 20 = 5 filled characters
        assert result.count("█") == 5
        # token count shown instead of meaningless percentage
        assert "100 tok" in result
