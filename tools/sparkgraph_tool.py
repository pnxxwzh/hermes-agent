"""SparkGraph tools."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from agent.sparkgraph.dedup import build_canonical_key, find_cross_type_dedup_match, find_dedup_match
from agent.sparkgraph.embedding import create_embedding, embedding_content_hash, embedding_enabled
from agent.sparkgraph.config import SparkGraphEmbeddingConfig
from agent.sparkgraph.scoring import initial_score_for
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
            "edges": {
                "type": "array",
                "description": (
                    "Semantic relationships between recorded items (optional). "
                    "Each edge links two items from this batch. "
                    "SOLVES: an ISSUE or FACT is resolved by a SKILL. "
                    "DEPENDS_ON: one item requires another as prerequisite. "
                    "RELATED_TO: loosely connected (prefer auto-link for loose ties). "
                    "DERIVED_FROM: one item succeeds or replaces another. "
                    "CONFLICTS_WITH: two items are mutually exclusive. "
                    "Only link items with clear intentional relationships; do not over-connect."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {
                            "type": "string",
                            "description": (
                                "Source node reference — must exactly or closely match the summary "
                                "text of a node in this batch. Use the full summary or a distinctive "
                                "fragment (≥ 5 chars) to ensure correct matching."
                            ),
                        },
                        "to": {
                            "type": "string",
                            "description": (
                                "Target node reference — same matching rules as 'from'."
                            ),
                        },
                        "type": {
                            "type": "string",
                            "enum": [
                                "RELATED_TO",
                                "SOLVES",
                                "DEPENDS_ON",
                                "DERIVED_FROM",
                                "APPLIES_TO",
                                "CONFLICTS_WITH",
                            ],
                        },
                        "instruction": {
                            "type": "string",
                            "description": (
                                "Brief explanation of why this relationship exists (max 200 chars)."
                            ),
                            "maxLength": 200,
                        },
                    },
                    "required": ["from", "to", "type"],
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
        "status distribution, and type distribution."
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


def _resolve_node_ref(
    ref: str,
    recorded_ids: list[str],
    store: SparkGraphStore,
) -> str | None:
    """
    将边的 from/to 引用解析为 node_id（评分制，最佳匹配优先）。

    仅在 recorded_ids 范围内查找，不做全局 fallback。

    评分规则（分高者赢）：
      1. exact match → score = 100
      2. full summary is prefix of ref → score = 90
      3. ref is prefix of full summary (ref_len >= 5) → score = 80
      4. ref_len < len(summary) + ref is substring of summary (ref_len >= 5) → score = 70
      5. character bigram Jaccard >= 0.5 → score = 60
      6. character bigram Jaccard >= 0.25 → score = 40

    Args:
        ref: LLM 输出的 from 或 to 引用（summary 文本片段或 id）
        recorded_ids: 本批次写入的 node_id 列表
        store: SparkGraphStore 实例

    Returns:
        node_id 或 None（无法解析）
    """
    if not ref or not recorded_ids:
        return None

    ref_lower = ref.lower()
    ref_len = len(ref)
    best_id: str | None = None
    best_score = 0

    for nid in recorded_ids:
        node = store.get_node(nid)
        if not node:
            continue

        summary_lower = str(node.get("summary") or "").lower()
        summary_raw = str(node.get("summary") or "")

        score = 0

        # 1. exact id match
        if nid == ref:
            score = 100

        # 2. summary is prefix of ref (ref is longer/equal → likely the exact match)
        elif ref_len >= len(summary_lower) and summary_lower == ref_lower[: len(summary_lower)]:
            score = 90

        # 3. ref is prefix of summary (short keyword match)
        elif ref_len <= len(summary_lower) and summary_lower.startswith(ref_lower):
            score = 80

        # 4. ref is substring of summary (len >= 5)
        elif ref_len >= 5 and ref_len <= len(summary_lower) and ref_lower in summary_lower:
            score = 70

        # 5-6. bigram fallback
        else:
            ref_bigrams = _char_ngram_set(ref_lower)
            summary_bigrams = _char_ngram_set(summary_lower)
            if ref_bigrams and summary_bigrams:
                overlap = len(ref_bigrams & summary_bigrams)
                union = len(ref_bigrams | summary_bigrams)
                if union > 0:
                    jaccard = overlap / union
                    if jaccard >= 0.5:
                        score = 60
                    elif jaccard >= 0.25:
                        score = 40

        if score > best_score:
            best_score = score
            best_id = nid

    return best_id


def _insert_llm_extracted_edges(
    edges: list[dict[str, Any]],
    recorded_ids: list[str],
    store: SparkGraphStore,
    batch_records: list[tuple[str, NodeType, str]] | None = None,
) -> int:
    """
    解析 LLM 提取的边，执行 name→id 映射、方向校验、去重，写入 sg_edges。

    边界条件处理：
        - edges 为空/非 list：返回 0
        - store 为 None：返回 0
        - 单个边字段缺失 from/to/type：跳过该边
        - from == to（自环）：跳过（但若因 dedup 导致 from_id==to_id，
          则从 batch_records 找替代候选）
        - EdgeType 非法：跳过
        - 无法解析 from/to 为 node_id：跳过
        - pair 去重（无向化，但含 edge_type）：tuple(sorted((from_id, to_id))) + (edge_type,)
        - insert_edge 唯一约束冲突：静默捕获

    Args:
        edges: LLM 输出的边列表
        recorded_ids: 本次 sparkgraph_record 写入的所有 node_id 列表
        store: SparkGraphStore 实例
        batch_records: 可选，(node_id, node_type, summary) 元组列表。
            当 from_id==to_id（dedup 导致 self-loop）时，
            从 batch_records 中找与 ref 匹配 score 最高的替代 node_id。

    Returns:
        成功写入的边数量（去重后）
    """
    if not edges or not isinstance(edges, list):
        return 0
    if store is None:
        return 0

    created = 0
    seen_pairs: set[tuple[str, str, str]] = set()
    # batch_records: (node_id, node_type, summary)
    if batch_records is None:
        batch_records = []

    for edge in edges:
        if not isinstance(edge, dict):
            continue

        from_ref = str(edge.get("from", "")).strip()
        to_ref = str(edge.get("to", "")).strip()
        edge_type_str = str(edge.get("type", "")).strip().upper()
        instruction = str(edge.get("instruction", ""))[:200]

        # 跳过非法字段
        if not from_ref or not to_ref or not edge_type_str:
            continue

        # 跳过自环（按引用层级检查）
        if from_ref == to_ref:
            continue

        # 解析 EdgeType
        try:
            edge_type = EdgeType[edge_type_str]
        except KeyError:
            continue

        # name → id 解析
        from_id = _resolve_node_ref(from_ref, recorded_ids, store)
        to_id = _resolve_node_ref(to_ref, recorded_ids, store)

        if not from_id or not to_id:
            continue

        # self-loop 处理：
        #   1. 引用层面相等 → 跳过（显式自环）
        #   2. 引用层面不同但解析到同一节点 → 可能是 dedup 导致的，
        #      从 batch_records 找与 ref 匹配度最高的替代节点
        if from_id == to_id:
            # 尝试找到与 to_ref 匹配（而非 from_ref）的替代节点
            # 优先使用 batch_records 中非 self 的节点
            alt_to_id = None
            if batch_records:
                alt_candidates = [
                    (nid, summary)
                    for nid, _, summary in batch_records
                    if nid != from_id and summary
                ]
                if alt_candidates:
                    # 对每个候选按 ref 相似度评分，取最高
                    best_alt_score = -1
                    for alt_nid, alt_summary in alt_candidates:
                        alt_ref_lower = to_ref.lower()
                        alt_summary_lower = alt_summary.lower()
                        if alt_ref_lower == alt_summary_lower:
                            alt_to_id = alt_nid
                            break
                        if len(alt_ref_lower) >= 5:
                            if alt_ref_lower in alt_summary_lower:
                                score = len(alt_ref_lower)  # 越长越准
                            else:
                                score = 0
                        else:
                            score = 0
                        if score > best_alt_score:
                            best_alt_score = score
                            alt_to_id = alt_nid
            if alt_to_id and alt_to_id != from_id:
                to_id = alt_to_id
            else:
                continue  # 真正的 self-loop，跳过

        # pair 去重（无向化，但保留 edge_type 以支持多类型）
        pair = tuple(sorted((from_id, to_id))) + (edge_type,)
        if pair in seen_pairs:
            continue

        # 写入
        try:
            store.insert_edge(
                from_id=from_id,
                to_id=to_id,
                edge_type=edge_type,
                weight=1.0,
                meta={
                    "origin": "llm_extracted",
                    "direction_note": instruction,
                },
            )
            seen_pairs.add(pair)
            created += 1
        except Exception:
            # 唯一约束冲突等，静默忽略
            continue

    return created


def sparkgraph_record_tool(
    *,
    items: list[dict[str, Any]],
    store: SparkGraphStore | None,
    session_id: str,
    turn_index: int,
    source_kind: str = "flush",
    embedding_config: SparkGraphEmbeddingConfig | None = None,
    edges: list[dict[str, Any]] | None = None,
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
        if existing is not None:
            # 去重命中：来源即命运，重新查表更新 confidence 和 status
            existing_node = store.get_node(existing.node_id) or {}
            existing_type_raw = str(existing.node_type or existing_node.get("type") or node_type.value).strip().upper()
            try:
                scoring_type = _normalize_node_type(existing_type_raw)
            except KeyError:
                scoring_type = node_type
            score_result = initial_score_for(source_kind)
            store.update_node_scoring(
                existing.node_id,
                confidence=score_result.confidence,
                status=score_result.initial_status.value,
                confidence_components=None,  # 清除旧字段，保持 meta 干净
            )
            # 去重命中：validated_count++（知识再次被确认）
            store.increment_validated_count([existing.node_id])
            store.merge_source_sessions(existing.node_id, session_id)
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

        # 新建节点：来源即命运，直接查表
        score_result = initial_score_for(source_kind)
        node_id = store.insert_node(
            SparkGraphNodeInput(
                type=node_type,
                summary=summary,
                canonical_key=canonical_key,
                source_kind=source_kind,
                status=score_result.initial_status,
                confidence=score_result.confidence,
                default_inject=source_kind not in {"reflection", "shadow"},
                meta={"source_kind": source_kind, "source_sessions": [session_id] if session_id else []},
            )
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

    # ── LLM-extracted edges ──────────────────────────────────────
    llm_edges_created = 0
    if edges:
        try:
            llm_edges_created = _insert_llm_extracted_edges(
                edges=edges,
                recorded_ids=recorded_ids,
                store=store,
                batch_records=batch_records,
            )
        except Exception:
            llm_edges_created = 0
    # ─────────────────────────────────────────────────────────────

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
            "llm_edges_created": llm_edges_created,
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
        edges=args.get("edges"),
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
