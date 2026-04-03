"""SparkGraph retrieval helpers for dynamic recall injection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.sparkgraph.config import SparkGraphEmbeddingConfig
from agent.sparkgraph.embedding import cosine_similarity, create_embedding, embedding_enabled
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
) -> list[dict[str, Any]]:
    config = config or RecallConfig()
    query = (query or "").strip()
    if not query or _is_low_signal_query(query):
        return []

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

    ranked = sorted(merged.values(), key=_recall_rank, reverse=True)
    return ranked[: config.max_nodes]
