"""SparkGraph tools."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from agent.sparkgraph.dedup import build_canonical_key, find_cross_type_dedup_match, find_dedup_match
from agent.sparkgraph.embedding import create_embedding, embedding_content_hash, embedding_enabled
from agent.sparkgraph.config import SparkGraphEmbeddingConfig
from agent.sparkgraph.scoring import compute_scores, default_type_priors, next_status_for_candidate
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType
from tools.registry import registry


SPARKGRAPH_RECORD_SCHEMA = {
    "name": "sparkgraph_record",
    "description": (
        "Record durable knowledge points into SparkGraph, Hermes' structured knowledge supplement. "
        "Use this proactively during flush-style summarization when you notice stable facts, recurring "
        "issues, reusable resources, lasting decisions, or stable user preferences that are likely to "
        "matter again later.\n\n"
        "WHEN TO RECORD:\n"
        "- You identify a concrete fact or troubleshooting rule that will be useful again later\n"
        "- You notice a recurring issue pattern, failure mode, or configuration gotcha\n"
        "- You learn a stable resource, decision, or convention worth recalling in future sessions\n"
        "- You notice a stable user preference that should be available for later retrieval\n\n"
        "PRIORITY: concrete facts and recurring issues > stable resources and decisions > stable preferences.\n\n"
        "DO NOT RECORD:\n"
        "- greetings, pleasantries, or small talk\n"
        "- temporary task state, progress updates, or one-off outcomes\n"
        "- speculative guesses, weak inferences, or raw dumps of conversation text\n\n"
        "Prefer 1-2 high-value items. If there is no durable knowledge worth retrieving later, do not call "
        "sparkgraph_record."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "Durable knowledge items to record. Prefer 1-2 high-value items.",
                "items": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": ["FACT", "PREFERENCE", "ISSUE", "RESOURCE", "DECISION"],
                        },
                        "evidence": {"type": "string"},
                    },
                    "required": ["summary", "type", "evidence"],
                },
            },
        },
        "required": ["items"],
    },
}

SPARKGRAPH_SEARCH_SCHEMA = {
    "name": "sparkgraph_search",
    "description": (
        "Search SparkGraph for previously stored durable knowledge nodes. "
        "Use this when you want explicit structured lookup of facts, preferences, "
        "issues, resources, or decisions already recorded in SparkGraph."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["candidate", "active", "deprecated"],
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["query"],
    },
}

SPARKGRAPH_STATS_SCHEMA = {
    "name": "sparkgraph_stats",
    "description": (
        "Return high-level SparkGraph database statistics, including node counts, "
        "status distribution, type distribution, and evidence row totals."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def check_sparkgraph_requirements() -> bool:
    return True


def _normalize_node_type(raw: str) -> NodeType:
    return NodeType[str(raw).strip().upper()]


_EDGE_ELIGIBLE_TYPES = {
    NodeType.FACT,
    NodeType.ISSUE,
    NodeType.RESOURCE,
    NodeType.DECISION,
}
_MIN_RELATED_SIMILARITY = 0.42
_MIN_ANCHORED_SIMILARITY = 0.14


def _normalize_edge_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    normalized = re.sub(r"[^\w\s-]", " ", normalized)
    normalized = re.sub(r"[-_]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _token_set(text: str) -> set[str]:
    return {token for token in _normalize_edge_text(text).split() if len(token) >= 2}


def _char_ngram_set(text: str, n: int = 2) -> set[str]:
    compact = re.sub(r"\s+", "", _normalize_edge_text(text))
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[idx : idx + n] for idx in range(len(compact) - n + 1)}


def _overlap_ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _related_similarity(left: str, right: str) -> float:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    token_overlap = _overlap_ratio(left_tokens, right_tokens)
    char_overlap = _overlap_ratio(_char_ngram_set(left), _char_ngram_set(right))
    return max(token_overlap, char_overlap)


def _shared_anchor_tokens(left: str, right: str) -> set[str]:
    return {
        token
        for token in (_token_set(left) & _token_set(right))
        if len(token) >= 4
    }


def _edge_compatible(node_type: NodeType) -> bool:
    return node_type in _EDGE_ELIGIBLE_TYPES


def _maybe_link_related_batch_items(
    *,
    store: SparkGraphStore,
    batch_records: list[tuple[str, NodeType, str]],
) -> int:
    created_edges = 0
    seen_pairs: set[tuple[str, str]] = set()

    for idx, (left_id, left_type, left_summary) in enumerate(batch_records):
        if not _edge_compatible(left_type):
            continue
        for right_id, right_type, right_summary in batch_records[idx + 1 :]:
            if left_id == right_id or not _edge_compatible(right_type):
                continue
            pair = tuple(sorted((left_id, right_id)))
            if pair in seen_pairs:
                continue
            similarity = _related_similarity(left_summary, right_summary)
            if similarity < _MIN_RELATED_SIMILARITY and not (
                _shared_anchor_tokens(left_summary, right_summary)
                and similarity >= _MIN_ANCHORED_SIMILARITY
            ):
                continue
            try:
                store.insert_edge(
                    from_id=pair[0],
                    to_id=pair[1],
                    edge_type=EdgeType.RELATED_TO,
                    weight=round(similarity, 3),
                    meta={"origin": "co_recorded_batch", "similarity": round(similarity, 3)},
                )
            except Exception:
                continue
            seen_pairs.add(pair)
            created_edges += 1

    return created_edges


def sparkgraph_record_tool(
    *,
    items: list[dict[str, Any]],
    store: SparkGraphStore | None,
    session_id: str,
    turn_index: int,
    source_kind: str = "flush",
    embedding_config: SparkGraphEmbeddingConfig | None = None,
) -> str:
    """Persist durable knowledge candidates into SparkGraph."""
    if store is None:
        return json.dumps({"success": False, "error": "SparkGraph store is unavailable."}, ensure_ascii=False)
    if not isinstance(items, list):
        return json.dumps({"success": False, "error": "items must be a list."}, ensure_ascii=False)

    created = 0
    updated = 0
    rejected = 0
    recorded_ids: list[str] = []
    batch_records: list[tuple[str, NodeType, str]] = []

    for item in items:
        if not isinstance(item, dict):
            rejected += 1
            continue

        summary = str(item.get("summary", "")).strip()
        evidence = str(item.get("evidence", "")).strip()
        raw_type = item.get("type", "")
        if not summary or not evidence or not raw_type:
            rejected += 1
            continue

        try:
            node_type = _normalize_node_type(raw_type)
        except KeyError:
            rejected += 1
            continue

        canonical_key = build_canonical_key(node_type, summary)
        existing = find_dedup_match(
            store,
            node_type=node_type,
            summary=summary,
            canonical_key=canonical_key,
        )
        if existing is None and node_type in {NodeType.ISSUE, NodeType.FACT}:
            existing = find_cross_type_dedup_match(
                store,
                node_types=(NodeType.ISSUE, NodeType.FACT),
                summary=summary,
            )
        priors = default_type_priors(node_type)

        if existing is not None:
            existing_node = store.get_node(existing.node_id) or {}
            existing_type_raw = str(existing.node_type or existing_node.get("type") or node_type.value).strip().upper()
            try:
                scoring_type = _normalize_node_type(existing_type_raw)
            except KeyError:
                scoring_type = node_type
            priors = default_type_priors(scoring_type)
            store.append_evidence(
                node_id=existing.node_id,
                session_id=session_id,
                turn_index=turn_index,
                source_text=evidence,
                source_kind=source_kind,
            )
            evidence_count = store.count_evidence(existing.node_id)
            scores = compute_scores(
                source_kind=source_kind,
                evidence_count=evidence_count,
                stability=priors["stability"],
                reuse_score=priors["reuse_score"],
                dedup_consistency_bonus=0.05 if existing.match_type == "near" else 0.10,
            )
            status = next_status_for_candidate(
                source_kind=source_kind,
                confidence=scores.confidence,
                stability=scores.stability,
                evidence_count=evidence_count,
            )
            store.update_node_scoring(
                existing.node_id,
                confidence=scores.confidence,
                stability=scores.stability,
                reuse_score=scores.reuse_score,
                status=status.value,
                confidence_components=scores.confidence_components,
            )
            if embedding_enabled(embedding_config):
                try:
                    current_node = store.get_node(existing.node_id) or {}
                    embed_text = str(current_node.get("summary") or summary).strip()
                    if embed_text:
                        store.upsert_vector(
                            node_id=existing.node_id,
                            content_hash=embedding_content_hash(embed_text),
                            embedding=create_embedding(embed_text, embedding_config),
                        )
                except Exception:
                    pass
            updated += 1
            recorded_ids.append(existing.node_id)
            batch_records.append((existing.node_id, scoring_type, str(existing_node.get("summary") or summary)))
            continue

        scores = compute_scores(
            source_kind=source_kind,
            evidence_count=1,
            stability=priors["stability"],
            reuse_score=priors["reuse_score"],
        )
        status = next_status_for_candidate(
            source_kind=source_kind,
            confidence=scores.confidence,
            stability=scores.stability,
            evidence_count=1,
        )
        node_id = store.insert_node(
            SparkGraphNodeInput(
                type=node_type,
                summary=summary,
                canonical_key=canonical_key,
                source_kind=source_kind,
                status=status,
                confidence=scores.confidence,
                stability=scores.stability,
                reuse_score=scores.reuse_score,
                meta={
                    "confidence_components": scores.confidence_components,
                    "confidence_version": 1,
                    "source_kind": source_kind,
                },
            )
        )
        store.append_evidence(
            node_id=node_id,
            session_id=session_id,
            turn_index=turn_index,
            source_text=evidence,
            source_kind=source_kind,
        )
        if embedding_enabled(embedding_config):
            try:
                vector = create_embedding(summary, embedding_config)
                store.upsert_vector(
                    node_id=node_id,
                    content_hash=embedding_content_hash(summary),
                    embedding=vector,
                )
            except Exception:
                pass
        created += 1
        recorded_ids.append(node_id)
        batch_records.append((node_id, node_type, summary))

    related_edges_created = _maybe_link_related_batch_items(
        store=store,
        batch_records=batch_records,
    )

    return json.dumps(
        {
            "success": True,
            "created": created,
            "updated": updated,
            "rejected": rejected,
            "recorded_ids": recorded_ids,
            "related_edges_created": related_edges_created,
        },
        ensure_ascii=False,
    )


def sparkgraph_search_tool(
    *,
    query: str,
    store: SparkGraphStore | None,
    status: str | None = None,
    limit: int = 5,
) -> str:
    if store is None:
        return json.dumps({"success": False, "error": "SparkGraph store is unavailable."}, ensure_ascii=False)

    query = str(query or "").strip()
    if not query:
        return json.dumps({"success": False, "error": "query is required."}, ensure_ascii=False)

    normalized_status = None
    if status is not None:
        normalized_status = str(status).strip().lower()
        if normalized_status not in {s.value for s in NodeStatus}:
            return json.dumps({"success": False, "error": "Invalid status filter."}, ensure_ascii=False)

    rows = store.search_nodes(query, status=normalized_status, limit=max(1, min(int(limit), 20)))
    items = [
        {
            "id": row["id"],
            "type": row["type"],
            "status": row["status"],
            "summary": row["summary"],
            "confidence": row["confidence"],
            "stability": row["stability"],
            "reuse_score": row["reuse_score"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]
    return json.dumps({"success": True, "count": len(items), "items": items}, ensure_ascii=False)


def sparkgraph_stats_tool(*, store: SparkGraphStore | None) -> str:
    if store is None:
        return json.dumps({"success": False, "error": "SparkGraph store is unavailable."}, ensure_ascii=False)

    return json.dumps(
        {
            "success": True,
            "nodes_total": store.count_nodes(),
            "nodes_by_status": store.count_nodes_by_status(),
            "nodes_by_type": store.count_nodes_by_type(),
            "evidence_total": store.count_evidence_rows(),
        },
        ensure_ascii=False,
    )


registry.register(
    name="sparkgraph_record",
    toolset="sparkgraph",
    schema=SPARKGRAPH_RECORD_SCHEMA,
    handler=lambda args, **kw: sparkgraph_record_tool(
        items=args.get("items", []),
        store=kw.get("store"),
        session_id=kw.get("session_id", ""),
        turn_index=int(kw.get("turn_index", 0) or 0),
        source_kind=kw.get("source_kind", "flush"),
        embedding_config=kw.get("embedding_config"),
    ),
    check_fn=check_sparkgraph_requirements,
    emoji="🕸️",
)

registry.register(
    name="sparkgraph_search",
    toolset="sparkgraph",
    schema=SPARKGRAPH_SEARCH_SCHEMA,
    handler=lambda args, **kw: sparkgraph_search_tool(
        query=args.get("query", ""),
        status=args.get("status"),
        limit=int(args.get("limit", 5) or 5),
        store=kw.get("store"),
    ),
    check_fn=check_sparkgraph_requirements,
    emoji="🕸️",
)

registry.register(
    name="sparkgraph_stats",
    toolset="sparkgraph",
    schema=SPARKGRAPH_STATS_SCHEMA,
    handler=lambda args, **kw: sparkgraph_stats_tool(store=kw.get("store")),
    check_fn=check_sparkgraph_requirements,
    emoji="🕸️",
)
