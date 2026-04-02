"""SparkGraph retrieval helpers for dynamic recall injection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.sparkgraph.config import SparkGraphEmbeddingConfig
from agent.sparkgraph.embedding import cosine_similarity, create_embedding, embedding_enabled
from agent.sparkgraph.store import SparkGraphStore

MIN_RECALL_CONFIDENCE = 0.60
MIN_RECALL_STABILITY = 0.60
MIN_VECTOR_SIMILARITY = 0.35


@dataclass(frozen=True)
class RecallConfig:
    search_limit: int = 8
    related_limit: int = 4
    max_nodes: int = 4
    vector_limit: int = 24


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


def recall_nodes(
    store: SparkGraphStore,
    *,
    query: str,
    config: RecallConfig | None = None,
    embedding_config: SparkGraphEmbeddingConfig | None = None,
) -> list[dict[str, Any]]:
    config = config or RecallConfig()
    query = (query or "").strip()
    if not query:
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
            for candidate in store.list_vector_nodes(status="active", limit=config.vector_limit):
                if not _is_recall_eligible(candidate):
                    continue
                similarity = cosine_similarity(query_vector, candidate.get("embedding") or [])
                if similarity < MIN_VECTOR_SIMILARITY:
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
            vector_hits = vector_hits[: config.search_limit]
        except Exception:
            vector_hits = []

    related_hits = store.get_related_nodes(
        [node["id"] for node in (direct_hits + vector_hits)],
        active_only=True,
        limit=config.related_limit,
    )
    related_hits = [node for node in related_hits if _is_recall_eligible(node)]

    merged: dict[str, dict[str, Any]] = {}
    for node in direct_hits:
        merged[node["id"]] = node
    for node in vector_hits:
        merged.setdefault(node["id"], node)
    for node in related_hits:
        merged.setdefault(node["id"], node)

    ranked = sorted(merged.values(), key=_sort_key, reverse=True)
    return ranked[: config.max_nodes]
