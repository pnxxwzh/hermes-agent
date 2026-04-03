"""Flush-aligned lightweight maintenance for SparkGraph."""

from __future__ import annotations

import json
import time

from agent.sparkgraph.config import SparkGraphEmbeddingConfig
from agent.sparkgraph.embedding import create_embedding, embedding_content_hash, embedding_enabled
from agent.sparkgraph.scoring import (
    DEPRECATE_STABILITY_THRESHOLD,
    evidence_based_promotion,
    should_deprecate_active,
    support_score,
)
from agent.sparkgraph.store import SparkGraphStore
from agent.sparkgraph.types import NodeStatus

STALE_CANDIDATE_DAYS = 14
STALE_CANDIDATE_CONFIDENCE_MAX = 0.55


def run_flush_maintenance(
    store: SparkGraphStore,
    *,
    now_ts: int | None = None,
    embedding_config: SparkGraphEmbeddingConfig | None = None,
    vector_backfill_limit: int = 8,
) -> dict[str, int]:
    now_ts = int(now_ts or time.time())
    stale_cutoff = now_ts - (STALE_CANDIDATE_DAYS * 24 * 60 * 60)

    stale_candidates = store.list_nodes(
        status=NodeStatus.CANDIDATE.value,
        updated_before=stale_cutoff,
    )

    deprecated = 0
    scanned = len(stale_candidates)
    for node in stale_candidates:
        node_id = node["id"]
        confidence = float(node.get("confidence") or 0.0)
        stability = float(node.get("stability") or 0.0)
        evidence_count = store.count_evidence(node_id)
        node["_evidence_count"] = evidence_count

        # B1 cycle-break: if evidence-based promotion says ACTIVE, promote instead
        # of deprecating (the node was useful enough to be recalled)
        proposed = evidence_based_promotion(node)
        if proposed == NodeStatus.ACTIVE:
            store.update_node_status(node_id, status=NodeStatus.ACTIVE.value)
            continue

        if (
            confidence <= STALE_CANDIDATE_CONFIDENCE_MAX
            and stability < DEPRECATE_STABILITY_THRESHOLD
            and evidence_count <= 1
        ):
            store.update_node_status(node_id, status=NodeStatus.DEPRECATED.value)
            deprecated += 1

    stale_actives = store.list_nodes(status=NodeStatus.ACTIVE.value)
    scanned += len(stale_actives)
    for node in stale_actives:
        last_recalled_at = int(node.get("last_recalled_at") or 0)
        reference_ts = last_recalled_at or int(node.get("updated_at") or now_ts)
        days_since_recall_hit = max(0, (now_ts - reference_ts) // (24 * 60 * 60))
        evidence_count = store.count_evidence(node["id"])
        meta = json.loads(node.get("meta") or "{}")
        recall_hits = int(meta.get("recall_hits") or 0)
        support_value = support_score(
            evidence_count=evidence_count,
            active_edge_count=0,
            recall_hits=recall_hits,
        )
        stability = float(node.get("stability") or 0.0)
        if should_deprecate_active(
            days_since_recall_hit=days_since_recall_hit,
            support_score_value=support_value,
            stability=stability,
        ):
            store.update_node_status(node["id"], status=NodeStatus.DEPRECATED.value)
            deprecated += 1

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
        "scanned": scanned,
        "deprecated": deprecated,
        "vectors_backfilled": vectors_backfilled,
    }

    # PPR maintenance (lightweight, called on session_end)
    ppr_result = run_ppr_maintenance(store)
    result["ppr_computed"] = ppr_result.get("ppr_computed")  # type: ignore[assignment]

    return result


# ─── PPR maintenance ────────────────────────────────────────────


def run_ppr_maintenance(store: SparkGraphStore) -> dict[str, bool | int | str | None]:
    """
    Compute global PageRank for graph health diagnostics.

    Currently: reads-only, does not write back to sg_nodes.
    Future: write scores to sg_nodes.pagerank column.

    Also invalidates the graph-structure cache after computation.
    """
    from agent.sparkgraph.pagerank import compute_global_pagerank, invalidate_graph_cache

    try:
        scores = compute_global_pagerank(store)
        invalidate_graph_cache()
        top_node = (
            max(scores.items(), key=lambda x: x[1])[0] if scores else None
        )
        return {
            "ppr_computed": True,
            "nodes_scored": len(scores),
            "top_node": top_node,
        }
    except Exception as exc:
        return {"ppr_computed": False, "error": str(exc)}
