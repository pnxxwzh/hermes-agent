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
            "context_identity", "context_tool_guidance",
            "context_tool_use_enforcement", "context_honcho_static",
            "context_system_message", "context_memory", "context_user_profile",
            "context_skills", "context_project", "context_time_platform",
            "context_ephemeral", "context_plugin", "context_sparkgraph_recall",
            "context_honcho_turn", "messages_user", "messages_assistant",
            "messages_tool", "messages_other", "prefill_messages",
            "tool_schemas",
        }
        assert set(_SOURCE_COLORS.keys()) == expected_sources

    def test_source_labels_defined(self):
        """T13.2: all sources have a label mapping."""
        expected_sources = {
            "stable", "memory", "user_profile", "skills", "project_context",
            "sparkgraph_recall", "ephemeral", "plugin", "honcho_static",
            "honcho_turn", "tool_guidance", "tool_use_enforcement",
            "identity", "system_message", "time_platform",
            "context_identity", "context_tool_guidance",
            "context_tool_use_enforcement", "context_honcho_static",
            "context_system_message", "context_memory", "context_user_profile",
            "context_skills", "context_project", "context_time_platform",
            "context_ephemeral", "context_plugin", "context_sparkgraph_recall",
            "context_honcho_turn", "messages_user", "messages_assistant",
            "messages_tool", "messages_other", "prefill_messages",
            "tool_schemas",
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


class MockRequestBucketMetrics:
    """Minimal RequestBucketMetrics mock for testing."""

    def __init__(self, bucket, rough_tokens, char_count):
        self.bucket = bucket
        self.rough_tokens = rough_tokens
        self.char_count = char_count


class MockRequestMetrics:
    """Minimal RequestMetrics mock for testing."""

    def __init__(self, by_bucket, total_estimated_tokens):
        self.by_bucket = by_bucket
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
        """T13.6: 4th+ source merged into 'other'."""
        cli = self._make_cli()
        # 4 sources: 3 large enough, 1 merged into other
        metrics = MockContextMetrics(
            [
                MockSourceMetrics("memory", 10, 40),
                MockSourceMetrics("identity", 10, 40),
                MockSourceMetrics("time_platform", 10, 40),
                MockSourceMetrics("skills", 970, 4000),
            ],
            total_estimated_tokens=1000,
        )
        result = cli._render_source_bar(metrics)
        # Top 3: skills:97%, memory:1%, identity:1% -> "other:1%" for time_platform
        assert "skills:97%" in result
        assert "other:1%" in result

    def test_render_source_bar_proportion_sum(self):
        """T13.7: returns compact label with source proportions."""
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
        assert "identity:57%" in result
        assert "memory:28%" in result
        assert "skills:14%" in result

    def test_render_source_bar_proportion_order(self):
        """T13.x: top 3 sources shown in descending proportion order."""
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
        assert "identity:80%" in result
        assert "skills:16%" in result
        assert "memory:4%" in result

    def test_render_source_bar_proportion_sum_with_other(self):
        """T13.x: 4th+ sources merged into other."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [
                MockSourceMetrics("memory", 200, 800),
                MockSourceMetrics("time_platform", 20, 80),
                MockSourceMetrics("identity", 500, 2000),
            ],
            total_estimated_tokens=1000,
        )
        result = cli._render_source_bar(metrics)
        assert "identity:50%" in result
        assert "memory:20%" in result

    def test_render_source_bar_unknown_source_color(self):
        """T13.9: unknown source keeps its source name in label."""
        cli = self._make_cli()
        unknown_metrics = MockContextMetrics(
            [MockSourceMetrics("unknown_source", 50, 200)],
            total_estimated_tokens=50,
        )
        result = cli._render_source_bar(unknown_metrics)
        assert "unknown_source:100%" in result

    def test_render_bar_width_fixed(self):
        """T13.10: width param does not affect label output (label has no bar)."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [MockSourceMetrics("identity", 1000, 4000)],
            total_estimated_tokens=1000,
        )
        result1 = cli._render_source_bar(metrics, width=20)
        result2 = cli._render_source_bar(metrics, width=30)
        # Label doesn't use width parameter
        assert result1 == result2 == "identity:100%"

    def test_render_source_bar_contains_tokens(self):
        """T13.x: result contains source proportions as percentages."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [MockSourceMetrics("identity", 50, 200)],
            total_estimated_tokens=50,
        )
        result = cli._render_source_bar(metrics)
        assert "identity:100%" in result

    def test_render_source_bar_aggregates_duplicate_sources(self):
        """T13.x: multiple chunks from the same source are aggregated once."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [
                MockSourceMetrics("memory", 40, 160),
                MockSourceMetrics("memory", 10, 40),
                MockSourceMetrics("identity", 50, 200),
            ],
            total_estimated_tokens=100,
        )
        result = cli._render_source_bar(metrics)
        assert result.count("memory:50%") == 1
        assert "identity:50%" in result

    def test_render_source_bar_supports_request_buckets(self):
        """T13.x: request metrics render via by_bucket."""
        cli = self._make_cli()
        metrics = MockRequestMetrics(
            [
                MockRequestBucketMetrics("messages_tool", 60, 240),
                MockRequestBucketMetrics("context_project", 30, 120),
                MockRequestBucketMetrics("tool_schemas", 10, 40),
            ],
            total_estimated_tokens=100,
        )
        result = cli._render_source_bar(metrics)
        assert "msg:tool:60%" in result
        assert "project:30%" in result
        assert "tools:10%" in result


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

    def test_render_context_breakdown_aggregates_duplicate_sources(self):
        """T13.x: duplicate source chunks are rendered as one aggregated line."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [
                MockSourceMetrics("memory", 5, 20),
                MockSourceMetrics("memory", 7, 28),
            ],
            total_estimated_tokens=12,
        )
        result = cli._render_context_breakdown(metrics)
        assert result.count("memory") == 1
        assert "12" in result
        assert "48" in result

    def test_render_context_breakdown_supports_request_metrics(self):
        """T13.x: request buckets render in detailed breakdown."""
        cli = self._make_cli()
        metrics = MockRequestMetrics(
            [
                MockRequestBucketMetrics("messages_user", 8, 32),
                MockRequestBucketMetrics("tool_schemas", 12, 48),
            ],
            total_estimated_tokens=20,
        )
        result = cli._render_context_breakdown(metrics)
        assert "msg:user" in result
        assert "tools" in result
        assert "32" in result
        assert "48" in result


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

    def test_get_current_request_metrics_no_agent(self):
        """T14.x: returns None when cli has no request-metrics agent."""
        cli = self._make_cli()
        cli.agent = None
        assert cli._get_current_request_metrics() is None

    def test_get_current_request_metrics_returns_metrics(self):
        """T14.x: returns agent._last_request_metrics when available."""
        from cli import HermesCLI
        cli = object.__new__(HermesCLI)
        cli._show_context_breakdown = True
        mock_metrics = MockRequestMetrics(
            [MockRequestBucketMetrics("messages_user", 8, 32)],
            total_estimated_tokens=8,
        )
        cli.agent = MagicMock()
        cli.agent._last_request_metrics = mock_metrics
        assert cli._get_current_request_metrics() is mock_metrics

    def test_get_preferred_breakdown_metrics_prefers_request_metrics(self):
        """T14.x: preferred breakdown uses request metrics first."""
        cli = self._make_cli()
        cli.agent = MagicMock()
        cli.agent._last_request_metrics = MockRequestMetrics(
            [MockRequestBucketMetrics("messages_user", 8, 32)],
            total_estimated_tokens=8,
        )
        cli.agent._last_context_metrics = MockContextMetrics(
            [MockSourceMetrics("memory", 5, 20)],
            total_estimated_tokens=5,
        )
        assert cli._get_preferred_breakdown_metrics() is cli.agent._last_request_metrics

    def test_get_preferred_breakdown_metrics_falls_back_to_context_metrics(self):
        """T14.x: preferred breakdown falls back to context metrics."""
        cli = self._make_cli()
        cli.agent = MagicMock()
        cli.agent._last_request_metrics = None
        cli.agent._last_context_metrics = MockContextMetrics(
            [MockSourceMetrics("memory", 5, 20)],
            total_estimated_tokens=5,
        )
        assert cli._get_preferred_breakdown_metrics() is cli.agent._last_context_metrics

    def test_render_source_bar_width_affects_output(self):
        """T14.5: width param is accepted without error (used for future bar rendering)."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [MockSourceMetrics("identity", 100, 400)],
            total_estimated_tokens=100,
        )
        # Width parameter is accepted; label format is independent of width
        result = cli._render_source_bar(metrics, width=20)
        assert "identity:100%" in result
        assert "█" not in result  # no block characters in new label format

    def test_render_source_bar_adaptive_width_param(self):
        """T14.6: label shows source proportions as percentages."""
        cli = self._make_cli()
        metrics = MockContextMetrics(
            [MockSourceMetrics("memory", 25, 100)],
            total_estimated_tokens=100,
        )
        # memory=25% -> "memory:25%" label
        result = cli._render_source_bar(metrics, width=20)
        assert "memory:25%" in result
