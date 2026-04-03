"""SparkGraph recall — simplified two-channel architecture aligned with graph-memory.

召回通道（只有两条）：
  1. 精确通道：FTS + 向量搜索合并去重
  2. 图扩展通道：1-hop active 邻居（以精确通道 top2 为种子）

排序：recall_priority_score(PPR×1000 + sourceBonus + validatedCount×5 + confidence×100 - superseded×500)

无 CANDIDATE，无 evidence，无三通道，无 L3 COLD 兜底。
"""

from __future__ import annotations

import json as _json
from typing import Any, Collection

from agent.sparkgraph.config import SparkGraphEmbeddingConfig
from agent.sparkgraph.embedding import cosine_similarity, create_embedding, embedding_enabled
from agent.sparkgraph.pagerank import invalidate_graph_cache, personalized_pagerank
from agent.sparkgraph.scoring import recall_priority_score
from agent.sparkgraph.store import SparkGraphStore
from agent.sparkgraph.types import NodeStatus

# ─── Vector thresholds ───────────────────────────────────────────
MIN_VECTOR_SIMILARITY = 0.55
SHORT_QUERY_VECTOR_SIMILARITY = 0.68
LOW_SIGNAL_RECALL_TOKENS = {
    "ahoy", "aloha", "ciao", "goodbye", "hallo", "hello", "hey", "hi",
    "hola", "howdy", "ok", "okay", "sup", "thanks", "thank", "thx", "yo",
}

# ─── PPR constants ───────────────────────────────────────────────
PPR_DAMPING = 0.85
PPR_ITERATIONS = 30

# ─── Recall config ───────────────────────────────────────────────
class RecallConfig:
    __slots__ = ("search_limit", "related_limit", "max_nodes", "vector_limit")

    def __init__(
        self,
        search_limit: int = 8,
        related_limit: int = 4,
        max_nodes: int = 4,
        vector_limit: int = 24,
    ):
        self.search_limit = search_limit
        self.related_limit = related_limit
        self.max_nodes = max_nodes
        self.vector_limit = vector_limit


# ─── Query pre-processing ───────────────────────────────────────


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
    existing["_match_priority"] = max(float(existing.get("_match_priority") or 0.0), float(match_priority))
    existing["_lexical_score"] = max(float(existing.get("_lexical_score") or 0.0), float(lexical_score))
    existing["semantic_score"] = max(float(existing.get("semantic_score") or 0.0), float(semantic_score))


# ─── Sorting ─────────────────────────────────────────────────────


def _sort_key(node: dict[str, Any]) -> tuple[float, float, float, float, int]:
    """Fallback sort: validated_count (primary), confidence, reuse_score, stability, updated_at."""
    return (
        float(node.get("validated_count") or 0),
        float(node.get("confidence") or 0.0),
        float(node.get("reuse_score") or 0.0),
        float(node.get("stability") or 0.0),
        int(node.get("updated_at") or 0),
    )


def _rank_with_ppr(
    nodes: list[dict[str, Any]],
    ppr_scores: dict[str, float],
    seed_ids: list[str],
    store: SparkGraphStore,
) -> list[dict[str, Any]]:
    """Rank nodes using PPR scores and recall_priority_score."""
    scored: list[tuple[float, float, float, float, float, int, dict[str, Any]]] = []
    for node in nodes:
        nid = str(node.get("id") or "")
        ppr = ppr_scores.get(nid, 0.0)
        node_copy = dict(node)
        node_copy["_ppr_score"] = ppr
        meta = node.get("meta") or {}
        if isinstance(meta, str):
            meta = _json.loads(meta)
        priority = recall_priority_score(
            ppr_score=ppr,
            validated_count=node.get("validated_count", 0),
            confidence=float(node.get("confidence") or 0.0),
            source_kind=str(node.get("source_kind") or ""),
            superseded=bool(meta.get("superseded_by") or meta.get("superseded")),
        )
        semantic = float(node.get("semantic_score") or 0.0)
        conf = float(node.get("confidence") or 0.0)
        validated = float(node.get("validated_count") or 0)
        updated = int(node.get("updated_at") or 0)
        scored.append((priority, validated, semantic, conf, updated, node_copy))
    scored.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]), reverse=True)
    return [item[5] for item in scored]


def _rank_legacy(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fallback rank when PPR is unavailable."""
    return sorted(nodes, key=_sort_key, reverse=True)


def _rank_nodes(
    nodes: list[dict[str, Any]],
    *,
    use_ppr: bool,
    seed_ids: list[str],
    store: SparkGraphStore,
) -> list[dict[str, Any]]:
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
            return _rank_with_ppr(nodes, ppr_scores, seed_ids, store)
        except Exception:
            pass
    return _rank_legacy(nodes)


# ─── Vector search ───────────────────────────────────────────────


def _vector_search(
    query: str,
    config: RecallConfig,
    embedding_config: SparkGraphEmbeddingConfig | None,
) -> list[dict[str, Any]]:
    """Search by embedding similarity. Returns enriched node dicts."""
    try:
        query_vector = create_embedding(query, embedding_config)
        min_similarity = _min_vector_similarity(query)
        results: list[dict[str, Any]] = []
        for candidate in _list_active_vector_nodes(embedding_config):
            similarity = cosine_similarity(query_vector, candidate.get("embedding") or [])
            if similarity < min_similarity:
                continue
            enriched = dict(candidate)
            enriched["semantic_score"] = similarity
            results.append(enriched)
        results.sort(key=lambda n: (float(n.get("semantic_score") or 0.0), *_sort_key(n)), reverse=True)
        return results[: config.vector_limit]
    except Exception:
        return []


def _list_active_vector_nodes(
    embedding_config: SparkGraphEmbeddingConfig | None,
) -> list[dict[str, Any]]:
    """List active nodes that have vectors."""
    from agent.sparkgraph.store import SparkGraphStore
    from pathlib import Path
    from agent.sparkgraph.config import SparkGraphConfig

    db_path = Path(embedding_config.db_path) if embedding_config else SparkGraphConfig.from_env().db_path
    store = SparkGraphStore(db_path)
    try:
        return store.list_vector_nodes(status=NodeStatus.ACTIVE.value)
    finally:
        store.close()


# ─── Main recall entry point ──────────────────────────────────────


def recall_nodes(
    store: SparkGraphStore,
    *,
    query: str,
    config: RecallConfig | None = None,
    embedding_config: SparkGraphEmbeddingConfig | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """两条通道召回：精确（vector+FTS）+ 图扩展（1-hop active）。

    无 CANDIDATE，无 evidence，无 L3 COLD。
    所有召回节点均为 active，排序由 recall_priority_score 决定。
    """
    config = config or RecallConfig()
    query = (query or "").strip()
    if not query or _is_low_signal_query(query):
        return [], []

    # ── Channel 1: 精确搜索（FTS + 向量） ────────────────────────────
    raw_fts = store.search_nodes(
        query,
        status=NodeStatus.ACTIVE.value,
        limit=config.search_limit,
    )
    raw_vector = _vector_search(query, config, embedding_config) if embedding_enabled(embedding_config) else []

    # 合并去重
    merged: dict[str, dict[str, Any]] = {}
    for idx, node in enumerate(raw_fts):
        _merge_hit(merged, node, match_priority=3, lexical_score=float(len(raw_fts) - idx))
    for idx, node in enumerate(raw_vector):
        _merge_hit(merged, node, match_priority=2, semantic_score=float(node.get("semantic_score") or 0.0))

    # ── Channel 2: 图扩展（以精确 top2 为种子） ──────────────────────
    l1_seeds = list(merged.values())[:2]
    seed_ids = [n["id"] for n in l1_seeds if n.get("id")]
    graph_hits: list[dict[str, Any]] = []
    if seed_ids:
        raw_graph = store.get_related_nodes(seed_ids, active_only=True, limit=config.related_limit)
        for node in raw_graph:
            if node.get("id") not in merged:
                _merge_hit(merged, node, match_priority=1)
                graph_hits.append(node)

    # ── 排序 ─────────────────────────────────────────────────────────
    all_nodes = list(merged.values())
    use_ppr = bool(graph_hits)
    ranked = _rank_nodes(all_nodes, use_ppr=use_ppr, seed_ids=seed_ids, store=store)

    final = ranked[: config.max_nodes]
    final_ids = [str(n["id"]) for n in final if n.get("id")]

    # ── 命中计数 ─────────────────────────────────────────────────────
    if final_ids:
        store.increment_validated_count(final_ids)

    # ── 取边 ─────────────────────────────────────────────────────────
    edges: list[dict[str, Any]] = []
    if final_ids:
        try:
            edges = store.get_edges_for_nodes(final_ids)
        except Exception:
            edges = []

    return final, edges
