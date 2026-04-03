"""SparkGraph recall — three-channel architecture aligned with graph-memory.

召回通道：
  1. 精确通道：FTS + 向量搜索合并去重
  2. 图扩展通道：1-hop active 邻居（以精确通道 top2 为种子）
  3. explicit/manual 优先通道：按 source_kind 过滤后补充（高 match_priority=4）

排序：recall_priority_score(PPR×1000 + sourceBonus + validatedCount×5 + confidence×100 - superseded×500)

无 CANDIDATE，无 evidence，无 L3 COLD 兜底。
default_inject=0 的节点在 recall 输出层被过滤（不可注入）。
"""

from __future__ import annotations

import json as _json
import time
from typing import Any

from agent.sparkgraph.config import SparkGraphEmbeddingConfig
from agent.sparkgraph.embedding import cosine_similarity, create_embedding, embedding_enabled
from agent.sparkgraph.pagerank import invalidate_graph_cache, personalized_pagerank
from agent.sparkgraph.scoring import recall_priority_score
from agent.sparkgraph.store import SparkGraphStore
from agent.sparkgraph.types import NodeStatus

# ─── Session Pool constants ─────────────────────────────────────
POOL_CAPACITY = 12          # Maximum nodes per session pool
LRFU_FREQ_W = 0.4           # Frequency weight in LRFU composite score
LRFU_RECENCY_W = 0.6       # Recency weight in LRFU composite score
LRFU_DECAY_BASE = 0.95     # Hourly decay factor for recency component
_POOLS: dict[str, dict[str, dict[str, Any]]] = {}  # session_id → node_id → PoolEntry


class PoolEntry(dict):
    """Lightweight container for a session-pool entry.
    Fields: node (dict), added_at (float unix s), hit_count (int),
            last_hit (float unix s).
    """
    __slots__ = ()


def _lrfu_score(entry: PoolEntry) -> float:
    """Compute LRFU score: w_freq * hit_count + w_recency * recency.
    recency = decay^(hours_since_last_hit).
    Higher is better; entries with lowest score are evicted first.
    """
    hit_count = float(entry.get("hit_count", 0))
    last_hit = float(entry.get("last_hit", 0.0))
    now = time.time()
    hours_elapsed = (now - last_hit) / 3600.0
    recency = LRFU_DECAY_BASE ** max(0.0, hours_elapsed)
    return LRFU_FREQ_W * hit_count + LRFU_RECENCY_W * recency


def _pool_evict_lrfu(pool: dict[str, PoolEntry]) -> None:
    """Evict the lowest-LRFU-scoring entry until pool is within capacity."""
    while len(pool) > POOL_CAPACITY:
        if not pool:
            break
        worst_id = min(pool, key=lambda nid: _lrfu_score(pool[nid]))
        del pool[worst_id]


def _pool_add(pool: dict[str, PoolEntry], node: dict[str, Any]) -> None:
    """Add or update a node in the session pool."""
    node_id = str(node.get("id") or "")
    if not node_id:
        return
    now = time.time()
    if node_id in pool:
        entry = pool[node_id]
        entry["hit_count"] = entry.get("hit_count", 0) + 1
        entry["last_hit"] = now
        # Merge in any newer data from this recall pass
        entry["node"].update(node)
    else:
        entry = PoolEntry({
            "node": dict(node),
            "added_at": now,
            "hit_count": 1,
            "last_hit": now,
        })
        pool[node_id] = entry
    _pool_evict_lrfu(pool)


def _pool_get_boosted(
    pool: dict[str, PoolEntry],
    nodes: list[dict[str, Any]],
) -> list[tuple[float, dict[str, Any]]]:
    """Return pool entries boosted by pool membership, sorted descending.

    For nodes already in the pool: apply a pool_boost so they outrank
    fresh results with equal recall_priority_score.  The boost is
    large enough to dominate typical score differences but small enough
    not to override clearly better matches.
    """
    pool_ids = set(pool)
    boosted_nodes: list[tuple[float, dict[str, Any]]] = []
    for node in nodes:
        nid = str(node.get("id") or "")
        entry = pool.get(nid)
        if entry is not None:
            # node is in pool — attach boost metadata for ranking
            node = dict(node)
            node["_pool_lrfu"] = _lrfu_score(entry)
            node["_pool_hit_count"] = entry.get("hit_count", 0)
        boosted_nodes.append((entry.get("hit_count", 0) if entry else 0, node))

    boosted_nodes.sort(key=lambda x: x[0], reverse=True)
    return boosted_nodes


def _pool_refresh(pool: dict[str, PoolEntry], recalled_ids: set[str]) -> None:
    """Refresh pool: update last_hit for entries recalled this turn; age-out the rest.

    Nodes NOT recalled in this turn get their recency decayed via time.time()
    so their LRFU score drops, reflecting that fresh recalls are more relevant.
    """
    now = time.time()
    for node_id, entry in list(pool.items()):
        if node_id in recalled_ids:
            entry["last_hit"] = now
        else:
            # Decay recency by passing time without a hit — the LRFU formula
            # uses (now - last_hit) so the score naturally drops.
            pass  # last_hit is already set to the last time this node was hit;
                   # not updating it here means the next _lrfu_score call
                   # correctly computes a larger hours_elapsed → lower recency.


def recall_pool_clear(session_id: str) -> None:
    """Clear the recall pool for a session (call when session ends)."""
    _POOLS.pop(session_id, None)


def recall_pool_get(session_id: str) -> dict[str, PoolEntry]:
    """Return (creating if needed) the pool for a session."""
    if session_id not in _POOLS:
        _POOLS[session_id] = {}
    return _POOLS[session_id]


# ─── Vector thresholds ───────────────────────────────────────────
MIN_VECTOR_SIMILARITY = 0.75
SHORT_QUERY_VECTOR_SIMILARITY = 0.80
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


def _is_injectable(node: dict[str, Any]) -> bool:
    """Return False for nodes that must not appear in recall output or PPR ranking."""
    di = node.get("default_inject")
    # default_inject=0 nodes (reflection/shadow source) are excluded from recall entirely.
    # Check both the int column value and a possible bool-ish representation.
    if di is not None and int(di) == 0:
        return False
    return True


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
    # default_inject=0 nodes must never enter the candidate set — they must not
    # influence PPR rankings or appear in output.
    if not _is_injectable(node):
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


def _sort_key(node: dict[str, Any]) -> tuple[float, float, int]:
    """Fallback sort: validated_count (primary), confidence, updated_at."""
    return (
        float(node.get("validated_count") or 0),
        float(node.get("confidence") or 0.0),
        int(node.get("updated_at") or 0),
    )


def _rank_with_ppr(
    nodes: list[dict[str, Any]],
    ppr_scores: dict[str, float],
    seed_ids: list[str],
    store: SparkGraphStore,
    pool: dict[str, PoolEntry] | None = None,
) -> list[dict[str, Any]]:
    """Rank nodes using PPR scores and recall_priority_score."""
    scored: list[tuple[float, float, float, float, float, float, int, dict[str, Any]]] = []
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
        # Pool boost: nodes that were recalled in previous turns get a secondary
        # boost (from_pool * pool_lrfu) so they rank above fresh matches at the
        # same priority level without overriding genuinely better new matches.
        from_pool = float(bool(node.get("_from_pool")))
        pool_lrfu = float(node.get("_pool_lrfu") or 0.0)
        scored.append((priority, from_pool, pool_lrfu, validated, semantic, conf, updated, node_copy))
    scored.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4], x[5], x[6]), reverse=True)
    return [item[7] for item in scored]


def _rank_legacy(
    nodes: list[dict[str, Any]],
    pool: dict[str, PoolEntry] | None = None,
) -> list[dict[str, Any]]:
    """Fallback rank when PPR is unavailable; pool nodes get secondary boost."""
    def _pool_sort_key(node: dict[str, Any]) -> tuple[float, float, float, int]:
        from_pool = float(bool(node.get("_from_pool")))
        pool_lrfu = float(node.get("_pool_lrfu") or 0.0)
        base = _sort_key(node)
        return (from_pool, pool_lrfu, base[0], base[1], base[2])
    return sorted(nodes, key=_pool_sort_key, reverse=True)


def _rank_nodes(
    nodes: list[dict[str, Any]],
    *,
    use_ppr: bool,
    seed_ids: list[str],
    store: SparkGraphStore,
    pool: dict[str, PoolEntry] | None = None,
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
    store: SparkGraphStore,
    query: str,
    config: RecallConfig,
    embedding_config: SparkGraphEmbeddingConfig | None,
) -> list[dict[str, Any]]:
    """Search by embedding similarity. Returns enriched node dicts."""
    try:
        query_vector = create_embedding(query, embedding_config)
        min_similarity = _min_vector_similarity(query)
        results: list[dict[str, Any]] = []
        for candidate in _list_active_vector_nodes(store):
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
    store: SparkGraphStore,
) -> list[dict[str, Any]]:
    """List active nodes that have vectors (reuses caller's store, no new connection)."""
    return store.list_vector_nodes(status=NodeStatus.ACTIVE.value)


# ─── Main recall entry point ──────────────────────────────────────


def recall_nodes(
    store: SparkGraphStore,
    *,
    query: str,
    config: RecallConfig | None = None,
    embedding_config: SparkGraphEmbeddingConfig | None = None,
    session_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """三条通道召回：explicit优先 + 精确（FTS+向量）+ 图扩展（1-hop）。

    当 session_id 提供时，额外执行 session pool 机制：
      - 从历史池中提升已召回节点的分数（跨轮次记忆）
      - 召回结果入池，池满时按 LRFU 驱逐最低分节点
      - 未在本轮召回的池节点自然衰减，为后续驱逐做准备

    Returns:
        (nodes, edges, token_estimate)
        token_estimate = sum(len(s.summary) + len(s.detail)) / 3 ≈ token 数
    """
    config = config or RecallConfig()
    query = (query or "").strip()

    # Session pool access — created lazily
    pool: dict[str, PoolEntry] = {}
    if session_id:
        pool = recall_pool_get(session_id)

    if not query or _is_low_signal_query(query):
        # Low-signal queries still update pool refresh but return empty recall
        if session_id and pool:
            _pool_refresh(pool, set())
        return [], [], 0

    # ── Channel 1: 精确搜索（FTS + 向量） ────────────────────────────
    raw_fts = store.search_nodes(
        query,
        status=NodeStatus.ACTIVE.value,
        limit=config.search_limit,
    )
    raw_vector = _vector_search(store, query, config, embedding_config) if embedding_enabled(embedding_config) else []

    # 合并去重
    merged: dict[str, dict[str, Any]] = {}
    for idx, node in enumerate(raw_fts):
        _merge_hit(merged, node, match_priority=3, lexical_score=float(len(raw_fts) - idx))
    for idx, node in enumerate(raw_vector):
        _merge_hit(merged, node, match_priority=2, semantic_score=float(node.get("semantic_score") or 0.0))

    # ── Pool layer: inject previously-seen nodes with boosted priority ──
    # Nodes that were recalled in previous turns of this session get a
    # _pool_boost marker so they rank above fresh results at equal priority.
    # This implements cross-turn context persistence without overriding
    # genuinely better new matches.
    pool_refreshed_ids: set[str] = set()
    if pool:
        for node_id, entry in pool.items():
            if node_id in merged:
                # Already found fresh — update pool entry recency and hit_count
                # (but do NOT call _pool_add here to avoid double-incrementing
                # hit_count when we process merged nodes in the final loop)
                entry["last_hit"] = time.time()
                entry["hit_count"] = entry.get("hit_count", 0) + 1
                entry["node"].update(merged[node_id])
                pool_refreshed_ids.add(node_id)
            else:
                # Not found fresh — inject with pool boost (low match_priority=0
                # but flagged as pool member so sort key lifts it)
                node = dict(entry["node"])
                node["_from_pool"] = True
                node["_pool_lrfu"] = _lrfu_score(entry)
                node["_pool_hit_count"] = entry.get("hit_count", 0)
                _merge_hit(merged, node, match_priority=0)

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

    # ── Channel 3: explicit/manual 优先补充 ─────────────────────────
    explicit_kinds = {"explicit", "manual"}
    existing_ids = {n["id"] for n in merged.values() if n.get("id")}
    explicit_nodes = store.get_by_source_kind(
        list(explicit_kinds), query, limit=config.search_limit
    )
    for node in explicit_nodes:
        if node.get("id") not in existing_ids:
            _merge_hit(merged, node, match_priority=4)
            existing_ids.add(node["id"])

    # ── 排序 ─────────────────────────────────────────────────────────
    all_nodes = list(merged.values())
    use_ppr = bool(graph_hits)
    ranked = _rank_nodes(all_nodes, use_ppr=use_ppr, seed_ids=seed_ids, store=store, pool=pool)

    final = ranked[: config.max_nodes]
    final_ids = {str(n["id"]) for n in final if n.get("id")}

    # ── Session pool update: add newly recalled nodes, refresh recency ─
    if pool:
        for node in final:
            nid = str(node.get("id") or "")
            if not nid or nid in pool_refreshed_ids:
                continue
            _pool_add(pool, node)
        _pool_refresh(pool, final_ids)

    # ── 命中计数 ─────────────────────────────────────────────────────
    if final_ids:
        store.increment_validated_count(list(final_ids))

    # ── 取边 ─────────────────────────────────────────────────────────
    edges: list[dict[str, Any]] = []
    if final_ids:
        try:
            edges = store.get_edges_for_nodes(list(final_ids))
        except Exception:
            edges = []

    # ── token 估算 ──────────────────────────────────────────────────
    token_estimate = int(sum(
        len(str(n.get("summary", ""))) + len(str(n.get("detail", "")))
        for n in final
    ) / 3)

    return final, edges, token_estimate
