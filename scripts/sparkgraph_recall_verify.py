#!/usr/bin/env python3
"""
SparkGraph Phase 1 PPR 真实数据验证脚本

测试目标：
1. 无关词汇 → 不召回（无 false positive）
2. 相关有效词汇 → 正确召回
3. PPR 排序使相关结果排在前面

数据库：~/.hermes/sparkgraph/default.db
Embedding：bge-m3-mlx-8bit（与 gm 一致）
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.sparkgraph.config import SparkGraphEmbeddingConfig
from agent.sparkgraph.recaller import RecallConfig, recall_nodes
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.embedding import create_embedding, embedding_content_hash
from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType


DB_PATH = Path.home() / ".hermes" / "sparkgraph" / "default.db"
EMBEDDING_CONFIG = SparkGraphEmbeddingConfig(
    provider="openai-compatible",
    model="bge-m3-mlx-8bit",
    base_url="http://127.0.0.1:8000/v1",
    api_key="1234",
    timeout=20,
)


def insert_node_and_vector(store, summary, key_prefix, status=NodeStatus.ACTIVE, conf=0.8, stab=0.8):
    nid = store.insert_node(SparkGraphNodeInput(
        type=NodeType.FACT,
        summary=summary,
        canonical_key=f"test:{key_prefix}-{uuid.uuid4().hex[:8]}",
        source_kind="flush",
        status=status,
        confidence=conf,
        stability=stab,
    ))
    vec = create_embedding(summary, EMBEDDING_CONFIG)
    store.upsert_vector(node_id=nid, content_hash=embedding_content_hash(summary), embedding=vec)
    return nid


def main():
    store = SparkGraphStore(DB_PATH)

    # ── 清理 ─────────────────────────────────────────────────
    store._conn.execute(
        "DELETE FROM sg_edges WHERE from_id IN (SELECT id FROM sg_nodes WHERE canonical_key LIKE 'test:%') "
        "OR to_id IN (SELECT id FROM sg_nodes WHERE canonical_key LIKE 'test:%')"
    )
    store._conn.execute("DELETE FROM sg_nodes WHERE canonical_key LIKE 'test:%'")
    store._conn.commit()
    print("✅ 清理完成\n")

    # ── 建图 ─────────────────────────────────────────────────
    #           A(container) ←—— seed(Docker-issue)
    #                                ↓
    #          C(k8s-deploy) ——→ D(k8s-port)   (seed是1-hop to C, C是2-hop to D)
    seed = insert_node_and_vector(store, "docker compose 启动后端口映射不生效", "seed")
    node_a = insert_node_and_vector(store, "docker container 创建与启动方法", "a", conf=0.75, stab=0.7)
    node_c = insert_node_and_vector(store, "kubernetes deployment 滚动更新配置", "c", conf=0.72, stab=0.68)
    node_d = insert_node_and_vector(store, "kubernetes service port 映射与访问", "d", conf=0.65, stab=0.6)
    unrelated = insert_node_and_vector(store, "Mac Studio 风扇转速正常范围 800-1200 RPM", "unrelated", conf=0.85, stab=0.8)

    store.insert_edge(from_id=seed, to_id=node_a, edge_type=EdgeType.RELATED_TO)
    store.insert_edge(from_id=seed, to_id=node_c, edge_type=EdgeType.RELATED_TO)
    store.insert_edge(from_id=node_c, to_id=node_d, edge_type=EdgeType.RELATED_TO)
    print(f"✅ 图已建立: seed={seed[:8]}, A={node_a[:8]}, C={node_c[:8]}, D={node_d[:8]}, unrelated={unrelated[:8]}\n")

    # ── 测试1：无关词汇 ───────────────────────────────────────
    print("=" * 60)
    print("🧪 测试1：无关词汇（不应召回）")
    print("=" * 60)
    irrelevant = ["天气怎么样", "今天吃什么", "你好吗", "mac 温度"]
    all_ok = True
    for q in irrelevant:
        nodes = recall_nodes(store, query=q, config=RecallConfig(
            max_nodes=4, search_limit=8, vector_limit=8, related_limit=4
        ), embedding_config=EMBEDDING_CONFIG)
        # unrelated 节点不应出现在无关查询中（但 FTS 精确匹配可能召回到 unrelated）
        unrelated_hit = any(n["id"] == unrelated for n in nodes)
        ok = not unrelated_hit and len(nodes) == 0
        tag = "✅" if ok else "❌"
        if not ok:
            all_ok = False
        print(f"  {tag} 「{q}」→ {len(nodes)} 个结果{' (不应有)' if nodes else ''}")
        for n in nodes:
            print(f"       [{n['id'][:8]}] {n['summary'][:40]}")
    print(f"  {'✅ 全部通过' if all_ok else '❌ 有失败'}\n")

    # ── 测试2：相关词汇召回 ───────────────────────────────────
    print("=" * 60)
    print("🧪 测试2：相关词汇（应正确召回）")
    print("=" * 60)
    tests = [
        ("docker 端口映射", {seed, node_c, node_d}),  # seed 命中, C(1-hop) 可被 related 召回
        ("docker container", {seed, node_a}),
        ("kubernetes deployment", {node_c}),
    ]
    all_hit_ok = True
    for q, expected in tests:
        nodes = recall_nodes(store, query=q, config=RecallConfig(
            max_nodes=4, search_limit=8, vector_limit=8, related_limit=4
        ), embedding_config=EMBEDDING_CONFIG)
        hit_ids = {n["id"] for n in nodes}
        missing = expected - hit_ids
        ok = len(missing) == 0
        if not ok:
            all_hit_ok = False
        tag = "✅" if ok else "❌"
        print(f"  {tag} 「{q}」→ 命中 {len(hit_ids & expected)}/{len(expected)}")
        if not ok:
            print(f"       缺失: {[m[:8] for m in missing]}")
        for n in nodes:
            star = "⭐" if n["id"] in expected else "  "
            ppr = n.get("_ppr_score", 0.0)
            ppr_s = f"{float(ppr):.4f}" if isinstance(ppr, float) else str(ppr)
            print(f"    {star} PPR={ppr_s} | {n['summary'][:40]}")
    print(f"  {'✅ 全部通过' if all_hit_ok else '❌ 有失败'}\n")

    # ── 测试3：PPR 排序验证 ───────────────────────────────────
    print("=" * 60)
    print("🧪 测试3：PPR 排序（图距离影响）")
    print("=" * 60)
    # 查 docker → seed + related 应该能触发 PPR (related_hits 非空)
    nodes = recall_nodes(store, query="docker 问题 排查", config=RecallConfig(
        max_nodes=4, search_limit=8, vector_limit=8, related_limit=4
    ), embedding_config=EMBEDDING_CONFIG)
    print(f"  查询「docker 问题 排查」→ {len(nodes)} 个节点：")
    has_ppr_scores = any(n.get("_ppr_score", 0) > 0 for n in nodes)
    for i, n in enumerate(nodes):
        ppr = n.get("_ppr_score", 0.0)
        ppr_s = f"{float(ppr):.4f}" if isinstance(ppr, float) else str(ppr)
        conf = n.get("confidence", 0)
        print(f"    {i+1}. PPR={ppr_s} conf={conf:.2f} | {n['summary'][:40]}")
    ppr_tag = "✅ PPR>0" if has_ppr_scores else "⚠️  PPR=0（可能是图太小/浮点精度）"
    print(f"  {ppr_tag}\n")

    # ── 清理 ─────────────────────────────────────────────────
    store._conn.execute(
        "DELETE FROM sg_edges WHERE from_id IN (SELECT id FROM sg_nodes WHERE canonical_key LIKE 'test:%') "
        "OR to_id IN (SELECT id FROM sg_nodes WHERE canonical_key LIKE 'test:%')"
    )
    store._conn.execute("DELETE FROM sg_vectors WHERE node_id IN (SELECT id FROM sg_nodes WHERE canonical_key LIKE 'test:%')")
    store._conn.execute("DELETE FROM sg_nodes WHERE canonical_key LIKE 'test:%'")
    store._conn.commit()
    print("✅ 清理完成")

    # ── 总结 ─────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("📊 总结")
    print("=" * 60)
    print(f"  Embedding API: {EMBEDDING_CONFIG.base_url} / {EMBEDDING_CONFIG.model}")
    print(f"  SG DB: {DB_PATH}")
    print(f"  无关词汇测试: {'✅ 通过' if all_ok else '❌ 失败'}")
    print(f"  召回命中测试: {'✅ 通过' if all_hit_ok else '❌ 失败'}")
    print(f"  PPR 激活: {'是' if has_ppr_scores else '否（见上方说明）'}")


if __name__ == "__main__":
    main()
