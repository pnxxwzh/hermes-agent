"""Flush-aligned lightweight maintenance for SparkGraph.

无 CANDIDATE，无 evidence_based_promotion。
deprecated 规则：30天无召回 + validated_count≤1 + stability<0.45
"""

from __future__ import annotations

import json
import time

from agent.sparkgraph.config import SparkGraphEmbeddingConfig
from agent.sparkgraph.embedding import create_embedding, embedding_content_hash, embedding_enabled
from agent.sparkgraph.scoring import should_deprecate_active, STALE_RECALL_DAYS
from agent.sparkgraph.store import SparkGraphStore
from agent.sparkgraph.types import NodeStatus


def run_flush_maintenance(
    store: SparkGraphStore,
    *,
    now_ts: int | None = None,
    embedding_config: SparkGraphEmbeddingConfig | None = None,
    vector_backfill_limit: int = 8,
) -> dict[str, int]:
    now_ts = int(now_ts or time.time())

    deprecated = 0

    # 检查所有 active 节点是否应 deprecated
    for node in store.list_nodes(status=NodeStatus.ACTIVE.value):
        last_recall = int(node.get("last_recalled_at") or 0)
        reference_ts = last_recall or int(node.get("updated_at") or now_ts)
        days_idle = max(0, (now_ts - reference_ts) // 86400)
        validated = int(node.get("validated_count") or 0)
        stability = float(node.get("stability") or 0.0)

        if should_deprecate_active(
            days_since_recall_hit=days_idle,
            validated_count=validated,
            stability=stability,
        ):
            store.update_node_status(node["id"], status=NodeStatus.DEPRECATED.value)
            deprecated += 1

    # 向量补全
    vectors_backfilled = 0
    if embedding_enabled(embedding_config):
        missing_vectors = store.list_nodes_missing_vectors(
            status=NodeStatus.ACTIVE.value,
            limit=vector_backfill_limit,
        )
        for node in missing_vectors:
            summary = str(node.get("summary") or "").strip()
            if not summary:
                continue
            try:
                store.upsert_vector(
                    node_id=node["id"],
                    content_hash=embedding_content_hash(summary),
                    embedding=create_embedding(summary, embedding_config),
                )
                vectors_backfilled += 1
            except Exception:
                continue

    result: dict[str, int] = {
        "deprecated": deprecated,
        "vectors_backfilled": vectors_backfilled,
    }

    ppr_result = run_ppr_maintenance(store)
    result["ppr_computed"] = ppr_result.get("ppr_computed")  # type: ignore[assignment]

    return result


def run_ppr_maintenance(store: SparkGraphStore) -> dict[str, bool | int | str | None]:
    from agent.sparkgraph.pagerank import compute_global_pagerank, invalidate_graph_cache

    try:
        scores = compute_global_pagerank(store)
        invalidate_graph_cache()
        top_node = max(scores.items(), key=lambda x: x[1])[0] if scores else None
        return {"ppr_computed": True, "nodes_scored": len(scores), "top_node": top_node}
    except Exception as exc:
        return {"ppr_computed": False, "error": str(exc)}
