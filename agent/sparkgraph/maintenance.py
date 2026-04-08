"""Flush-aligned lightweight maintenance for SparkGraph.

无 CANDIDATE，无 evidence_based_promotion。
deprecated 规则：30天无召回 + validated_count≤1
"""

from __future__ import annotations

import logging as _logging
import time

_log = _logging.getLogger(__name__)

from agent.sparkgraph.config import SparkGraphEmbeddingConfig
from agent.sparkgraph.embedding import create_embedding, embedding_content_hash, embedding_enabled
from agent.sparkgraph.scoring import should_deprecate_active
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
    scanned = 0

    # 检查所有 active 节点是否应 deprecated
    for node in store.list_nodes(status=NodeStatus.ACTIVE.value):
        scanned += 1
        last_recall = int(node.get("last_recalled_at") or 0)
        updated_at = int(node.get("updated_at") or now_ts)
        validated = int(node.get("validated_count") or 0)

        # reference_ts 选取逻辑（修复 B1 根因）：
        #
        # 核心不变量：increment_validated_count 总是设置 last_recalled_at = now。
        # 因此任何真正的召回之后，last_recalled_at >= updated_at。
        # 如果 last_recalled_at < updated_at，说明节点是在上次内容更新之前被召回的，
        # updated_at 的刷新来源于 dedup（而非新的召回）。
        #
        # 三段式判断：
        #
        # 1. last_recalled_at > updated_at：
        #    节点在上次更新之后被召回过。last_recalled_at 是可信的最后召回时间。
        #
        # 2. last_recalled_at <= updated_at 且 validated_count > 0：
        #    节点被召回（validated_count>0），但召回发生在上次内容更新之前。
        #    updated_at 的更新来源于 dedup 或其他内容修改，不反映新的召回活动。
        #    此时 last_recalled_at 仍是最新的召回时间信号。
        #
        # 3. last_recalled_at <= updated_at 且 validated_count == 0：
        #    从未被计数过的节点，idle 时间退回到 updated_at。
        #    新节点 updated_at≈now → 不淘汰；长期陈旧的未召回节点则允许自然过期。
        #
        # 覆盖场景：
        #   (a) 正常召回：last_recalled_at(召回时间) > updated_at(上次更新时间) ✓
        #   (b) dedup：last_recalled_at(召回时间) <= updated_at(昨天) 但 validated_count>0
        #       → 仍用 last_recalled_at（dedup 不应重置 idle 时间）✓
        #   (c) 老数据：last_recalled_at=0 <= updated_at(31天前)，validated_count>0
        #       → 用 last_recalled_at=0，validated_count>0 → 走 should_deprecate_active ✓
        #   (d) 新节点：last_recalled_at=0 <= updated_at(now)，validated_count=0
        #       → reference_ts=updated_at≈now → days_idle=0，不淘汰 ✓
        #   (e) 老旧未召回节点：last_recalled_at=0 <= updated_at(31天前)，validated_count=0
        #       → reference_ts=updated_at=31天前 → days_idle=31，可淘汰 ✓
        if last_recall > updated_at:
            reference_ts = last_recall
        elif validated > 0:
            # validated_count>0：节点曾被计数过（last_recalled_at 是可信的最后召回时间）
            reference_ts = last_recall or updated_at
        else:
            # validated_count=0：从未被计数，使用 updated_at 区分新节点与长期陈旧节点
            reference_ts = updated_at

        days_idle = max(0, (now_ts - reference_ts) // 86400)

        if should_deprecate_active(
            days_since_recall_hit=days_idle,
            validated_count=validated,
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
    else:
        _log.info("SparkGraph embedding disabled, vector backfill skipped")

    result: dict[str, int] = {
        "scanned": scanned,
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
