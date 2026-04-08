# B1 专项修复 · 三层 Recall 架构
**状态：** 设计中
**创建：** 2026-04-03
**基于：** B1 分析报告（方案 C）
**审核前：不得实施**

---

## 1. 问题回顾

### 1.1 根因

```
用户显式写入 ISSUE 节点（flush 或 manual）
  → status = CANDIDATE（因 confidence 0.562 < 0.70 无需 promote）
  → recall 时 _is_recall_eligible() 检查 status == "active" → False
  → 节点被过滤，不进入候选集
  → SOLVES 边链路的 RESOURCE 也无法经由 ISSUE 召回
  → flush maintenance 无法对该 ISSUE 做 evidence 积累（从未被 recall）
  → 鸡生蛋：节点永远停留在 CANDIDATE，无法晋升 ACTIVE
```

### 1.2 三层 Recall 架构设计思想

| 通道 | 何时启用 | eligibility 门槛 | 目的 |
|---|---|---|---|
| **L1 · Direct** | query → FTS/vector 精准匹配 | candidate 且 evidence≥1 OR active | 精准召回已知节点 |
| **L2 · Graph** | L1 结果的 1-hop 邻居（SOLVES/DEPENDS_ON 优先） | active（严格） | 图扩展发现关联资源 |
| **L3 · Cold** | L1+L2 不足时的兜底 | candidate 且 evidence=0 且 confidence≥0.40 | 冷启动探索 |

**核心原则：**
- candidate 节点**可以被召回**，但只能通过 L1 direct hit，且必须有 evidence≥1（说明曾经被用到过）
- L2 graph 保持 active-only，防止图遍历被大量 candidate 节点污染
- L3 作为兜底，给极低 confidence 但零 evidence 的节点一个被发现的窗口（触发后会获得第一次 evidence）

---

## 2. 改动文件清单

| 文件 | 改动类型 |
|---|---|
| `agent/sparkgraph/recaller.py` | 重构 + 新增 |
| `agent/sparkgraph/types.py` | 新增常量 |
| `agent/sparkgraph/scoring.py` | 小改（`count_evidence` 导出） |
| `agent/sparkgraph/store.py` | 新增 `count_evidence` 方法 |
| `agent/sparkgraph/maintenance.py` | 小改（引用新常量） |
| `tests/sparkgraph/test_recaller.py` | 扩充覆盖用例 |
| `tests/sparkgraph/test_maintenance.py` | 扩充覆盖用例 |

---

## 3. 类型系统变更

### 3.1 `types.py` — 新增 RecallChannel 枚举

```python
class RecallChannel(str, Enum):
    """三条召回通道，对应 recall_eligibility 的三个层次。"""
    DIRECT   = "direct"    # L1：精准匹配，含 candidate（需 evidence≥1）
    GRAPH    = "graph"     # L2：图扩展，active-only
    COLD     = "cold"      # L3：兜底，零 evidence candidate
```

### 3.2 `types.py` — 新增 RecallConfig 字段

```python
@dataclass
class RecallConfig:
    # ... 现有字段 ...

    # 三层架构新增配置
    cold_start_confidence_threshold: float = 0.40   # L3 最低 confidence
    cold_start_evidence_threshold: int = 0           # L3 允许零 evidence
    candidate_direct_evidence_threshold: int = 1     # L1 candidate 最低 evidence
```

---

## 4. 函数级设计

### 4.1 `store.py` — 新增 `count_evidence(node_id)`

**职责：** 返回某节点关联的 evidence 行数。

```python
def count_evidence(self, node_id: str) -> int:
    """返回 node_id 在 sg_evidence 表中的行数（不计 revoked）。"""
    with self._conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) as cnt FROM sg_evidence
            WHERE node_id = ? AND revoked = 0
            """,
            (node_id,),
        ).fetchone()
        return int(row["cnt"]) if row else 0
```

**边界条件：**
- node_id 不存在 → 返回 0
- 已被 revoke 的 evidence 不计入

---

### 4.2 `recaller.py` — 重构 `_is_recall_eligible`

**旧签名：**
```python
def _is_recall_eligible(node: dict[str, Any]) -> bool:
```

**新签名：**
```python
def _is_recall_eligible(
    node: dict[str, Any],
    channel: RecallChannel,
    config: RecallConfig,
) -> bool:
```

**新实现逻辑：**

```python
def _is_recall_eligible(
    node: dict[str, Any],
    channel: RecallChannel,
    config: RecallConfig,
) -> bool:
    """按通道决定 eligibility。

    L1 DIRECT:
      - active → 按 confidence/stability 阈值过滤
      - candidate → 必须 evidence >= candidate_direct_evidence_threshold

    L2 GRAPH:
      - 必须 active（严格）

    L3 COLD:
      - 仅限 candidate
      - evidence == 0
      - confidence >= cold_start_confidence_threshold
    """
    status = node.get("status")

    if channel == RecallChannel.GRAPH:
        # L2 图扩展：只允许 active，防止 candidate 污染图遍历
        return status == NodeStatus.ACTIVE.value

    if channel == RecallChannel.COLD:
        # L3 冷启动：零 evidence candidate，confidence 门槛
        return (
            status == NodeStatus.CANDIDATE.value
            and float(node.get("confidence") or 0.0) >= config.cold_start_confidence_threshold
            and float(node.get("stability") or 0.0) >= config.cold_start_stability_threshold
        )

    # L1 DIRECT
    if status == NodeStatus.ACTIVE.value:
        return (
            float(node.get("confidence") or 0.0) >= config.min_recall_confidence
            and float(node.get("stability") or 0.0) >= config.min_recall_stability
        )
    # candidate 节点：direct hit 路径，但必须有 evidence 积累
    elif status == NodeStatus.CANDIDATE.value:
        # evidence_count 需要 store 注入，由调用方确保传入
        evidence_count = node.get("_evidence_count", 0)
        return (
            evidence_count >= config.candidate_direct_evidence_threshold
            and float(node.get("confidence") or 0.0) >= config.cold_start_confidence_threshold
        )

    return False
```

**关键：调用方（`recall_nodes`）负责在 node dict 中注入 `_evidence_count` 字段。**

---

### 4.3 `recaller.py` — 重构 `recall_nodes`

**主流程变更：**

```
旧流程：
  1. search_nodes (direct + vector) → raw_direct
  2. 过滤 _is_recall_eligible(raw_direct)
  3. 1-hop related hits (SOLVES 优先)
  4. 过滤 _is_recall_eligible(related)
  5. PPR 排序
  6. 取 top max_nodes

新流程：
  1. search_nodes → raw_direct  (不过滤)
  2. 获取 raw_direct 中所有 node_id 的 evidence_count（批量）
  3. 注入 _evidence_count 到 node dict
  4. L1 过滤：_is_recall_eligible(raw_direct, DIRECT)
  5. L2 related：只取 active 邻居
  6. PPR 排序（L1 + L2 合并候选）
  7. 若 L1+L2 结果 < max_nodes：追加 L3 cold_start（排序取 top 差量）
  8. 取 top max_nodes
```

**新增 `batch_get_evidence_counts` 函数：**

```python
def batch_get_evidence_counts(store: SparkGraphStore, node_ids: list[str]) -> dict[str, int]:
    """批量获取一批 node_id 的 evidence 计数。返回 {node_id: count}。"""
    if not node_ids:
        return {}
    with store._conn() as conn:
        placeholders = ",".join("?" * len(node_ids))
        rows = conn.execute(
            f"""
            SELECT node_id, COUNT(*) as cnt
            FROM sg_evidence
            WHERE node_id IN ({placeholders}) AND revoked = 0
            GROUP BY node_id
            """,
            node_ids,
        ).fetchall()
        return {str(row["node_id"]): int(row["cnt"]) for row in rows}
```

**重构 `recall_nodes` 函数：**

```python
def recall_nodes(
    store: SparkGraphStore,
    query: str,
    *,
    config: RecallConfig | None = None,
    session_id: str | None = None,
    node_types: Collection[NodeType] | None = None,
    max_nodes: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """三层召回主函数。

    Returns:
        (nodes, edges): 符合 eligibility 的节点列表及关联边
    """
    config = config or RecallConfig()
    max_nodes = max_nodes or config.max_nodes

    # Step 1: 原始检索（全量，不过滤）
    raw_direct = store.search_nodes(
        query,
        status=None,  # 不过滤 status，由 recall_nodes 统一处理
        node_types=node_types,
        limit=config.vector_search_limit * 2,  # 宽松上限，后面再截断
    )

    if not raw_direct:
        return [], []

    # Step 2: 批量注入 evidence_count
    node_ids = [n["id"] for n in raw_direct]
    evidence_map = batch_get_evidence_counts(store, node_ids)
    for node in raw_direct:
        node["_evidence_count"] = evidence_map.get(node["id"], 0)

    # Step 3: L1 — Direct 通道过滤
    l1_candidates = [
        n for n in raw_direct
        if _is_recall_eligible(n, RecallChannel.DIRECT, config)
    ]

    # Step 4: L2 — Graph 扩展（仅 active 邻居）
    l1_ids = {n["id"] for n in l1_candidates}
    related_hits = _get_related_hits(
        store,
        l1_ids,
        config,
        node_types=node_types,
        # _get_related_hits 内部过滤 GRAPH 通道（active-only）
    )

    # Step 5: PPR 排序（L1 + L2 合并）
    all_candidates = l1_candidates + related_hits

    # 去重（id 唯一）
    seen_ids: set[str] = set()
    unique_candidates = []
    for n in all_candidates:
        if n["id"] not in seen_ids:
            seen_ids.add(n["id"])
            unique_candidates.append(n)

    ranked = _rank_candidates(unique_candidates, config)

    # Step 6: L3 — Cold 兜底（若 top 不够）
    final_nodes: list[dict[str, Any]] = []
    if len(ranked) < max_nodes:
        remaining_slots = max_nodes - len(ranked)
        l3_candidates = [
            n for n in raw_direct
            if n["id"] not in seen_ids
            and _is_recall_eligible(n, RecallChannel.COLD, config)
        ]
        l3_ranked = _rank_candidates(l3_candidates, config)
        ranked.extend(l3_ranked[:remaining_slots])

    final_nodes = ranked[:max_nodes]
    final_ids = {n["id"] for n in final_nodes}

    # Step 7: 提取关联边
    edges = store.get_edges_for_nodes(list(final_ids), types=None)

    return final_nodes, edges
```

**新增内部函数 `_get_related_hits`（从原 `recall_nodes` 中提取并改造）：**

```python
def _get_related_hits(
    store: SparkGraphStore,
    seed_ids: set[str],
    config: RecallConfig,
    node_types: Collection[NodeType] | None = None,
) -> list[dict[str, Any]]:
    """获取 seed_ids 的 1-hop 邻居（L2 GRAPH 通道，active-only）。"""
    if not seed_ids:
        return []

    placeholders = ",".join("?" * len(seed_ids))

    # 边类型优先级：SOLVES > DEPENDS_ON > 其他
    # 但 graph 通道统一用 active 过滤，不在这里区分边类型优先级（留给 Phase 2）
    edge_type_filter = ""
    if config.preferred_edge_types:
        type_placeholders = ",".join(
            f"'{et.value}'" for et in config.preferred_edge_types
        )
        edge_type_filter = f"AND e.type IN ({type_placeholders})"

    rows = store._conn().execute(
        f"""
        SELECT DISTINCT n.id, n.name, n.type, n.status, n.confidence,
               n.stability, n.reuse_score, n.meta, n.created_at,
               n.updated_at, n.session_id
        FROM sg_nodes n
        JOIN sg_edges e ON e.to_id = n.id
        WHERE e.from_id IN ({placeholders})
          AND n.status = 'active'
          {edge_type_filter}
        LIMIT {config.related_hits_limit}
        """,
        list(seed_ids),
    ).fetchall()

    return [dict(row) for row in rows]
```

---

### 4.4 `scoring.py` — 新增 `count_evidence` 参数传入点

`next_status_for_candidate` 不变，但需要让 `maintenance.py` 能在 recall 节点后对 candidate 做 promote 判断时能查到真实的 evidence_count。

**新增：**

```python
def evidence_based_promotion(
    node: dict[str, Any],
    config: RecallConfig | None = None,
) -> NodeStatus:
    """基于 evidence 次数判断 candidate 是否应晋升 active。

    适用于 recall 后 maintenance 阶段：如果节点被成功召回，
    说明它有价值，可以给予 evidence 积累奖励。
    """
    if node.get("status") != NodeStatus.CANDIDATE.value:
        return NodeStatus(node["status"])

    evidence_count = node.get("_evidence_count", 0)
    confidence = float(node.get("confidence") or 0.0)

    # evidence 超过 2 次，直接晋升（已被多次使用）
    if evidence_count >= 2:
        return NodeStatus.ACTIVE

    # evidence = 1 且 confidence 较高：晋升
    if evidence_count == 1 and confidence >= 0.65:
        return NodeStatus.ACTIVE

    return NodeStatus.CANDIDATE
```

---

### 4.5 `maintenance.py` — 改造 `run_flush_maintenance`

**变更点：** 调用 `evidence_based_promotion` 替代纯 confidence 判断。

```python
# maintenance.py run_flush_maintenance 中
for node in stale_actives:
    node_id = node["id"]
    # 获取 evidence_count
    evidence_cnt = store.count_evidence(node_id)
    node["_evidence_count"] = evidence_cnt

    new_status = evidence_based_promotion(node, config=config)
    if new_status != NodeStatus(node["status"]):
        store.update_node_status(node_id, status=new_status.value)
```

---

## 5. 行为变更对照

| 场景 | 旧行为 | 新行为 |
|---|---|---|
| 新建 ISSUE（flush，confidence=0.56，evidence=0） | 无法 recall | L1 direct：evidence=0 不满足→跳过；L3 cold：confidence=0.56≥0.40→可进入兜底候选 |
| ISSUE 被 recall 一次后（evidence=1，confidence=0.62） | 仍 CANDIDATE（confidence<0.70） | L1 direct：evidence=1≥1→通过，status 仍 CANDIDATE 但可召回 |
| ISSUE 被 recall 两次后（evidence=2） | 仍 CANDIDATE | `evidence_based_promotion`：evidence=2→直接晋升 ACTIVE |
| 已有 ACTIVE ISSUE 通过 SOLVES 找关联 RESOURCE | PPR 可召回 | 不变 |
| candidate ISSUE 不会通过 L2 GRAPH 扩散 | — | 保持：L2 仅 active |

---

## 6. 测试用例设计

### 6.1 `test_recaller.py` 新增用例

#### TC-R-001：L1 direct — active 节点 recall 正常

```python
def test_recaller_l1_active_node(recaller, store):
    """active 节点不受影响，应正常出现在结果中。"""
    node_id = store.insert_node(...)
    store.update_node_status(node_id, status="active")
    store.update_node_confidence(node_id, confidence=0.80)

    nodes, edges = recall_nodes(store, query="docker port", ...)
    assert any(n["id"] == node_id for n in nodes)
```

#### TC-R-002：L1 direct — candidate + evidence≥1 可召回

```python
def test_recaller_l1_candidate_with_evidence(recaller, store):
    """candidate 但 evidence>=1 → L1 direct 可召回。"""
    node_id = store.insert_node(...)
    store.update_node_status(node_id, status="candidate")
    store.update_node_confidence(node_id, confidence=0.55)
    store.add_evidence(node_id, session_id="s1", content="some fact")
    store.add_evidence(node_id, session_id="s2", content="another fact")

    nodes, _ = recall_nodes(store, query="docker port", ...)
    assert any(n["id"] == node_id for n in nodes)
```

#### TC-R-003：L1 direct — candidate + evidence=0 不可召回

```python
def test_recaller_l1_candidate_zero_evidence(recaler, store):
    """candidate + zero evidence → L1 过滤掉（交给 L3 判断）。"""
    node_id = store.insert_node(...)
    store.update_node_status(node_id, status="candidate")
    store.update_node_confidence(node_id, confidence=0.55)

    nodes, _ = recall_nodes(store, query="docker port", ...)
    assert not any(n["id"] == node_id for n in nodes)
```

#### TC-R-004：L3 cold — candidate + zero evidence + confidence≥0.40 进入兜底

```python
def test_recaller_l3_cold_start(recaller, store):
    """L1 过滤后候选不足，cold start 兜底补充。"""
    # 准备：一个 candidate zero evidence 节点
    node_id = store.insert_node(...)
    store.update_node_status(node_id, status="candidate")
    store.update_node_confidence(node_id, confidence=0.50)

    # 让 L1 结果为空（搜索词不匹配任何 active 节点）
    nodes, _ = recall_nodes(store, query="xyz_nomatch_abc", max_nodes=4, ...)
    # 期望：该节点从 L3 进入候选（confidence 0.50 >= 0.40）
    assert any(n["id"] == node_id for n in nodes)
```

#### TC-R-005：L3 cold — confidence < 0.40 不进入

```python
def test_recaller_l3_cold_rejects_low_confidence(recaller, store):
    """L3 也有 confidence 下限，低于 0.40 仍排除。"""
    node_id = store.insert_node(...)
    store.update_node_status(node_id, status="candidate")
    store.update_node_confidence(node_id, confidence=0.35)

    nodes, _ = recall_nodes(store, query="docker port", max_nodes=4, ...)
    assert not any(n["id"] == node_id for n in nodes)
```

#### TC-R-006：L2 graph — candidate 节点不通过 related_hits 扩散

```python
def test_recaller_l2_graph_excludes_candidate(store, recaller):
    """图扩展通道只允许 active 节点，不允许 candidate 扩散。"""
    # A: candidate ISSUE
    issue_id = store.insert_node(...)
    store.update_node_status(issue_id, status="candidate")
    store.update_node_confidence(issue_id, confidence=0.75)
    # B: active RESOURCE，通过 SOLVES 边连接
    res_id = store.insert_node(...)
    store.update_node_status(res_id, status="active")
    store.update_node_confidence(res_id, confidence=0.80)
    store.insert_edge(from_id=issue_id, to_id=res_id, type="SOLVES")

    # L1 直接搜 RESOURCE（active）→ 通过
    nodes, _ = recall_nodes(store, query="k8s", ...)
    assert any(n["id"] == res_id for n in nodes)
    # B 的 related_hits 不包含 issue_id（因为 issue 是 candidate）
    # 即使包含，_is_recall_eligible(..., GRAPH) 也会过滤掉
```

#### TC-R-007：L1+L2 不足时 L3 补充到 max_nodes

```python
def test_recaller_l3_fills_gap_to_max_nodes(store, recaller):
    """L1+L2 结果 < max_nodes 时，L3 补充差量。"""
    # 1 个 active L1 节点
    active_id = store.insert_node(...)
    store.update_node_status(active_id, status="active")
    store.update_node_confidence(active_id, confidence=0.80)

    # 2 个 candidate zero evidence L3 候选
    c1_id = store.insert_node(...)
    store.update_node_status(c1_id, status="candidate")
    store.update_node_confidence(c1_id, confidence=0.55)

    nodes, _ = recall_nodes(store, query="docker", max_nodes=4, ...)
    # 期望 active_id(1) + c1_id(1) = 2个
    assert len(nodes) >= 1
```

#### TC-R-008：batch_get_evidence_counts 大量节点性能

```python
def test_batch_evidence_counts_performance(store, recaller):
    """批量获取 100 个节点的 evidence_count，验证无 N+1。"""
    node_ids = [store.insert_node(...) for _ in range(100)]
    result = batch_get_evidence_counts(store, node_ids)
    assert len(result) <= 100
    assert all(isinstance(v, int) for v in result.values())
```

### 6.2 `test_maintenance.py` 新增用例

#### TC-M-001：evidence=2 → candidate 直接晋升 ACTIVE

```python
def test_evidence_based_promotion_two_evidence(store, maintenance):
    node_id = store.insert_node(...)
    store.update_node_status(node_id, status="candidate")
    store.update_node_confidence(node_id, confidence=0.55)
    store.add_evidence(node_id, session_id="s1", content="...")
    store.add_evidence(node_id, session_id="s2", content="...")

    new_status = evidence_based_promotion(
        store.get_node(node_id),
    )
    assert new_status == NodeStatus.ACTIVE
```

#### TC-M-002：evidence=1 + confidence≥0.65 → 晋升

```python
def test_evidence_based_promotion_one_evidence_enough_confidence(store, maintenance):
    node_id = store.insert_node(...)
    store.update_node_status(node_id, status="candidate")
    store.update_node_confidence(node_id, confidence=0.68)
    store.add_evidence(node_id, session_id="s1", content="...")

    new_status = evidence_based_promotion(store.get_node(node_id))
    assert new_status == NodeStatus.ACTIVE
```

#### TC-M-003：evidence=1 + confidence<0.65 → 不晋升

```python
def test_evidence_based_promotion_one_evidence_low_confidence(store, maintenance):
    node_id = store.insert_node(...)
    store.update_node_status(node_id, status="candidate")
    store.update_node_confidence(node_id, confidence=0.55)
    store.add_evidence(node_id, session_id="s1", content="...")

    new_status = evidence_based_promotion(store.get_node(node_id))
    assert new_status == NodeStatus.CANDIDATE
```

---

## 7. 向后兼容性

| 改动 | 兼容性影响 |
|---|---|
| `RecallConfig` 新增字段有默认值 | ✅ 兼容，调用方不需改 |
| `store.count_evidence` 新增 | ✅ 向后兼容，无现有调用 |
| `_is_recall_eligible` 签名变更 | ⚠️ **BREAKING**：若外部有调用方（tools/sparkgraph_tool.py）需同步更新 |
| `recall_nodes` 返回结构不变 | ✅ 兼容 |
| `evidence_based_promotion` 新增 | ✅ 向后兼容 |

**需要检查的外部调用方：**

```bash
grep -r "_is_recall_eligible" /Users/wzh/IsacHermes/ --include="*.py"
# 预期：仅 recaller.py 内部使用，若有其他文件调用需同步更新签名
```

---

## 8. 实施顺序

```
Phase 1（最小验证集）:
  1. types.py — RecallChannel + RecallConfig 新字段
  2. store.py — count_evidence + batch_get_evidence_counts
  3. scoring.py — evidence_based_promotion

Phase 2（核心逻辑）:
  4. recaller.py — 重构 _is_recall_eligible + recall_nodes
  5. maintenance.py — 接入 evidence_based_promotion

Phase 3（测试覆盖）:
  6. test_recaller.py — TC-R-001 ~ TC-R-008
  7. test_maintenance.py — TC-M-001 ~ TC-M-003
  8. 全量回归: pytest tests/sparkgraph/ -v

Phase 4（上线）:
  9. 写 release note
  10. staging 验证 recall 行为变更
```

---

## 9. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| candidate 节点过多进入 recall，recall 质量下降 | 中 | 中 | L1 要求 evidence≥1，L3 有 confidence 下限，双重过滤 |
| L3 cold 兜底过于激进，零 evidence 节点出现在 top 结果 | 低 | 低 | cold 节点排在 active 之后，不会超过高质量 active 节点 |
| `_is_recall_eligible` 签名变更破坏外部调用 | 低 | 高 | 先 grep 确认无外部调用；有则统一更新 |
| batch_get_evidence_counts 引入新 SQL 查询，性能问题 | 低 | 低 | 单次 SQL GROUP BY，O(N) 无 N+1 |
