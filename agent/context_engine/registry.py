"""Explicit source registry for Phase 1."""

from __future__ import annotations

from typing import Callable, Literal

from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import ContextChunk

# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

STABLE_SOURCE_FACTORIES: list[tuple[str, Callable[[AssemblyContext], list[ContextChunk]]]] = []
DYNAMIC_SOURCE_FACTORIES: list[tuple[str, Callable[[AssemblyContext], list[ContextChunk]]]] = []


def _register(
    name: str,
    factory: Callable[[AssemblyContext], list[ContextChunk]],
    registry: list,
) -> None:
    """Register a source factory.

    Raises ValueError if name is already registered in this registry.
    """
    for existing_name, _ in registry:
        if existing_name == name:
            raise ValueError(f"Source {name!r} already registered in this registry")
    registry.append((name, factory))


def register_stable(name: str) -> Callable:
    """Decorator: register a stable source factory."""
    def decorator(fn: Callable[[AssemblyContext], list[ContextChunk]]):
        _register(name, fn, STABLE_SOURCE_FACTORIES)
        return fn
    return decorator


def register_dynamic(name: str) -> Callable:
    """Decorator: register a dynamic source factory."""
    def decorator(fn: Callable[[AssemblyContext], list[ContextChunk]]):
        _register(name, fn, DYNAMIC_SOURCE_FACTORIES)
        return fn
    return decorator
