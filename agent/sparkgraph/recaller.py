"""SparkGraph retrieval helpers for dynamic recall injection."""

from __future__ import annotations

import json as _json
from dataclasses import dataclass
from typing import Any

from agent.sparkgraph.config import SparkGraphEmbeddingConfig
from agent.sparkgraph.embedding import cosine_similarity, create_embedding, embedding_enabled
from agent.sparkgraph.pagerank import invalidate_graph_cache, personalized_pagerank
from agent.sparkgraph.store import SparkGraphStore

MIN_RECALL_CONFIDENCE = 0.60
MIN_RECALL_STABILITY = 0.60
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


def _is_recall_eligible(node: dict[str, Any]) -> bool:
    return (
        node.get("status") == "active"
        and float(node.get("confidence") or 0.0) >= MIN_RECALL_CONFIDENCE
        and float(node.get("stability") or 0.0) >= MIN_RECALL_STABILITY
    )


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
    """
    Compute PPR-based priority score for a node (aligned with gm nodePriority).

    Ranking dimensions (primary → secondary):
      1. ppr_score * 1000  (dominant signal)
      2. source_kind bonus  (explicit +80, manual +40, isolated reflection +800)
      3. confidence * 100
      4. evidence_count * 5  (capped at 20 evidence → max 100)
      5. superseded penalty  (-500)
    """
    if source_scores is None:
        source_scores = _SOURCE_SCORES

    score = ppr_score * 1000.0
    source_kind = str(node.get("source_kind") or "")
    score += source_scores.get(source_kind, 0.0)

    # Isolated reflection strong bonus: ppr_score < 0.01 means no graph neighbours
    if source_kind == "reflection" and ppr_score < 0.01:
        score += 800.0

    score += float(node.get("confidence") or 0.0) * 100.0

    # evidence_count from sg_evidence table — passed in by caller (batch-queried)
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
    """
    Rank nodes using PPR scores (replaces _recall_rank during PPR mode).

    Each node is enriched with _ppr_score for observability.
    """
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


def recall_nodes(
    store: SparkGraphStore,
    *,
    query: str,
    config: RecallConfig | None = None,
    embedding_config: SparkGraphEmbeddingConfig | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Return (nodes, edges) for the current query.

    Edges are fetched for all final ranked candidate nodes (aligned with gm
    graphWalk returning {nodes, edges}). LLM receives structured triples
    instead of isolated facts.
    """
    config = config or RecallConfig()
    query = (query or "").strip()
    if not query or _is_low_signal_query(query):
        return [], []

    direct_hits = store.search_nodes(
        query,
        status="active",
        limit=config.search_limit,
    )
    direct_hits = [node for node in direct_hits if _is_recall_eligible(node)]

    vector_hits: list[dict[str, Any]] = []
    if embedding_enabled(embedding_config):
        try:
            query_vector = create_embedding(query, embedding_config)
            min_similarity = _min_vector_similarity(query)
            for candidate in store.list_vector_nodes(status="active", limit=None):
                if not _is_recall_eligible(candidate):
                    continue
                similarity = cosine_similarity(query_vector, candidate.get("embedding") or [])
                if similarity < min_similarity:
                    continue
                enriched = dict(candidate)
                enriched["semantic_score"] = similarity
                vector_hits.append(enriched)
            vector_hits.sort(
                key=lambda node: (
                    float(node.get("semantic_score") or 0.0),
                    *_sort_key(node),
                ),
                reverse=True,
            )
            vector_hits = vector_hits[: config.vector_limit]
        except Exception:
            vector_hits = []

    related_hits = store.get_related_nodes(
        [node["id"] for node in (direct_hits + vector_hits)],
        active_only=True,
        limit=config.related_limit,
    )
    related_hits = [node for node in related_hits if _is_recall_eligible(node)]

    merged: dict[str, dict[str, Any]] = {}
    direct_count = len(direct_hits)
    for idx, node in enumerate(direct_hits):
        _merge_hit(
            merged,
            node,
            match_priority=3,
            lexical_score=float(direct_count - idx),
            semantic_score=float(node.get("semantic_score") or 0.0),
        )
    for idx, node in enumerate(vector_hits):
        _merge_hit(
            merged,
            node,
            match_priority=2,
            lexical_score=float(len(vector_hits) - idx),
            semantic_score=float(node.get("semantic_score") or 0.0),
        )
    for node in related_hits:
        _merge_hit(merged, node, match_priority=1)

    # ━━━━ PPR personalized ranking (Phase 1) ━━━━━━━━━━━━━━━━━━━
    # PPR adds value when the graph has been traversed beyond direct seeds
    # (related_hits are 1-hop neighbours that benefit from PPR's propagation).
    # When there are no related hits, fall back to legacy ranking to preserve
    # the established semantic-score > recency ordering.
    use_ppr = bool(related_hits)
    candidate_ids = [
        str(node.get("id", "")) for node in merged.values() if node.get("id")
    ]
    if use_ppr and candidate_ids:
        seed_ids = [
            str(node.get("id", ""))
            for node in (direct_hits + vector_hits)
            if node.get("id")
        ]
        # Batch-query evidence counts to avoid N individual queries during sort
        evidence_counts: dict[str, int] = {}
        for cid in candidate_ids:
            evidence_counts[cid] = store.count_evidence(cid)
        try:
            ppr_scores = personalized_pagerank(
                store,
                seed_ids=seed_ids,
                candidate_ids=candidate_ids,
                damping=PPR_DAMPING,
                iterations=PPR_ITERATIONS,
            )
            ranked = _rank_with_ppr(
                list(merged.values()),
                ppr_scores,
                evidence_counts,
            )
        except Exception:
            # PPR failure → fallback to legacy ranking
            ranked = sorted(merged.values(), key=_recall_rank, reverse=True)
    else:
        ranked = sorted(merged.values(), key=_recall_rank, reverse=True)
    # ━━━━ PPR end ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # Fetch edges for the final candidate nodes (mirrors gm graphWalk {nodes, edges})
    final_ids = [str(n.get("id", "")) for n in ranked[: config.max_nodes] if n.get("id")]
    edges: list[dict[str, Any]] = []
    if final_ids:
        try:
            edges = store.get_edges_for_nodes(final_ids)
        except Exception:
            edges = []

    return ranked[: config.max_nodes], edges
