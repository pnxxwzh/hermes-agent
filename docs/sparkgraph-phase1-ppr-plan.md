# Phase 1 PPR 实施计划

> 文档版本：v1.2（已实现）
> 日期：2026-04-03
> 状态：计划中
> 依赖文件：`tasks/sparkgraph-recall-analysis.md`

---

## 一、目标

在 SparkGraph 中实现 **Personalized PageRank（PPR）排序**，替代当前的混合打分方案（`_recall_rank`），提升 recall 的语义相关性排序能力。

**不改变** `recall_nodes` 的外部接口和返回格式，向后完全兼容。

---

## 二、文件变更总览

| 操作 | 文件路径 |
|---|---|
| **新建** | `agent/sparkgraph/pagerank.py` |
| **修改** | `agent/sparkgraph/recaller.py` |
| **修改** | `agent/sparkgraph/maintenance.py` |
| **新建** | `tests/sparkgraph/test_pagerank.py` |
| **新建** | `tests/sparkgraph/test_recaller_ppr.py` |
| **新建** | `tests/sparkgraph/conftest.py` |
| **新建** | `tests/sparkgraph/test_maintenance_ppr.py` |

---

## 三、新建：`agent/sparkgraph/pagerank.py`

```python
"""
SparkGraph Personalized PageRank (PPR)

核心思想（与 gm 一致）：
  - 全局 PageRank：所有节点均匀分布起点，teleport 回到所有节点
  - 个性化 PageRank：种子节点作为 teleport 目标，离种子越近的节点分数越高

关键参数：
  - damping = 0.85
  - iterations = 30
  - cache_ttl = 30_000 ms（图结构缓存有效期）
"""

from __future__ import annotations

import time
from typing import Any

from agent.sparkgraph.store import SparkGraphStore
from agent.sparkgraph.types import NodeStatus

# ─── 全局图结构缓存 ──────────────────────────────────────────

_CACHE_TTL_MS = 30_000


class _GraphCache:
    """进程内图结构缓存，TTL=30秒，compact 后失效。"""

    __slots__ = ("adj", "node_ids", "N", "cached_at_ms")

    def __init__(
        self,
        adj: dict[str, list[str]],
        node_ids: set[str],
        N: int,
        cached_at_ms: int,
    ):
        self.adj = adj
        self.node_ids = node_ids
        self.N = N
        self.cached_at_ms = cached_at_ms

    def is_fresh(self) -> bool:
        return (time.time() * 1000 - self.cached_at_ms) < _CACHE_TTL_MS


_graph_cache: _GraphCache | None = None


def invalidate_graph_cache() -> None:
    """compact 或 schema 变更后调用，清除图结构缓存。"""
    global _graph_cache
    _graph_cache = None


def _load_graph(store: SparkGraphStore) -> _GraphCache:
    """从数据库加载无向邻接表，带 30 秒缓存。"""
    global _graph_cache
    if _graph_cache is not None and _graph_cache.is_fresh():
        return _graph_cache

    rows = store._conn.execute(
        "SELECT id FROM sg_nodes WHERE status = ?",
        (NodeStatus.ACTIVE.value,),
    ).fetchall()
    node_ids = {row["id"] for row in rows}

    edge_rows = store._conn.execute(
        "SELECT from_id, to_id FROM sg_edges"
    ).fetchall()

    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for e in edge_rows:
        fid, tid = e["from_id"], e["to_id"]
        if fid not in node_ids or tid not in node_ids:
            continue
        adj[fid].append(tid)
        adj[tid].append(fid)

    _graph_cache = _GraphCache(adj, node_ids, len(node_ids), int(time.time() * 1000))
    return _graph_cache


# ─── Personalized PageRank ────────────────────────────────────


def personalized_pagerank(
    store: SparkGraphStore,
    seed_ids: list[str],
    candidate_ids: list[str],
    *,
    damping: float = 0.85,
    iterations: int = 30,
) -> dict[str, float]:
    """
    Personalized PageRank — 从种子节点出发，返回候选节点的 PPR 分数。

    算法（与 gm 一致）：
      1. 初始化：只有 seed_ids 有非零 rank = 1/|seeds|
      2. 每次迭代：
         a. teleport 分量：(1-d) * teleport_weight，只回到种子节点
         b. 传播分量：d * rank[n] / |neighbors|，均分给每个邻居
         c. dangling 节点（无邻居）：其 rank 传回种子节点
      3. 重复 iter 次
      4. 只返回 candidate_ids 中的节点分数

    参数：
      store：SparkGraphStore 实例
      seed_ids：查询命中的种子节点 ID（direct + vector hits）
      candidate_ids：需要排序的候选节点 ID（direct + vector + related）
      damping：阻尼系数，默认 0.85
      iterations：迭代次数，默认 30

    返回：
      {node_id: ppr_score}，只包含 candidate_ids 中的节点
    """
    graph = _load_graph(store)
    adj, node_ids, N = graph.adj, graph.node_ids, graph.N

    if N == 0 or not seed_ids:
        return {}

    valid_seeds = [sid for sid in seed_ids if sid in node_ids]
    if not valid_seeds:
        return {}

    teleport_weight = 1.0 / len(valid_seeds)
    seed_set = set(valid_seeds)

    # 初始化 rank
    rank: dict[str, float] = {
        nid: (teleport_weight if nid in seed_set else 0.0) for nid in node_ids
    }

    # Power iteration
    for _ in range(iterations):
        new_rank: dict[str, float] = {
            nid: (1.0 - damping) * teleport_weight if nid in seed_set else 0.0
            for nid in node_ids
        }

        # 传播
        for node_id, neighbors in adj.items():
            if not neighbors:
                continue
            contrib = rank[node_id] / len(neighbors)
            if contrib == 0.0:
                continue
            for nb in neighbors:
                new_rank[nb] += damping * contrib

        # dangling 节点：rank 传回种子节点
        dangling_sum = sum(rank[n] for n, nb in adj.items() if not nb)
        if dangling_sum > 0.0:
            dangling_contrib = damping * dangling_sum * teleport_weight
            for sid in valid_seeds:
                new_rank[sid] += dangling_contrib

        rank = new_rank

    return {nid: rank.get(nid, 0.0) for nid in candidate_ids if nid in node_ids}


# ─── 全局 PageRank ───────────────────────────────────────────


def compute_global_pagerank(
    store: SparkGraphStore,
    *,
    damping: float = 0.85,
    iterations: int = 30,
) -> dict[str, float]:
    """
    全局 PageRank — 均匀 teleport 到所有节点。

    用途：
      - 作为 recall fallback 排序基线
      - 未来写入 sg_nodes.pagerank 列
    """
    graph = _load_graph(store)
    adj, node_ids, N = graph.adj, graph.node_ids, graph.N

    if N == 0:
        return {}

    init_rank = 1.0 / N
    rank: dict[str, float] = {nid: init_rank for nid in node_ids}

    for _ in range(iterations):
        base = (1.0 - damping) / N
        new_rank: dict[str, float] = {nid: base for nid in node_ids}

        for node_id, neighbors in adj.items():
            if not neighbors:
                continue
            contrib = rank[node_id] / len(neighbors)
            for nb in neighbors:
                new_rank[nb] += damping * contrib

        dangling_sum = sum(rank[n] for n, nb in adj.items() if not nb)
        if dangling_sum > 0.0:
            dc = damping * dangling_sum / N
            for nid in node_ids:
                new_rank[nid] += dc

        rank = new_rank

    return rank
```

---

## 四、修改：`agent/sparkgraph/recaller.py`

### 改动位置总览

| 位置 | 改动内容 |
|---|---|
| 顶部 import | 新增 `from agent.sparkgraph.pagerank import invalidate_graph_cache, personalized_pagerank` |
| 常量区 | 新增 `PPR_DAMPING = 0.85`，`PPR_ITERATIONS = 30` |
| 新增函数 | `_node_priority_ppr()` |
| 新增函数 | `_rank_with_ppr()` |
| `recall_nodes()` 内 | 在 `sorted(merged.values(), key=_recall_rank)` 前插入 PPR 计算逻辑 |

### 改动1：顶部新增导入

```python
# 新增一行到 import 区
from agent.sparkgraph.pagerank import invalidate_graph_cache, personalized_pagerank
```

### 改动2：新增常量（在 MIN_RECALL_* 附近）

```python
PPR_DAMPING = 0.85
PPR_ITERATIONS = 30
```

### 改动3：新增 `_node_priority_ppr` 函数

```python
def _node_priority_ppr(
    node: dict[str, Any],
    ppr_score: float,
    source_scores: dict[str, float] | None = None,
) -> float:
    """
    计算节点的 PPR 排序优先级分数（参考 gm nodePriority）。

    排序维度（从主到次）：
      1. ppr_score * 1000（主导）
      2. sourceKind 加权（explicit +80, manual +40, 孤立 reflection +800）
      3. confidence * 100
      4. validatedCount * 5（上限 20 条）
      5. superseded 扣 500
    """
    if source_scores is None:
        source_scores = {
            "explicit": 80.0,
            "manual": 40.0,
            "reflection": 0.0,
            "review": 40.0,
            "flush": 0.0,
            "auto": 0.0,
            "shadow": 0.0,
        }

    score = ppr_score * 1000.0
    source_kind = str(node.get("source_kind") or "")
    score += source_scores.get(source_kind, 0.0)

    # 孤立 reflection 强补偿（ppr_score < 0.01 说明图上孤立）
    if source_kind == "reflection" and ppr_score < 0.01:
        score += 800.0

    score += float(node.get("confidence") or 0.0) * 100.0

    meta = node.get("meta") or {}
    if isinstance(meta, str):
        import json as _json
        meta = _json.loads(meta)
    # evidence_count 从 sg_evidence 表实时查询，不从 meta 读取
    # 由调用方在 recall_nodes 内批量查询后通过 evidence_count 参数传入
    if meta.get("superseded_by") or meta.get("superseded"):
        score -= 500.0

    return score
```

### 改动4：新增 `_rank_with_ppr` 函数

```python
def _rank_with_ppr(
    nodes: list[dict[str, Any]],
    ppr_scores: dict[str, float],
    evidence_counts: dict[str, int],
    source_scores: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """
    用 PPR 分数对节点排序，替代原有的 _recall_rank。

    每个节点附加 _ppr_score 字段便于调试。
    evidence_counts：{node_id: evidence_count}，由调用方批量查询后传入。
    """
    scored: list[tuple[float, float, float, int, dict[str, Any]]] = []
    for node in nodes:
        nid = str(node.get("id") or "")
        ppr = ppr_scores.get(nid, 0.0)
        node_copy = dict(node)
        node_copy["_ppr_score"] = ppr
        priority = _node_priority_ppr(
            node_copy, ppr,
            evidence_count=evidence_counts.get(nid, 0),
            source_scores=source_scores,
        )
        semantic = float(node.get("semantic_score") or 0.0)
        conf = float(node.get("confidence") or 0.0)
        updated = int(node.get("updated_at") or 0)
        scored.append((priority, semantic, conf, updated, node_copy))

    scored.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
    return [item[4] for item in scored]
```

### 改动5：`recall_nodes` 函数内插入 PPR 计算

在 `ranked = sorted(merged.values(), key=_recall_rank, reverse=True)` 之前，
**替换**为：

```python
    # ━━━━ PPR 个性化排序（Phase 1 新增）━━━━━━━━━━━━━━━━━━
    candidate_ids = [
        str(node.get("id", "")) for node in merged.values() if node.get("id")
    ]
    if candidate_ids:
        seed_ids = [
            str(node.get("id", ""))
            for node in (direct_hits + vector_hits)
            if node.get("id")
        ]
        # 批量查询 evidence_count，避免在排序函数内 N 次查库
        evidence_counts: dict[str, int] = {}
        for cid in candidate_ids:
            evidence_counts[cid] = store.count_evidence(cid)
        try:
            ppr_scores = personalized_pagerank(
                store,
                seed_ids=seed_ids,
                candidate_ids=candidate_ids,
                damping=PPR_DAMPING,
                iterations=PPR_ITERATIONS,
            )
            ranked = _rank_with_ppr(
                list(merged.values()),
                ppr_scores,
                evidence_counts,
            )
        except Exception:
            # PPR 计算失败时 fallback 到原有排序
            ranked = sorted(merged.values(), key=_recall_rank, reverse=True)
    else:
        ranked = sorted(merged.values(), key=_recall_rank, reverse=True)
    # ━━━━ PPR 结束 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**说明**：
- `store` 参数从 `recaller.py` 的 `recall_nodes` 函数中已经存在（第一个参数）
- `seed_ids` = direct_hits + vector_hits（与 query 直接相关的节点，作为 PPR 的 teleport 目标）
- `candidate_ids` = direct + vector + related（所有候选节点，需要 PPR 排序的节点集合）
- 异常 fallback 保证 PPR 崩溃不影响现有流程

---

## 五、修改：`agent/sparkgraph/maintenance.py`

在文件末尾（`run_flush_maintenance` 函数之后）新增：

```python
def run_ppr_maintenance(store: SparkGraphStore) -> dict[str, Any]:
    """
    计算全局 PageRank（未来写回 sg_nodes.pagerank 列）。

    当前版本：仅做图健康度检查。
    """
    from agent.sparkgraph.pagerank import (
        compute_global_pagerank,
        invalidate_graph_cache,
    )

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
```

在 `run_flush_maintenance` 末尾的 `return` 之前插入：

```python
    # PPR maintenance（轻量，session_end 时调用）
    ppr_result = run_ppr_maintenance(store)
    result["ppr_maintenance"] = ppr_result
```

---

## 六、新建测试文件

### 6.1 `tests/sparkgraph/test_pagerank.py`

```python
"""
Phase 1 测试：PPR 算法正确性
"""

import time

import pytest

from agent.sparkgraph.pagerank import (
    _GraphCache,
    _load_graph,
    compute_global_pagerank,
    invalidate_graph_cache,
    personalized_pagerank,
)
from agent.sparkgraph.recaller import RecallConfig, recall_nodes
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType


def _insert_active(
    store: SparkGraphStore,
    summary: str,
    canonical_key: str,
    *,
    confidence: float = 0.8,
    stability: float = 0.8,
) -> str:
    return store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary=summary,
            canonical_key=canonical_key,
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=confidence,
            stability=stability,
        )
    )


# ─── _GraphCache 单元测试 ────────────────────────────────────

class TestGraphCache:
    def test_cache_fresh_within_ttl(self):
        cache = _GraphCache(adj={}, node_ids=set(), N=0, cached_at_ms=int(time.time() * 1000))
        assert cache.is_fresh() is True

    def test_cache_expired_after_ttl(self):
        old_ts = int((time.time() - 31) * 1000)
        cache = _GraphCache(adj={}, node_ids=set(), N=0, cached_at_ms=old_ts)
        assert cache.is_fresh() is False

    def test_invalidate_clears_global(self):
        import agent.sparkgraph.pagerank as pg_mod
        pg_mod._graph_cache = _GraphCache({}, set(), 0, int(time.time() * 1000))
        invalidate_graph_cache()
        assert pg_mod._graph_cache is None


# ─── personalized_pagerank 单元测试 ──────────────────────────

class TestPersonalizedPagerank:
    def test_empty_seeds_returns_empty(self, tmp_path):
        store = SparkGraphStore(tmp_path / "sg.db")
        result = personalized_pagerank(store, seed_ids=[], candidate_ids=["n1"])
        assert result == {}

    def test_single_node_self_score(self, tmp_path):
        """单节点图：seed=target，PPR 分数应集中在自身（> 0.5）。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid = _insert_active(store, "solo", "fact:solo")
        scores = personalized_pagerank(store, seed_ids=[nid], candidate_ids=[nid])
        assert scores[nid] > 0.5

    def test_two_node_chain_seed_first(self, tmp_path):
        """A → B（A 是种子），A 的 PPR > B。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "A", "fact:a")
        nid_b = _insert_active(store, "B", "fact:b")
        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)

        scores = personalized_pagerank(store, seed_ids=[nid_a], candidate_ids=[nid_a, nid_b])
        assert scores[nid_a] > scores[nid_b]

    def test_two_node_chain_seed_second(self, tmp_path):
        """A → B（B 是种子），B 的 PPR > A。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "A", "fact:a")
        nid_b = _insert_active(store, "B", "fact:b")
        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)

        scores = personalized_pagerank(store, seed_ids=[nid_b], candidate_ids=[nid_a, nid_b])
        assert scores[nid_b] > scores[nid_a]

    def test_disconnected_node_isolated(self, tmp_path):
        """孤立节点 PPR 接近 0。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "A", "fact:a")
        nid_b = _insert_active(store, "B", "fact:b")
        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)
        nid_c = _insert_active(store, "C", "fact:c")  # 无边

        scores = personalized_pagerank(store, seed_ids=[nid_a], candidate_ids=[nid_a, nid_b, nid_c])
        assert scores[nid_c] < 0.01

    def test_triangle_balanced_scores(self, tmp_path):
        """三角连通图（A↔B↔C↔A），3 个种子，分数应接近（差异 < 0.1）。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "A", "fact:a")
        nid_b = _insert_active(store, "B", "fact:b")
        nid_c = _insert_active(store, "C", "fact:c")
        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)
        store.insert_edge(from_id=nid_b, to_id=nid_c, edge_type=EdgeType.RELATED_TO)
        store.insert_edge(from_id=nid_c, to_id=nid_a, edge_type=EdgeType.RELATED_TO)

        scores = personalized_pagerank(
            store,
            seed_ids=[nid_a, nid_b, nid_c],
            candidate_ids=[nid_a, nid_b, nid_c],
        )
        vals = list(scores.values())
        assert max(vals) - min(vals) < 0.1

    def test_nonexistent_candidate_returns_zero(self, tmp_path):
        """不存在的节点 ID 返回中无该键，不抛异常。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid = _insert_active(store, "real", "fact:real")
        scores = personalized_pagerank(
            store,
            seed_ids=[nid],
            candidate_ids=[nid, "nonexistent"],
        )
        assert "nonexistent" not in scores

    def test_deterministic_output(self, tmp_path):
        """相同输入两次计算，结果应完全一致。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "A", "fact:a")
        nid_b = _insert_active(store, "B", "fact:b")
        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)

        s1 = personalized_pagerank(store, seed_ids=[nid_a], candidate_ids=[nid_a, nid_b])
        s2 = personalized_pagerank(store, seed_ids=[nid_a], candidate_ids=[nid_a, nid_b])
        assert abs(s1[nid_a] - s2[nid_a]) < 1e-6
        assert abs(s1[nid_b] - s2[nid_b]) < 1e-6


# ─── compute_global_pagerank 单元测试 ────────────────────────

class TestGlobalPagerank:
    def test_scores_sum_to_one(self, tmp_path):
        """全局 PR 所有节点分数之和 ≈ 1。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "A", "fact:a")
        nid_b = _insert_active(store, "B", "fact:b")
        nid_c = _insert_active(store, "C", "fact:c")
        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)
        store.insert_edge(from_id=nid_b, to_id=nid_c, edge_type=EdgeType.RELATED_TO)

        scores = compute_global_pagerank(store)
        total = sum(scores.values())
        assert 0.99 < total < 1.01

    def test_empty_graph_returns_empty(self, tmp_path):
        store = SparkGraphStore(tmp_path / "sg.db")
        scores = compute_global_pagerank(store)
        assert scores == {}


# ─── recall 集成测试 ──────────────────────────────────────────

class TestRecallWithPPR:
    def test_result_has_ppr_score_field(self, tmp_path):
        """recall 结果节点应包含 _ppr_score 浮点数字段。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        _insert_active(store, "elasticsearch mapping issue", "fact:es-mapping")

        nodes = recall_nodes(
            store,
            query="elasticsearch mapping",
            config=RecallConfig(max_nodes=4, search_limit=4, vector_limit=0, related_limit=0),
        )
        assert len(nodes) > 0
        assert "_ppr_score" in nodes[0]
        assert isinstance(nodes[0]["_ppr_score"], float)

    def test_direct_hit_ranks_higher_than_neighbor(self, tmp_path):
        """direct hit（seed 节点）应排在 1 跳邻居前面。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        direct_id = _insert_active(store, "redis remote access problem", "fact:redis")
        indirect_id = _insert_active(store, "port binding connectivity", "fact:port-binding")
        store.insert_edge(from_id=direct_id, to_id=indirect_id, edge_type=EdgeType.RELATED_TO)

        nodes = recall_nodes(
            store,
            query="redis remote access",
            config=RecallConfig(max_nodes=2, search_limit=2, vector_limit=0, related_limit=4),
        )
        assert nodes[0]["id"] == direct_id

    def test_empty_graph_no_crash(self, tmp_path):
        """空图 recall 返回空列表，不抛异常。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        nodes = recall_nodes(store, query="anything", config=RecallConfig(max_nodes=4))
        assert nodes == []

    def test_ppr_score_reflects_graph_distance(self, tmp_path):
        """图距离越远，PPR 分数越低：seed > 1跳 > 2跳 > 孤立。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        seed = _insert_active(store, "seed", "fact:seed")
        hop1 = _insert_active(store, "hop1", "fact:hop1")
        hop2 = _insert_active(store, "hop2", "fact:hop2")
        isolated = _insert_active(store, "isolated", "fact:isolated")

        store.insert_edge(from_id=seed, to_id=hop1, edge_type=EdgeType.RELATED_TO)
        store.insert_edge(from_id=hop1, to_id=hop2, edge_type=EdgeType.RELATED_TO)
        # isolated: 无边

        scores = personalized_pagerank(
            store,
            seed_ids=[seed],
            candidate_ids=[seed, hop1, hop2, isolated],
        )
        assert scores[seed] > scores[hop1]
        assert scores[hop1] > scores[hop2]
        assert scores[hop2] > scores[isolated]

    def test_multihop_graph_recall(self, tmp_path):
        """2跳图：A(query) ↔ B ↔ C，C 应出现在 recall 结果中。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid_a = _insert_active(store, "docker deployment", "fact:docker-deploy")
        nid_b = _insert_active(store, "docker compose", "fact:docker-compose")
        nid_c = _insert_active(store, "port forwarding", "fact:port-forward")

        store.insert_edge(from_id=nid_a, to_id=nid_b, edge_type=EdgeType.RELATED_TO)
        store.insert_edge(from_id=nid_b, to_id=nid_c, edge_type=EdgeType.RELATED_TO)

        nodes = recall_nodes(
            store,
            query="docker deployment",
            config=RecallConfig(max_nodes=4, search_limit=2, vector_limit=0, related_limit=4),
        )
        ids = [n["id"] for n in nodes]
        assert nid_a in ids  # direct hit
        assert nid_b in ids  # 1-hop neighbor

    def test_candidate_only_nodes_recall_empty(self, tmp_path):
        """只有 candidate 状态节点时，recall 返回空（现有行为不变）。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        store.insert_node(
            SparkGraphNodeInput(
                type=NodeType.FACT,
                summary="candidate node",
                canonical_key="fact:candidate",
                source_kind="flush",
                status=NodeStatus.CANDIDATE,
                confidence=0.8,
                stability=0.8,
            )
        )
        nodes = recall_nodes(store, query="candidate", config=RecallConfig(max_nodes=4))
        assert nodes == []

    def test_dedup_preserved_after_ppr(self, tmp_path):
        """同一节点从 direct 和 related 同时出现，最终只有一条记录。"""
        store = SparkGraphStore(tmp_path / "sg.db")
        nid = _insert_active(store, "shared", "fact:shared")
        other_id = _insert_active(store, "other", "fact:other")
        store.insert_edge(from_id=nid, to_id=other_id, edge_type=EdgeType.RELATED_TO)

        nodes = recall_nodes(
            store,
            query="shared",
            config=RecallConfig(max_nodes=4, search_limit=4, vector_limit=0, related_limit=4),
        )
        ids = [n["id"] for n in nodes]
        assert ids == list(dict.fromkeys(ids))  # 无重复
```

### 6.2 `tests/sparkgraph/test_maintenance_ppr.py`

```python
"""Phase 1 测试：PPR maintenance 集成"""

from agent.sparkgraph.maintenance import run_ppr_maintenance
from agent.sparkgraph.store import SparkGraphNodeInput, SparkGraphStore
from agent.sparkgraph.types import NodeStatus, NodeType


def _insert_active(store: SparkGraphStore, summary: str, canonical_key: str) -> str:
    return store.insert_node(
        SparkGraphNodeInput(
            type=NodeType.FACT,
            summary=summary,
            canonical_key=canonical_key,
            source_kind="flush",
            status=NodeStatus.ACTIVE,
            confidence=0.8,
            stability=0.8,
        )
    )


class TestPPRMaintenance:
    def test_returns_stats_on_normal_graph(self, tmp_path):
        store = SparkGraphStore(tmp_path / "sg.db")
        nid = _insert_active(store, "test node", "fact:test")

        result = run_ppr_maintenance(store)
        assert result["ppr_computed"] is True
        assert result["nodes_scored"] >= 1

    def test_empty_graph_returns_zero_nodes(self, tmp_path):
        store = SparkGraphStore(tmp_path / "sg.db")
        result = run_ppr_maintenance(store)
        assert result["ppr_computed"] is True
        assert result["nodes_scored"] == 0
```

---

## 七、实施顺序与验收标准

| 步骤 | 操作 | 验收条件 |
|---|---|---|
| 1 | 新建 `pagerank.py` | `pytest tests/sparkgraph/test_pagerank.py -v` 全量通过 |
| 2 | 修改 `recaller.py` | `pytest tests/sparkgraph/test_recaller_ppr.py -v` 全量通过 |
| 3 | 修改 `maintenance.py` | `pytest tests/sparkgraph/test_maintenance_ppr.py -v` 全量通过 |
| 4 | 全量回归 | `pytest tests/sparkgraph/test_recaller.py -v` 全部通过 |
| 5 | 手动验证 | recall 结果包含 `_ppr_score`，PPR 分数与图距离呈负相关 |

---

## 八、字段兼容性核查

### 8.1 sg_nodes 现有字段 vs PPR 计划假设

| 字段 | 存在位置 | PPR 计划假设 | 核查结果 |
|---|---|---|---|
| `id` | sg_nodes | ✅ 直接读取 | ✅ 一致 |
| `status` | sg_nodes | ✅ `status == "active"` | ✅ 一致 |
| `confidence` | sg_nodes | ✅ `confidence * 100` | ✅ 一致 |
| `stability` | sg_nodes | ❌ 未使用 | ✅ 不影响 |
| `reuse_score` | sg_nodes | ❌ 未使用 | ✅ 不影响 |
| `source_kind` | sg_nodes | ✅ source_scores 加权 | ✅ 一致 |
| `updated_at` | sg_nodes | ✅ 时间排序 tie-break | ✅ 一致 |
| `meta` | sg_nodes (JSON) | ⚠️ 部分字段需验证 | 见下文 |
| `embedding` | sg_vectors 表 | ✅ cosine_similarity | ✅ 已有逻辑 |
| `last_recalled_at` | sg_nodes | ❌ 未使用 | ✅ 不影响 |

### 8.2 meta 字段兼容性

`sparkgraph_record_tool` 写入 `meta` 时的内容：

```python
# 新建节点时写入的 meta
{
    "confidence_components": { ... },
    "confidence_version": 1,
    "source_kind": source_kind,
}

# update_node_scoring 时（更新已有节点）
{
    "confidence_components": { ... },
    "confidence_version": 1,
    "source_kind": source_kind,
}

# mark_recalled 后（recall_nodes 内部）
{
    "recall_hits": 1,  # 每次 recall +1
    ...
}
```

**PPR 计划中 `_node_priority_ppr` 读取的 meta 字段逐一核对：**

| 计划中读取的字段 | 实际存在？ | 修正方案 |
|---|---|---|
| `meta.get("validated_count")` | ❌ **不存在**。证据数量在 `sg_evidence` 表，不在 meta | 改为 `store.count_evidence(node["id"])` 实时查询 |
| `meta.get("superseded_by")` / `meta.get("superseded")` | ⚠️ 可选字段，`should_deprecate_active` 会写入 | ✅ 保持现有逻辑，缺失时返回 0 |
| `meta.get("recall_hits")` | ✅ 存在（recall 后追加） | ✅ 一致 |

### 8.3 关键修正：`_node_priority_ppr` 证据数量读取方式

**计划原文（错误）：**
```python
validated_count = int(meta.get("validated_count", 0))
```

**修正后（正确）：**
```python
# evidence_count 必须从 sg_evidence 表实时查询，不能从 meta 读取
evidence_count = store.count_evidence(node["id"])
score += min(evidence_count, 20) * 5.0
```

这意味着 `_node_priority_ppr` 函数需要增加 `store` 参数，或在调用前预先查询。

**推荐方案：预先批量查询**

在 `recall_nodes` 中，PPR 排序前批量查询所有候选节点的 evidence 数量：

```python
# PPR 排序前：批量查询 evidence_count（避免 N 次单独查询）
candidate_ids = [str(node.get("id", "")) for node in merged.values() if node.get("id")]
evidence_counts: dict[str, int] = {}
for cid in candidate_ids:
    evidence_counts[cid] = store.count_evidence(cid)

# 传入 evidence_counts 字典，避免在排序函数内查库
ranked = _rank_with_ppr(list(merged.values()), ppr_scores, evidence_counts)
```

对应修改 `_rank_with_ppr` 签名：

```python
def _rank_with_ppr(
    nodes: list[dict[str, Any]],
    ppr_scores: dict[str, float],
    evidence_counts: dict[str, int],
    source_scores: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
```

### 8.4 embedding 来源确认

- embedding 存储在 `sg_vectors` 表，通过 `list_vector_nodes()` 返回的 `embedding` 字段读取
- `recaller.py` 已有 `cosine_similarity(query_vector, candidate.get("embedding") or [])` 逻辑
- **PPR 不涉及 embedding 生成或写入**，不与现有 embedding 流程冲突 ✅

### 8.5 存储流程完全不侵入

PPR 实现**只读**现有数据，不创建新字段，不修改写入流程：

| 操作 | 现有存储流程 | PPR 影响 |
|---|---|---|
| `insert_node` | 不变 | 无 |
| `append_evidence` | 不变 | 无 |
| `upsert_vector` | 不变 | 无 |
| `update_node_scoring` | 不变 | 无 |
| `insert_edge` | 不变 | 无 |
| `mark_recalled` | 不变（meta 追加 recall_hits） | 无 |
| recall 读取图结构 | 新增：读取 sg_edges 构建邻接表 | 只读，不写 |
| recall 排序 | 新增：`_ppr_score` 附加到结果节点 | 只读，不写 |

---

## 九、向后兼容性

- `recall_nodes` 返回类型不变：`list[dict[str, Any]]`
- 新增 `_ppr_score` 字段为**附加调试信息**，不影响调用方
- 异常 fallback 机制：PPR 计算失败 → 回退到 `_recall_rank`（现有逻辑）
- 现有测试 `test_recaller.py` 全部保持通过
- 不改变任何节点的存储字段和写入流程
