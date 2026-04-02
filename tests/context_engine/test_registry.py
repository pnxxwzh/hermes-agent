"""Tests for context_engine.registry."""

import pytest

from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import ContextChunk
from agent.context_engine import registry as registry_module


class TestRegistry:
    """T3: Source registration."""

    def test_register_stable_decorator(self):
        """T3.1: register_stable decorator adds to list."""
        initial_len = len(registry_module.STABLE_SOURCE_FACTORIES)
        @registry_module.register_stable("test_source_a")
        def factory(ctx: AssemblyContext) -> list[ContextChunk]:
            return []
        assert len(registry_module.STABLE_SOURCE_FACTORIES) == initial_len + 1
        names = [n for n, _ in registry_module.STABLE_SOURCE_FACTORIES]
        assert "test_source_a" in names
        # cleanup
        registry_module.STABLE_SOURCE_FACTORIES.pop()

    def test_register_dynamic_decorator(self):
        """T3.2: register_dynamic decorator adds to list."""
        initial_len = len(registry_module.DYNAMIC_SOURCE_FACTORIES)
        @registry_module.register_dynamic("test_source_b")
        def factory(ctx: AssemblyContext) -> list[ContextChunk]:
            return []
        assert len(registry_module.DYNAMIC_SOURCE_FACTORIES) == initial_len + 1
        names = [n for n, _ in registry_module.DYNAMIC_SOURCE_FACTORIES]
        assert "test_source_b" in names
        # cleanup
        registry_module.DYNAMIC_SOURCE_FACTORIES.pop()

    def test_register_duplicate_name_raises(self):
        """T3.3: same name in same registry raises ValueError."""
        @registry_module.register_stable("dup_test")
        def factory1(ctx: AssemblyContext) -> list[ContextChunk]:
            return []
        with pytest.raises(ValueError, match="already registered"):
            @registry_module.register_stable("dup_test")
            def factory2(ctx: AssemblyContext) -> list[ContextChunk]:
                return []
        # cleanup
        for i, (n, _) in enumerate(registry_module.STABLE_SOURCE_FACTORIES):
            if n == "dup_test":
                registry_module.STABLE_SOURCE_FACTORIES.pop(i)
                break

    def test_same_name_different_lists(self):
        """T3.4: same name in stable and dynamic does not conflict."""
        @registry_module.register_stable("shared_name")
        def factory_s(ctx: AssemblyContext) -> list[ContextChunk]:
            return []
        @registry_module.register_dynamic("shared_name")
        def factory_d(ctx: AssemblyContext) -> list[ContextChunk]:
            return []
        # Both should exist in their respective lists
        stable_names = [n for n, _ in registry_module.STABLE_SOURCE_FACTORIES]
        dynamic_names = [n for n, _ in registry_module.DYNAMIC_SOURCE_FACTORIES]
        assert "shared_name" in stable_names
        assert "shared_name" in dynamic_names
        # cleanup
        registry_module.STABLE_SOURCE_FACTORIES.pop()
        registry_module.DYNAMIC_SOURCE_FACTORIES.pop()

    def test_decorator_returns_original_function(self):
        """T3.x: decorator returns the original callable."""
        def my_factory(ctx: AssemblyContext) -> list[ContextChunk]:
            return []
        decorated = registry_module.register_stable("orig_fn_test")(my_factory)
        assert decorated is my_factory
        # cleanup
        registry_module.STABLE_SOURCE_FACTORIES.pop()

    def test_register_stable_twice_different_names(self):
        """T3.x: different names in same registry works."""
        @registry_module.register_stable("name_x")
        def fx(ctx: AssemblyContext) -> list[ContextChunk]:
            return []
        @registry_module.register_stable("name_y")
        def fy(ctx: AssemblyContext) -> list[ContextChunk]:
            return []
        names = [n for n, _ in registry_module.STABLE_SOURCE_FACTORIES]
        assert "name_x" in names
        assert "name_y" in names
        # cleanup
        registry_module.STABLE_SOURCE_FACTORIES.pop()
        registry_module.STABLE_SOURCE_FACTORIES.pop()
