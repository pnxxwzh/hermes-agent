"""Tests for context_engine.assembler."""

import logging
import pytest
from unittest.mock import MagicMock, patch

from agent.context_engine import registry as registry_module
from agent.context_engine.assembler import (
    ContextAssembler,
    STABLE_FACTORIES,
    DYNAMIC_FACTORIES,
    _identity_factory,
    _project_context_factory,
)
from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import ContextChunk, AssemblyResult


def _make_chunk(source, stage="stable", content="x" * 12):
    return ContextChunk(
        source=source,
        stage=stage,
        slot=source,
        priority=1,
        content=content,
    )


class TestAssemblerStable:
    """T9: stable assembly."""

    def test_assemble_stable_calls_all_factories(self):
        """T9.1: all stable factories are called."""
        mock_agent = MagicMock()
        calls = []

        def tracking_factory(name):
            def factory(ctx):
                calls.append(name)
                return []
            return factory
        factories = [(n, tracking_factory(n)) for n, _ in STABLE_FACTORIES]

        assembler = ContextAssembler(mock_agent, stable_factories=factories)
        assembler.assemble_stable()
        assert len(calls) == len(STABLE_FACTORIES)

    def test_assemble_stable_skips_empty_results(self):
        """T9.2: empty list returns are skipped, no duplicate chunks."""
        mock_agent = MagicMock()
        factories = [
            ("empty", lambda ctx: []),
            ("chunk", lambda ctx: [_make_chunk("chunk")]),
            ("also_empty", lambda ctx: []),
        ]
        assembler = ContextAssembler(mock_agent, stable_factories=factories)
        result = assembler.assemble_stable()
        assert len(result.stable_chunks) == 1
        assert result.stable_chunks[0].source == "chunk"

    def test_assemble_stable_exception_in_factory(self):
        """T9.3: single factory exception does not terminate assembly."""
        mock_agent = MagicMock()
        factories = [
            ("ok", lambda ctx: [_make_chunk("ok")]),
            ("boom", lambda ctx: (_ for _ in ()).throw(RuntimeError("test"))),
            ("also_ok", lambda ctx: [_make_chunk("also_ok")]),
        ]
        assembler = ContextAssembler(mock_agent, stable_factories=factories)
        result = assembler.assemble_stable()
        assert len(result.stable_chunks) == 2
        sources = {c.source for c in result.stable_chunks}
        assert sources == {"ok", "also_ok"}

    def test_assemble_stable_logs_factory_exception(self, caplog):
        """T9.x: factory failures are logged with the source name."""
        mock_agent = MagicMock()
        caplog.set_level(logging.WARNING)
        assembler = ContextAssembler(
            mock_agent,
            stable_factories=[
                ("boom", lambda ctx: (_ for _ in ()).throw(RuntimeError("kaboom"))),
            ],
        )

        result = assembler.assemble_stable()

        assert result.stable_chunks == []
        assert "Context source 'boom' failed during assembly" in caplog.text

    def test_assemble_stable_none_system_message(self):
        """T9.4: system_message=None does not crash."""
        mock_agent = MagicMock()
        mock_agent.ephemeral_system_prompt = ""
        # Use custom empty factories to isolate from compat wrapper failures
        def identity(ctx): return [_make_chunk("identity", content="id")]
        def empty(ctx): return []
        assembler = ContextAssembler(
            mock_agent,
            stable_factories=[("identity", identity)],
            dynamic_factories=[("e", empty)],
        )
        result = assembler.assemble_stable(system_message=None)
        assert isinstance(result, AssemblyResult)

    def test_assemble_stable_source_order(self):
        """T9.13: chunks preserve factory registration order."""
        mock_agent = MagicMock()
        factories = [
            ("first", lambda ctx: [_make_chunk("first", content="a" * 12)]),
            ("second", lambda ctx: [_make_chunk("second", content="b" * 12)]),
            ("third", lambda ctx: [_make_chunk("third", content="c" * 12)]),
        ]
        assembler = ContextAssembler(mock_agent, stable_factories=factories)
        result = assembler.assemble_stable()
        assert [c.source for c in result.stable_chunks] == ["first", "second", "third"]

    def test_assemble_stable_respects_skip_context_files(self):
        """T9.x: skip_context_files disables SOUL and project-context sources."""
        mock_agent = MagicMock()
        mock_agent.skip_context_files = True
        mock_agent._context_cwd = "/workspace"
        mock_agent._honcho_config = None
        mock_agent.model = None
        mock_agent.provider = None
        mock_agent.session_id = None
        mock_agent.platform = None
        mock_agent.pass_session_id = False

        with (
            patch("agent.context_engine.sources.wrap_load_soul_md", return_value=("SOUL", None)) as mock_soul,
            patch(
                "agent.context_engine.compat.wrap_build_context_files_prompt",
                return_value=("# Project Context", None),
            ) as mock_context,
        ):
            assembler = ContextAssembler(
                mock_agent,
                stable_factories=[
                    ("identity", _identity_factory),
                    ("project_context", _project_context_factory),
                ],
            )
            result = assembler.assemble_stable()

        assert "SOUL" not in result.stable_system
        assert "# Project Context" not in result.stable_system
        mock_soul.assert_not_called()
        mock_context.assert_not_called()

    def test_assemble_stable_does_not_duplicate_soul(self):
        """T9.x: SOUL belongs to identity slot only, not project context too."""
        mock_agent = MagicMock()
        mock_agent.skip_context_files = False
        mock_agent._context_cwd = "/workspace"
        mock_agent._honcho_config = None
        mock_agent.model = None
        mock_agent.provider = None
        mock_agent.session_id = None
        mock_agent.platform = None
        mock_agent.pass_session_id = False

        with (
            patch("agent.context_engine.sources.wrap_load_soul_md", return_value=("SOUL", None)),
            patch(
                "agent.context_engine.compat.wrap_build_context_files_prompt",
                return_value=("# Project Context", None),
            ) as mock_context,
        ):
            assembler = ContextAssembler(
                mock_agent,
                stable_factories=[
                    ("identity", _identity_factory),
                    ("project_context", _project_context_factory),
                ],
            )
            result = assembler.assemble_stable()

        assert result.stable_system.count("SOUL") == 1
        mock_context.assert_called_once_with(cwd="/workspace", skip_soul=True)

    def test_assemble_stable_includes_registered_factories(self):
        """T9.x: registry-based stable factories are appended to default assembly."""
        mock_agent = MagicMock()
        mock_agent.skip_context_files = True
        mock_agent._honcho_config = None
        mock_agent.valid_tool_names = []
        mock_agent._tool_use_enforcement = None
        mock_agent._honcho = None
        mock_agent._memory_store = None
        mock_agent._memory_enabled = False
        mock_agent._user_profile_enabled = False
        mock_agent._context_cwd = None
        mock_agent.model = None
        mock_agent.provider = None
        mock_agent.session_id = None
        mock_agent.platform = None
        mock_agent.pass_session_id = False

        @registry_module.register_stable("registered_stable")
        def _registered(ctx):
            return [_make_chunk("registered_stable", content="registered")]

        try:
            assembler = ContextAssembler(mock_agent)
            result = assembler.assemble_stable()
            assert any(c.source == "registered_stable" for c in result.stable_chunks)
        finally:
            registry_module.STABLE_SOURCE_FACTORIES.pop()


class TestAssemblerDynamic:
    """T9: dynamic assembly."""

    def test_assemble_dynamic_calls_all_factories(self):
        """T9.5: all dynamic factories are called."""
        mock_agent = MagicMock()
        calls = []

        def tracking_factory(name):
            def factory(ctx):
                calls.append(name)
                return []
            return factory
        factories = [(n, tracking_factory(n)) for n, _ in DYNAMIC_FACTORIES]

        assembler = ContextAssembler(mock_agent, dynamic_factories=factories)
        assembler.assemble_dynamic()
        assert len(calls) == len(DYNAMIC_FACTORIES)

    def test_assemble_dynamic_empty_user_message(self):
        """T9.6: user_message=None does not crash."""
        mock_agent = MagicMock()
        # Use custom mock factories to bypass compat wrappers entirely
        def ephemeral(ctx): return [_make_chunk("ephemeral", stage="dynamic")]
        def plugin(ctx): return []
        def sparkgraph(ctx): return []
        def honcho_turn(ctx): return []
        assembler = ContextAssembler(
            mock_agent,
            stable_factories=[],
            dynamic_factories=[
                ("ephemeral", ephemeral),
                ("plugin", plugin),
                ("sparkgraph_recall", sparkgraph),
                ("honcho_turn", honcho_turn),
            ],
        )
        result = assembler.assemble_dynamic(user_message=None)
        assert isinstance(result, AssemblyResult)

    def test_assemble_dynamic_empty_conversation_history(self):
        """T9.7: conversation_history=None -> []."""
        mock_agent = MagicMock()
        def ephemeral(ctx): return [_make_chunk("ephemeral", stage="dynamic")]
        def plugin(ctx): return []
        def sparkgraph(ctx): return []
        def honcho_turn(ctx): return []
        assembler = ContextAssembler(
            mock_agent,
            stable_factories=[],
            dynamic_factories=[
                ("ephemeral", ephemeral),
                ("plugin", plugin),
                ("sparkgraph_recall", sparkgraph),
                ("honcho_turn", honcho_turn),
            ],
        )
        result = assembler.assemble_dynamic(conversation_history=None)
        assert isinstance(result, AssemblyResult)

    def test_assemble_dynamic_source_order(self):
        """T9.14: dynamic chunks preserve factory order."""
        mock_agent = MagicMock()
        factories = [
            ("d1", lambda ctx: [_make_chunk("d1", stage="dynamic", content="a" * 12)]),
            ("d2", lambda ctx: [_make_chunk("d2", stage="dynamic", content="b" * 12)]),
        ]
        assembler = ContextAssembler(mock_agent, dynamic_factories=factories)
        result = assembler.assemble_dynamic()
        assert [c.source for c in result.dynamic_chunks] == ["d1", "d2"]

    def test_default_dynamic_factories_include_honcho_turn(self):
        """T9.x: generic ContextAssembler includes honcho turn context by default."""
        mock_agent = MagicMock()
        mock_agent._honcho = MagicMock()
        mock_agent._honcho.get_turn_context.return_value = "HONCHO TURN"
        mock_agent.session_id = None
        mock_agent._sparkgraph_manager = None
        mock_agent._sparkgraph_enabled = False
        mock_agent._plugin_turn_context_ready = False
        mock_agent._plugin_turn_context = ""
        mock_agent._sparkgraph_turn_context_ready = False
        mock_agent._sparkgraph_turn_context = ""
        mock_agent.ephemeral_system_prompt = ""
        mock_agent.model = ""
        mock_agent.platform = ""

        assembler = ContextAssembler(mock_agent)
        result = assembler.assemble_dynamic(
            user_message="hello",
            conversation_history=[],
        )

        assert any(c.source == "honcho_turn" for c in result.dynamic_chunks)
        assert "HONCHO TURN" in result.dynamic_system


class TestAssemblerAll:
    """T9: assemble_all convenience method."""

    def test_assemble_all_combines_chunks(self):
        """T9.8: stable + dynamic chunks both in result."""
        mock_agent = MagicMock()
        stable_factories = [
            ("s1", lambda ctx: [_make_chunk("s1", stage="stable", content="stable_content")]),
        ]
        dynamic_factories = [
            ("d1", lambda ctx: [_make_chunk("d1", stage="dynamic", content="dynamic_content")]),
        ]
        assembler = ContextAssembler(
            mock_agent,
            stable_factories=stable_factories,
            dynamic_factories=dynamic_factories,
        )
        result = assembler.assemble_all()
        all_sources = {c.source for c in result.stable_chunks + result.dynamic_chunks}
        assert all_sources == {"s1", "d1"}

    def test_assemble_all_effective_system_format(self):
        """T9.9: effective_system = stable + "\n\n" + dynamic."""
        mock_agent = MagicMock()
        stable_factories = [
            ("s1", lambda ctx: [_make_chunk("s1", stage="stable", content="STABLE")]),
        ]
        dynamic_factories = [
            ("d1", lambda ctx: [_make_chunk("d1", stage="dynamic", content="DYNAMIC")]),
        ]
        assembler = ContextAssembler(
            mock_agent,
            stable_factories=stable_factories,
            dynamic_factories=dynamic_factories,
        )
        result = assembler.assemble_all()
        assert result.effective_system == "STABLE\n\nDYNAMIC"

    def test_assemble_all_effective_system_only_stable(self):
        """T9.x: effective_system when only stable has content."""
        mock_agent = MagicMock()
        stable_factories = [
            ("s1", lambda ctx: [_make_chunk("s1", stage="stable", content="ONLY_STABLE")]),
        ]
        dynamic_factories = [
            ("d1", lambda ctx: [_make_chunk("d1", stage="dynamic", content="")]),
        ]
        assembler = ContextAssembler(
            mock_agent,
            stable_factories=stable_factories,
            dynamic_factories=dynamic_factories,
        )
        result = assembler.assemble_all()
        assert result.effective_system == "ONLY_STABLE"


class TestAssemblerMetrics:
    """T9: token metrics in AssemblyResult."""

    def test_assemble_result_metrics_has_all_sources(self):
        """T9.10: by_source includes all non-empty sources."""
        mock_agent = MagicMock()
        factories = [
            ("a", lambda ctx: [_make_chunk("a", content="a" * 12)]),
            ("b", lambda ctx: [_make_chunk("b", content="b" * 12)]),
        ]
        assembler = ContextAssembler(mock_agent, stable_factories=factories, dynamic_factories=[])
        result = assembler.assemble_stable()
        source_names = {sm.source for sm in result.metrics.by_source}
        assert source_names == {"a", "b"}

    def test_assemble_result_metrics_stable_dynamic_split(self):
        """T9.11: stable_tokens / dynamic_tokens correctly split."""
        mock_agent = MagicMock()
        stable_factories = [
            ("stable_src", lambda ctx: [_make_chunk("stable_src", stage="stable", content="s" * 8)]),
        ]
        dynamic_factories = [
            ("dynamic_src", lambda ctx: [_make_chunk("dynamic_src", stage="dynamic", content="d" * 8)]),
        ]
        assembler = ContextAssembler(
            mock_agent,
            stable_factories=stable_factories,
            dynamic_factories=dynamic_factories,
        )
        result = assembler.assemble_all()
        # 8 chars = 2 tokens each
        assert result.metrics.stable_tokens == 2
        assert result.metrics.dynamic_tokens == 2


class TestAssemblerCustom:
    """T9: custom factory injection."""

    def test_assembler_custom_factories(self):
        """T9.12: passing mock_factories replaces defaults."""
        mock_agent = MagicMock()
        custom_factories = [
            ("custom", lambda ctx: [_make_chunk("custom")]),
        ]
        assembler = ContextAssembler(
            mock_agent,
            stable_factories=custom_factories,
            dynamic_factories=[],
        )
        result = assembler.assemble_stable()
        assert len(result.stable_chunks) == 1
        assert result.stable_chunks[0].source == "custom"

    def test_assembler_empty_factories(self):
        """T9.x: empty factory list -> empty chunks."""
        mock_agent = MagicMock()
        assembler = ContextAssembler(
            mock_agent,
            stable_factories=[],
            dynamic_factories=[],
        )
        result = assembler.assemble_all()
        assert result.stable_chunks == []
        assert result.dynamic_chunks == []
        assert result.effective_system == ""

    def test_assemble_dynamic_none_chunks_in_metrics(self):
        """T9.x: dynamic_tokens=0 when no dynamic factories."""
        mock_agent = MagicMock()
        assembler = ContextAssembler(
            mock_agent,
            stable_factories=[
                ("s", lambda ctx: [_make_chunk("s", content="x" * 12)]),
            ],
            dynamic_factories=[],
        )
        result = assembler.assemble_all()
        assert result.metrics.dynamic_tokens == 0
        assert result.metrics.stable_tokens == 3
