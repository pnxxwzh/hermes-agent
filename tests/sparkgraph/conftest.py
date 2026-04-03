"""Pytest configuration for sparkgraph tests — invalidate PPR cache per test."""

import pytest


@pytest.fixture(autouse=True)
def fresh_ppr_cache():
    """Ensure the global PPR graph cache is cleared before each test."""
    from agent.sparkgraph.pagerank import invalidate_graph_cache

    invalidate_graph_cache()
    yield
    invalidate_graph_cache()
