"""
SparkGraph Personalized PageRank (PPR)

Core idea (aligned with gm):
  - Personalized PageRank: seed nodes are the teleport target; nodes near seeds score higher
  - Global PageRank: uniform teleport across all nodes (used as fallback baseline)

Key parameters:
  - damping = 0.85
  - iterations = 30
  - cache_ttl = 30_000 ms (graph structure cache)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.sparkgraph.store import SparkGraphStore

# ─── Global graph-structure cache ─────────────────────────────────

_CACHE_TTL_MS = 30_000


class _GraphCache:
    """Process-local graph-structure cache. TTL=30s, invalidated on compact."""

    __slots__ = ("adj", "node_ids", "N", "cached_at_ms")

    def __init__(
        self,
        adj: dict[str, list[str]],
        node_ids: set[str],
        N: int,
        cached_at_ms: int,
    ):
        self.adj = adj
        self.node_ids = node_ids
        self.N = N
        self.cached_at_ms = cached_at_ms

    def is_fresh(self) -> bool:
        return (time.time() * 1000 - self.cached_at_ms) < _CACHE_TTL_MS


_graph_cache: _GraphCache | None = None


def invalidate_graph_cache() -> None:
    """Call after compact or schema changes to force a reload."""
    global _graph_cache
    _graph_cache = None


def _load_graph(store: "SparkGraphStore") -> _GraphCache:
    """Load undirected adjacency table from the database with 30-second cache."""
    global _graph_cache
    if _graph_cache is not None and _graph_cache.is_fresh():
        return _graph_cache

    rows = store._conn.execute(
        "SELECT id FROM sg_nodes WHERE status = ?",
        ("active",),
    ).fetchall()
    node_ids = {row["id"] for row in rows}

    edge_rows = store._conn.execute(
        "SELECT from_id, to_id FROM sg_edges"
    ).fetchall()

    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for e in edge_rows:
        fid, tid = e["from_id"], e["to_id"]
        if fid not in node_ids or tid not in node_ids:
            continue
        adj[fid].append(tid)
        adj[tid].append(fid)

    _graph_cache = _GraphCache(
        adj, node_ids, len(node_ids), int(time.time() * 1000)
    )
    return _graph_cache


# ─── Personalized PageRank ───────────────────────────────────────


def personalized_pagerank(
    store: "SparkGraphStore",
    seed_ids: list[str],
    candidate_ids: list[str],
    *,
    damping: float = 0.85,
    iterations: int = 30,
) -> dict[str, float]:
    """
    Personalized PageRank — propagate influence from seed nodes to candidates.

    Algorithm (aligned with gm):
      1. Initialise: only seed_ids have non-zero rank = 1/|seeds|
      2. Each iteration:
         a. teleport component: (1-d) * teleport_weight — flows back to seeds only
         b. propagation component: d * rank[n] / |neighbors| — evenly to all neighbours
         c. dangling nodes (no neighbours): their rank flows back to seeds
      3. Repeat for `iterations` passes
      4. Return scores for candidate_ids only

    Parameters:
      store: SparkGraphStore instance
      seed_ids: Query-hit node IDs (direct FTS + vector hits)
      candidate_ids: Nodes to score (direct + vector + related)
      damping: Damping factor (default 0.85)
      iterations: Power-iteration count (default 30)

    Returns:
      {node_id: ppr_score} — only includes nodes present in candidate_ids
    """
    graph = _load_graph(store)
    adj, node_ids, N = graph.adj, graph.node_ids, graph.N

    if N == 0 or not seed_ids:
        return {}

    valid_seeds = [sid for sid in seed_ids if sid in node_ids]
    if not valid_seeds:
        return {}

    teleport_weight = 1.0 / len(valid_seeds)
    seed_set = set(valid_seeds)

    # Initialise rank: seeds = 1/|seeds|, others = 0
    rank: dict[str, float] = {
        nid: (teleport_weight if nid in seed_set else 0.0) for nid in node_ids
    }

    # Power iteration
    for _ in range(iterations):
        new_rank: dict[str, float] = {
            nid: (1.0 - damping) * teleport_weight if nid in seed_set else 0.0
            for nid in node_ids
        }

        # Propagate from each node to its neighbours
        for node_id, neighbors in adj.items():
            if not neighbors:
                continue
            contrib = rank[node_id] / len(neighbors)
            if contrib == 0.0:
                continue
            for nb in neighbors:
                new_rank[nb] += damping * contrib

        # Dangling nodes: all their rank flows back to seeds
        dangling_sum = sum(rank[n] for n, nb in adj.items() if not nb)
        if dangling_sum > 0.0:
            dangling_contrib = damping * dangling_sum * teleport_weight
            for sid in valid_seeds:
                new_rank[sid] += dangling_contrib

        rank = new_rank

    return {nid: rank.get(nid, 0.0) for nid in candidate_ids if nid in node_ids}


# ─── Global PageRank ───────────────────────────────────────────


def compute_global_pagerank(
    store: "SparkGraphStore",
    *,
    damping: float = 0.85,
    iterations: int = 30,
) -> dict[str, float]:
    """
    Global PageRank — uniform teleport across all active nodes.

    Uses:
      - Recall fallback baseline (top_nodes by pagerank)
      - Future: write back to sg_nodes.pagerank column
    """
    graph = _load_graph(store)
    adj, node_ids, N = graph.adj, graph.node_ids, graph.N

    if N == 0:
        return {}

    init_rank = 1.0 / N
    rank: dict[str, float] = {nid: init_rank for nid in node_ids}

    for _ in range(iterations):
        base = (1.0 - damping) / N
        new_rank: dict[str, float] = {nid: base for nid in node_ids}

        for node_id, neighbors in adj.items():
            if not neighbors:
                continue
            contrib = rank[node_id] / len(neighbors)
            for nb in neighbors:
                new_rank[nb] += damping * contrib

        # Dangling nodes: evenly distribute to all nodes
        dangling_sum = sum(rank[n] for n, nb in adj.items() if not nb)
        if dangling_sum > 0.0:
            dc = damping * dangling_sum / N
            for nid in node_ids:
                new_rank[nid] += dc

        rank = new_rank

    return rank
