"""SparkGraph retrieval helpers for dynamic recall injection."""

from __future__ import annotations

import json as _json
from dataclasses import dataclass
from typing import Any, Collection

from agent.sparkgraph.config import SparkGraphEmbeddingConfig
from agent.sparkgraph.embedding import cosine_similarity, create_embedding, embedding_enabled
from agent.sparkgraph.pagerank import invalidate_graph_cache, personalized_pagerank
from agent.sparkgraph.store import SparkGraphStore
from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType, RecallChannel

# ─── Recall thresholds ────────────────────────────────────────────
# L1 DIRECT — active nodes
MIN_RECALL_CONFIDENCE_ACTIVE = 0.60
MIN_RECALL_STABILITY_ACTIVE = 0.60
# L1 DIRECT — candidate nodes (must also satisfy evidence threshold)
CANDIDATE_DIRECT_EVIDENCE_THRESHOLD = 1
# L3 COLD — zero-evidence candidate fallback
COLD_START_CONFIDENCE_THRESHOLD = 0.40
COLD_START_STABILITY_THRESHOLD = 0.40

# ─── Vector thresholds ───────────────────────────────────────────
MIN_VECTOR_SIMILARITY = 0.55
SHORT_QUERY_VECTOR_SIMILARITY = 0.68
LOW_SIGNAL_RECALL_TOKENS = {
    "ahoy",
    "aloha",
    "ciao",
    "goodbye",
    "hallo",
    "hello",
    "hey",
    "hi",
    "hola",
    "howdy",
    "ok",
    "okay",
    "sup",
    "thanks",
    "thank",
    "thx",
    "yo",
}

# ─── PPR constants ───────────────────────────────────────────────
PPR_DAMPING = 0.85
PPR_ITERATIONS = 30

# ─── Default source-kind weights (aligned with gm) ───────────────
_SOURCE_SCORES: dict[str, float] = {
    "explicit": 80.0,
    "manual": 40.0,
    "reflection": 0.0,
    "review": 40.0,
    "flush": 0.0,
    "auto": 0.0,
    "shadow": 0.0,
}


@dataclass(frozen=True)
class RecallConfig:
    search_limit: int = 8
    related_limit: int = 4
    max_nodes: int = 4
    vector_limit: int = 24


# ─── Query pre-processing ────────────────────────────────────────


def _query_terms(query: str) -> list[str]:
    return [
        token
        for token in "".join(
            ch.lower() if (ch.isalnum() or ch.isspace()) else " "
            for ch in str(query or "")
        ).split()
        if token
    ]


def _is_low_signal_query(query: str) -> bool:
    terms = _query_terms(query)
    if not terms:
        return True
    return len(terms) <= 2 and all(term in LOW_SIGNAL_RECALL_TOKENS for term in terms)


def _min_vector_similarity(query: str) -> float:
    terms = _query_terms(query)
    if len(terms) <= 1:
        return SHORT_QUERY_VECTOR_SIMILARITY
    return MIN_VECTOR_SIMILARITY


# ─── Evidence batch helper ────────────────────────────────────────


def batch_get_evidence_counts(store: SparkGraphStore, node_ids: list[str]) -> dict[str, int]:
    """Batch-query evidence counts for a list of node IDs.

    Returns {node_id: count} for every node that has at least one evidence row.
    Nodes with zero evidence are simply absent from the dict (caller should
    treat absence as 0).
    """
    if not node_ids:
        return {}
    placeholders = ", ".join("?" * len(node_ids))
    rows = store._conn.execute(
        f"""
        SELECT node_id, COUNT(*) AS cnt
        FROM sg_evidence
        WHERE node_id IN ({placeholders})
        GROUP BY node_id
        """,
        list(node_ids),
    ).fetchall()
    return {str(row["node_id"]): int(row["cnt"]) for row in rows}


# ─── Recall eligibility (three-channel architecture) ────────────


def _is_recall_eligible(
    node: dict[str, Any],
    channel: RecallChannel,
    config: RecallConfig | None = None,
) -> bool:
    """Three-channel recall eligibility check.

    L1 DIRECT
      - active  → confidence >= 0.60, stability >= 0.60
      - candidate → evidence >= 1 AND confidence >= 0.40

    L2 GRAPH
      - active only (strict) — prevents candidate nodes polluting graph traversal

    L3 COLD
      - candidate with zero evidence AND confidence >= 0.40, stability >= 0.40
      - pure cold-start discovery channel
    """
    status = node.get("status")
    config = config or RecallConfig()

    if channel == RecallChannel.GRAPH:
        # L2: Graph expansion — active nodes only, no exceptions
        return status == NodeStatus.ACTIVE.value

    if channel == RecallChannel.COLD:
        # L3: Cold-start fallback — zero-evidence candidate, confidence floor
        return (
            status == NodeStatus.CANDIDATE.value
            and float(node.get("confidence") or 0.0) >= COLD_START_CONFIDENCE_THRESHOLD
            and float(node.get("stability") or 0.0) >= COLD_START_STABILITY_THRESHOLD
            and node.get("_evidence_count", 0) == 0
        )

    # L1 DIRECT
    if status == NodeStatus.ACTIVE.value:
        return (
            float(node.get("confidence") or 0.0) >= MIN_RECALL_CONFIDENCE_ACTIVE
            and float(node.get("stability") or 0.0) >= MIN_RECALL_STABILITY_ACTIVE
        )
    if status == NodeStatus.CANDIDATE.value:
        # Candidate nodes are reachable via direct search only when they have
        # at least one piece of supporting evidence (they were useful before).
        return (
            node.get("_evidence_count", 0) >= CANDIDATE_DIRECT_EVIDENCE_THRESHOLD
            and float(node.get("confidence") or 0.0) >= COLD_START_CONFIDENCE_THRESHOLD
        )

    return False


# ─── Sorting helpers ──────────────────────────────────────────────


def _sort_key(node: dict[str, Any]) -> tuple[float, float, float, int]:
    return (
        float(node.get("confidence") or 0.0),
        float(node.get("reuse_score") or 0.0),
        float(node.get("stability") or 0.0),
        int(node.get("updated_at") or 0),
    )


def _recall_rank(node: dict[str, Any]) -> tuple[float, float, float, float, float, int]:
    return (
        float(node.get("_match_priority") or 0.0),
        float(node.get("semantic_score") or 0.0),
        float(node.get("_lexical_score") or 0.0),
        *_sort_key(node),
    )


def _node_priority_ppr(
    node: dict[str, Any],
    ppr_score: float,
    evidence_count: int,
    source_scores: dict[str, float] | None = None,
) -> float:
    """PPR-based priority score for a node (aligned with gm nodePriority)."""
    if source_scores is None:
        source_scores = _SOURCE_SCORES

    score = ppr_score * 1000.0
    source_kind = str(node.get("source_kind") or "")
    score += source_scores.get(source_kind, 0.0)

    if source_kind == "reflection" and ppr_score < 0.01:
        score += 800.0

    score += float(node.get("confidence") or 0.0) * 100.0
    score += min(evidence_count, 20) * 5.0

    # superseded penalty
    meta = node.get("meta") or {}
    if isinstance(meta, str):
        meta = _json.loads(meta)
    if meta.get("superseded_by") or meta.get("superseded"):
        score -= 500.0

    return score


def _rank_with_ppr(
    nodes: list[dict[str, Any]],
    ppr_scores: dict[str, float],
    evidence_counts: dict[str, int],
    source_scores: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Rank nodes using PPR scores."""
    scored: list[tuple[float, float, float, int, dict[str, Any]]] = []
    for node in nodes:
        nid = str(node.get("id") or "")
        ppr = ppr_scores.get(nid, 0.0)
        node_copy = dict(node)
        node_copy["_ppr_score"] = ppr
        priority = _node_priority_ppr(
            node_copy,
            ppr,
            evidence_count=evidence_counts.get(nid, 0),
            source_scores=source_scores,
        )
        semantic = float(node.get("semantic_score") or 0.0)
        conf = float(node.get("confidence") or 0.0)
        updated = int(node.get("updated_at") or 0)
        scored.append((priority, semantic, conf, updated, node_copy))

    scored.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
    return [item[4] for item in scored]


def _rank_candidates(
    nodes: list[dict[str, Any]],
    evidence_counts: dict[str, int],
    use_ppr: bool = False,
    seed_ids: list[str] | None = None,
    store: SparkGraphStore | None = None,
) -> list[dict[str, Any]]:
    """Rank a candidate list using PPR or legacy sort."""
    if use_ppr and seed_ids and store:
        candidate_ids = [str(n.get("id", "")) for n in nodes if n.get("id")]
        try:
            ppr_scores = personalized_pagerank(
                store,
                seed_ids=seed_ids,
                candidate_ids=candidate_ids,
                damping=PPR_DAMPING,
                iterations=PPR_ITERATIONS,
            )
            return _rank_with_ppr(nodes, ppr_scores, evidence_counts)
        except Exception:
            pass
    return sorted(nodes, key=_recall_rank, reverse=True)


# ─── Hit merging ─────────────────────────────────────────────────


def _merge_hit(
    merged: dict[str, dict[str, Any]],
    node: dict[str, Any],
    *,
    match_priority: int,
    lexical_score: float = 0.0,
    semantic_score: float = 0.0,
) -> None:
    node_id = str(node.get("id") or "")
    if not node_id:
        return

    existing = merged.get(node_id)
    if existing is None:
        enriched = dict(node)
        enriched["_match_priority"] = float(match_priority)
        enriched["_lexical_score"] = float(lexical_score)
        if semantic_score > 0.0:
            enriched["semantic_score"] = float(semantic_score)
        merged[node_id] = enriched
        return

    existing["_match_priority"] = max(
        float(existing.get("_match_priority") or 0.0),
        float(match_priority),
    )
    existing["_lexical_score"] = max(
        float(existing.get("_lexical_score") or 0.0),
        float(lexical_score),
    )
    existing["semantic_score"] = max(
        float(existing.get("semantic_score") or 0.0),
        float(semantic_score),
    )


# ─── L2 Graph expansion helper ──────────────────────────────────


def _get_related_hits(
    store: SparkGraphStore,
    seed_ids: Collection[str],
    config: RecallConfig,
) -> list[dict[str, Any]]:
    """Fetch 1-hop neighbours of seed nodes for L2 GRAPH channel.

    L2 is always active-only — candidate nodes must not diffuse through
    the graph traversal path.
    """
    seed_list = list(seed_ids)
    if not seed_list:
        return []

    # Pull active neighbours via the store's existing helper, then
    # re-filter through _is_recall_eligible(..., GRAPH) as a belt-and-suspenders
    # check (the store's active_only param already enforces this).
    raw = store.get_related_nodes(
        node_ids=seed_list,
        active_only=True,
        limit=config.related_limit,
    )
    return [n for n in raw if _is_recall_eligible(n, RecallChannel.GRAPH, config)]


# ─── Main recall entry point (three-layer architecture) ──────────


def recall_nodes(
    store: SparkGraphStore,
    *,
    query: str,
    config: RecallConfig | None = None,
    embedding_config: SparkGraphEmbeddingConfig | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Three-layer recall.

    Layer 1 (L1) — DIRECT
      FTS + vector search → raw hits → _is_recall_eligible(..., DIRECT)
      active nodes: confidence>=0.60, stability>=0.60
      candidate nodes: evidence>=1 AND confidence>=0.40

    Layer 2 (L2) — GRAPH
      1-hop neighbours of L1 seeds → _is_recall_eligible(..., GRAPH)
      active-only (strict)

    Layer 3 (L3) — COLD
      If L1+L2 result count < max_nodes, fill remaining slots with
      zero-evidence candidates that have confidence>=0.40 (cold-start
      discovery).  These are ranked last and serve as a safety net.

    Returns (nodes, edges) aligned with gm graphWalk.
    """
    config = config or RecallConfig()
    query = (query or "").strip()
    if not query or _is_low_signal_query(query):
        return [], []

    # ━━━━ Step 1: Raw FTS search (no status filter — handled by channel) ━━━
    raw_direct = store.search_nodes(
        query,
        status=None,  # let recall_nodes decide eligibility
        limit=config.search_limit,
    )

    # ━━━━ Step 2: Raw vector search ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    vector_hits: list[dict[str, Any]] = []
    if embedding_enabled(embedding_config):
        try:
            query_vector = create_embedding(query, embedding_config)
            min_similarity = _min_vector_similarity(query)
            for candidate in store.list_vector_nodes(status=None, limit=None):
                similarity = cosine_similarity(
                    query_vector, candidate.get("embedding") or []
                )
                if similarity < min_similarity:
                    continue
                enriched = dict(candidate)
                enriched["semantic_score"] = similarity
                vector_hits.append(enriched)
            vector_hits.sort(
                key=lambda n: (float(n.get("semantic_score") or 0.0), *_sort_key(n)),
                reverse=True,
            )
            vector_hits = vector_hits[: config.vector_limit]
        except Exception:
            vector_hits = []

    # ━━━━ Step 3: Batch evidence counts for all raw candidates ━━━━━━━
    all_raw_ids = [n["id"] for n in raw_direct + vector_hits if n.get("id")]
    evidence_map = batch_get_evidence_counts(store, all_raw_ids)
    for node in raw_direct:
        node["_evidence_count"] = evidence_map.get(node["id"], 0)
    for node in vector_hits:
        node["_evidence_count"] = evidence_map.get(node["id"], 0)

    # ━━━━ Step 4: L1 DIRECT channel — eligibility filter ━━━━━━━━━━
    l1_direct = [
        n for n in raw_direct
        if _is_recall_eligible(n, RecallChannel.DIRECT, config)
    ]
    l1_vector = [
        n for n in vector_hits
        if _is_recall_eligible(n, RecallChannel.DIRECT, config)
    ]

    # ━━━━ Step 5: L2 GRAPH channel — 1-hop expansion from L1 seeds ━━
    l1_seed_ids = {n["id"] for n in l1_direct + l1_vector if n.get("id")}
    l2_hits = _get_related_hits(store, l1_seed_ids, config)

    # Inject evidence counts for L2 nodes too (needed for ranking)
    l2_ids = [n["id"] for n in l2_hits if n.get("id")]
    l2_evidence = batch_get_evidence_counts(store, l2_ids)
    for node in l2_hits:
        node["_evidence_count"] = l2_evidence.get(node["id"], 0)

    # ━━━━ Step 6: Merge all ranked candidates ━━━━━━━━━━━━━━━━━━━━━
    merged: dict[str, dict[str, Any]] = {}

    for idx, node in enumerate(l1_direct):
        _merge_hit(
            merged,
            node,
            match_priority=3,
            lexical_score=float(len(l1_direct) - idx),
            semantic_score=float(node.get("semantic_score") or 0.0),
        )
    for idx, node in enumerate(l1_vector):
        _merge_hit(
            merged,
            node,
            match_priority=2,
            lexical_score=float(len(l1_vector) - idx),
            semantic_score=float(node.get("semantic_score") or 0.0),
        )
    for node in l2_hits:
        _merge_hit(merged, node, match_priority=1)

    # ━━━━ Step 7: Rank L1+L2 candidates ━━━━━━━━━━━━━━━━━━━━━━━━━━
    all_evidence = {**evidence_map, **l2_evidence}
    use_ppr = bool(l2_hits)
    seed_ids = list(l1_seed_ids)
    ranked = _rank_candidates(
        list(merged.values()),
        all_evidence,
        use_ppr=use_ppr,
        seed_ids=seed_ids,
        store=store,
    )

    # ━━━━ Step 8: L3 COLD — fill remaining slots if under max_nodes ━━
    final_nodes: list[dict[str, Any]] = []
    if len(ranked) < config.max_nodes:
        remaining_slots = config.max_nodes - len(ranked)
        seen_ids = {n["id"] for n in ranked}
        l3_candidates = [
            n for n in (raw_direct + vector_hits)
            if n.get("id") not in seen_ids
            and _is_recall_eligible(n, RecallChannel.COLD, config)
        ]
        l3_ranked = _rank_candidates(l3_candidates, all_evidence)
        ranked.extend(l3_ranked[:remaining_slots])

    final_nodes = ranked[: config.max_nodes]
    final_ids = [str(n["id"]) for n in final_nodes if n.get("id")]

    # ━━━━ Step 9: Fetch edges for final candidates ━━━━━━━━━━━━━━━━
    edges: list[dict[str, Any]] = []
    if final_ids:
        try:
            edges = store.get_edges_for_nodes(final_ids)
        except Exception:
            edges = []

    return final_nodes, edges
