# SparkGraph 简化重构设计文档
**状态**：实施中
**目标**：参照 graph-memory，移除 CANDIDATE 中间态，删除 sg_evidence 表，以 validated_count 单整数驱动

---

## 一、核心设计变更

### 1.1 状态机（从3状态→2状态）

| 状态 | 含义 | 可召回 |
|---|---|---|
| `active` | 有效节点 | ✅ |
| `deprecated` | 已废弃 | ❌ |

**来源即命运**（写时直接定状态）：

| source_kind | 初始状态 | confidence |
|---|---|---|
| manual / explicit / review | active | 0.90 / 0.88 / 0.92 |
| flush / auto | active | 0.72 |
| reflection / shadow | deprecated | 0.50 |

> 不再存在 CANDIDATE → ACTIVE 的晋升路径。新节点写入时直接决定状态。

### 1.2 排序机制（取代 evidence 驱动晋升）

```
recall 排序分 = PPR×1000
              + sourceKind补偿（explicit+80, manual+40, review+40, flush+0, auto+0）
              + validatedCount×5（封顶20次，即+100）
              + confidence×100
              - superseded?500

validatedCount = 被成功召回并使用的次数（每次 recall_nodes 命中即+1）
```

> graph-memory 经验：validatedCount 才是驱动"有用节点上浮"的核心机制，而不是先晋升再召回。

### 1.3 数据模型变更

**sg_nodes 表变更：**

```sql
ALTER TABLE sg_nodes ADD COLUMN validated_count INTEGER DEFAULT 0;
ALTER TABLE sg_nodes ADD COLUMN recall_hits      INTEGER DEFAULT 0;  -- 已有meta.recall_hits，冗余写方便查询
```

**删除 sg_evidence 表（所有 evidence 相关逻辑）：**
- 删除 `sg_evidence` 表
- 删除 `store.append_evidence()`
- 删除 `store.count_evidence()`
- 删除 `scoring.support_score()` 的 evidence 参数
- 删除 `scoring.compute_scores()` 的 evidence_count 参数

### 1.4 写入流程（简化后）

```
flush_memories / sparkgraph_record
  ↓
  dedup 检查（canonical_key）
  ↓
  source_kind → initial_status（来源即命运）
  confidence = SOURCE_SCORES[source_kind]（简单查表，无公式）
  ↓
  store.insert_node(status=active, confidence=...)
  validated_count = 0
```

### 1.5 召回流程（简化后）

```
recall_nodes(query)
  ↓
  Step 1: 精确通道（vector + FTS 合并去重）
  ↓
  Step 2: 图扩展通道（1-hop active 邻居，最多4个）
  ↓
  Step 3: 合并 + 按 recall_score 排序
  ↓
  Step 4: 命中的节点 validated_count++ + recall_hits++
  ↓
  返回 top max_nodes
```

---

## 二、文件级改动清单

### 2.1 `agent/sparkgraph/types.py`

```python
# 删除 RecallChannel 枚举
# 删除 CANDIDATE 状态
class NodeStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
```

### 2.2 `agent/sparkgraph/db.py`

```python
# NODES_TABLE DDL 新增列
VALIDATED_COUNT_COL = "validated_count"

# 删除 SOURCE_KINDS 中的 "recall"
# 删除 EVIDENCE_TABLE 相关常量
```

### 2.3 `agent/sparkgraph/scoring.py`

**完全重写，删除以下：**
- `support_score()`（evidence 相关）
- `compute_scores()`（6分量公式）
- `should_promote_candidate()`
- `next_status_for_candidate()`
- `evidence_based_promotion()`
- `ScoreBreakdown`（原复杂版）
- `ACTIVE_CONFIDENCE_THRESHOLD`、`ACTIVE_STABILITY_THRESHOLD` 等晋升相关常量

**新增：**

```python
@dataclass(frozen=True)
class ScoreResult:
    confidence: float        # 直接 = source_score（查表）
    initial_status: NodeStatus  # 直接由 source_kind 决定

# 简单查表，0公式
SOURCE_CONFIDENCE = {
    "manual":    0.90,
    "explicit":  0.88,
    "review":   0.92,
    "flush":    0.72,
    "auto":     0.72,
    "reflection": 0.50,
    "shadow":   0.50,
}

# deprecated 来源
DEPRECATED_SOURCES = {"reflection", "shadow"}

def compute_initial_status_and_confidence(source_kind: str) -> ScoreResult:
    """来源即命运：无公式，直接查表。"""
    if source_kind in DEPRECATED_SOURCES:
        return ScoreResult(confidence=SOURCE_CONFIDENCE[source_kind], initial_status=NodeStatus.DEPRECATED)
    return ScoreResult(
        confidence=SOURCE_CONFIDENCE.get(source_kind, 0.72),
        initial_status=NodeStatus.ACTIVE,
    )

# recall_score 计算（供 recall_nodes 排序用）
def recall_priority_score(
    *,
    ppr_score: float,
    validated_count: int,
    confidence: float,
    source_kind: str,
    superseded: bool,
) -> float:
    """graph-memory 风格排序分。"""
    score = ppr_score * 1000.0
    SOURCE_BONUS = {"explicit": 80, "manual": 40, "review": 40, "flush": 0, "auto": 0, "reflection": 0, "shadow": 0}
    score += SOURCE_BONUS.get(source_kind, 0)
    score += min(validated_count, 20) * 5.0
    score += confidence * 100.0
    if superseded:
        score -= 500.0
    return score

# should_deprecate_active（保留，逻辑不变）
STALE_RECALL_DAYS = 30
DEPRECATE_STABILITY_THRESHOLD = 0.45
DEPRECATE_SUPPORT_THRESHOLD = 0.35
```

### 2.4 `agent/sparkgraph/recaller.py`

**删除：**
- `RecallChannel` 导入
- `MIN_RECALL_CONFIDENCE_ACTIVE`、`CANDIDATE_DIRECT_EVIDENCE_THRESHOLD`、`COLD_START_*` 等召回阈值
- `batch_get_evidence_counts()`
- `_is_recall_eligible()`
- `COLD` 通道相关逻辑
- `evidence_count` 相关逻辑

**重构 `recall_nodes`：**

```python
RecallConfig = namedtuple("RecallConfig", ["search_limit", "related_limit", "max_nodes", "vector_limit"])
RecallConfig.__new__.__defaults__ = (8, 4, 4, 24)

def recall_nodes(store, query, config=None, embedding_config=None):
    """两条通道：精确（vector+FTS）+ 图扩展（1-hop active）"""
    config = config or RecallConfig()
    if not query or _is_low_signal_query(query):
        return [], []

    # Step 1: 精确通道
    fts_hits = store.search_nodes(query, status=NodeStatus.ACTIVE.value, limit=config.search_limit)
    vector_hits = _vector_search(query, config, embedding_config) if embedding_enabled(embedding_config) else []

    # Step 2: 合并去重
    merged = _merge_hits(fts_hits, vector_hits)

    # Step 3: 图扩展（以精确通道 top2 为种子）
    seed_ids = [n["id"] for n in merged[:2] if n.get("id")]
    graph_hits = store.get_related_nodes(seed_ids, active_only=True, limit=config.related_limit) if seed_ids else []

    # Step 4: 合并所有通道
    all_nodes = _dedup_merge(list(merged.values()) + graph_hits)

    # Step 5: 排序
    ranked = _rank_nodes(all_nodes, use_ppr=bool(graph_hits), seed_ids=seed_ids, store=store)

    final = ranked[:config.max_nodes]
    final_ids = [str(n["id"]) for n in final if n.get("id")]

    # Step 6: 命中计数
    if final_ids:
        store.increment_validated_count(final_ids)

    # Step 7: 取边
    edges = store.get_edges_for_nodes(final_ids) if final_ids else []
    return final, edges

def _rank_nodes(nodes, *, use_ppr, seed_ids, store):
    if use_ppr and seed_ids and store:
        ppr_scores = personalized_pagerank(store, seed_ids=seed_ids,
                                           candidate_ids=[n["id"] for n in nodes],
                                           damping=0.85, iterations=30)
    else:
        ppr_scores = {}

    scored = []
    for node in nodes:
        nid = str(node.get("id") or "")
        ppr = ppr_scores.get(nid, 0.0)
        priority = recall_priority_score(
            ppr_score=ppr,
            validated_count=node.get("validated_count", 0),
            confidence=float(node.get("confidence", 0.0)),
            source_kind=str(node.get("source_kind", "")),
            superseded=bool(node.get("meta", {}).get("superseded_by")),
        )
        scored.append((priority, float(node.get("confidence", 0)), node))
    scored.sort(key=lambda x: x[:2], reverse=True)
    return [s[2] for s in scored]
```

### 2.5 `agent/sparkgraph/store.py`

**删除：**
- `append_evidence()`
- `count_evidence()`

**新增：**

```python
def increment_validated_count(self, node_ids: list[str], *, now_ts: int | None = None) -> None:
    """命中的节点 validated_count++ + recall_hits++（meta）"""
    if not node_ids:
        return
    stamp = int(now_ts or time.time())
    for node_id in node_ids:
        current = self.get_node(node_id)
        if not current:
            continue
        meta = json.loads(current.get("meta") or "{}")
        meta["recall_hits"] = int(meta.get("recall_hits", 0)) + 1
        self._conn.execute(
            f"""UPDATE {NODES_TABLE} SET validated_count = validated_count + 1,
                last_recalled_at = ?, meta = ?, updated_at = updated_at WHERE id = ?""",
            (stamp, json.dumps(meta, sort_keys=True), node_id),
        )
    self._conn.commit()

def get_validated_count(self, node_id: str) -> int:
    """直接读 validated_count 字段（不再查 evidence 表）"""
    row = self._conn.execute(
        f"SELECT validated_count FROM {NODES_TABLE} WHERE id = ?", (node_id,)
    ).fetchone()
    return int(row["validated_count"]) if row else 0
```

**修改 `insert_node` 签名：**
```python
def insert_node(self, item: SparkGraphNodeInput, validated_count: int = 0) -> str:
    # 新增 validated_count 参数，默认0
```

### 2.6 `agent/sparkgraph/maintenance.py`

**删除：**
- `evidence_based_promotion` 导入和调用
- `evidence_count` 相关逻辑
- `support_score()` 调用

**修改 `run_flush_maintenance`：**

```python
def run_flush_maintenance(store, *, now_ts=None, embedding_config=None, vector_backfill_limit=8):
    now_ts = int(now_ts or time.time())
    stale_cutoff = now_ts - (STALE_CANDIDATE_DAYS * 24 * 60 * 60)

    # deprecated 逻辑：30天无recall + 低validated_count + 低stability
    for node in store.list_nodes(status=NodeStatus.ACTIVE.value):
        last_recall = int(node.get("last_recalled_at") or 0)
        reference_ts = last_recall or int(node.get("updated_at") or now_ts)
        days_idle = max(0, (now_ts - reference_ts) // (86400))
        validated = node.get("validated_count", 0)
        stability = float(node.get("stability", 0))
        if (days_idle >= STALE_RECALL_DAYS
                and validated <= 1
                and stability < DEPRECATE_STABILITY_THRESHOLD):
            store.update_node_status(node["id"], status=NodeStatus.DEPRECATED.value)
```

> 注意：`STALE_CANDIDATE_DAYS` 变量名保留但语义变为"ACTIVE 节点idle超30天"

### 2.7 `tools/sparkgraph_tool.py`

**删除：**
- `compute_scores` 调用（6分量公式）
- `next_status_for_candidate` 调用
- `store.append_evidence()` 调用
- 所有 `evidence_count` 参数传递

**修改写入路径：**

```python
# 写入节点
scores = compute_initial_status_and_confidence(source_kind=source_kind)
store.insert_node(
    SparkGraphNodeInput(...),
    validated_count=0,  # 新建节点validated_count=0
)
# 不再 append_evidence
```

**修改去重更新路径：**

```python
# 去重命中：只更新 confidence（用新的 source_kind 重新查表），不做 evidence 累积
scores = compute_initial_status_and_confidence(source_kind=source_kind)
store.update_node_scoring(
    node_id=existing["id"],
    confidence=scores.confidence,  # 直接用新的 source_score
    stability=existing["stability"],
    reuse_score=existing["reuse_score"],
    status=scores.initial_status,  # 可能从 deprecated 变 active（如 explicit 更新了 reflection）
)
```

---

## 三、测试用例设计（11个）

### 3.1 单元测试：`tests/sparkgraph/test_scoring.py`（新建）

| 用例 | 描述 | 断言 |
|---|---|---|
| TC-S-01 | manual/explicit/review → active, confidence查表 | status=active, confidence准确 |
| TC-S-02 | reflection/shadow → deprecated | status=deprecated, confidence=0.50 |
| TC-S-03 | recall_priority_score 公式 | PPR+validatedCount+confidence+sourceBonus正确叠加 |
| TC-S-04 | superseded节点排序分-500 | recall_priority_score(superseded=True) = base-500 |
| TC-S-05 | validated_count封顶20 | recall_priority_score(validated_count=100) = recall_priority_score(validated_count=20) |

### 3.2 单元测试：`tests/sparkgraph/test_recaller.py`（重建）

| 用例 | 描述 | 断言 |
|---|---|---|
| TC-R-01 | 精确通道：FTS匹配active节点可召回 | 召回结果含目标，validated_count+1 |
| TC-R-02 | 图扩展通道：seed的1-hop active邻居可召回 | 邻居节点出现在结果中 |
| TC-R-03 | 图扩展不包含非active邻居 | deprecated邻居不出现 |
| TC-R-04 | 合并后按recall_priority_score排序 | 高validated_count节点排在前 |
| TC-R-05 | deprecated节点不参与任何通道 | deprecated节点不在召回结果中 |
| TC-R-06 | 低signal查询返回空 | 空列表 |

### 3.3 集成测试：`tests/integrations/test_sparkgraph_v2_flow.py`（重建）

| 用例 | 描述 | 断言 |
|---|---|---|
| TC-I-01 | 写入flush节点→直接active→可立即召回 | recall可命中，validated_count=0 |
| TC-I-02 | 写入reflection节点→直接deprecated→不可召回 | recall不命中 |
| TC-I-03 | 节点被召回→validated_count++ | 两次recall后validated_count=2 |
| TC-I-04 | 去重更新：reflection节点被explicit覆盖→status→active | 去重后status变为active |
| TC-I-05 | maintenance：30天idle+低validated_count→deprecated | deprecated后不可召回 |

---

## 四、实施顺序

```
Step 1:  db.py        — 新增 validated_count 列，删除 EVIDENCE_TABLE 常量
Step 2:  types.py     — 删除 CANDIDATE，删除 RecallChannel
Step 3:  scoring.py   — 完全重写（删6分量表，改为查表+recall_priority_score）
Step 4:  store.py     — 删除 append_evidence/count_evidence，新增 increment_validated_count
Step 5:  recaller.py  — 删除三通道，重构为两通道
Step 6:  maintenance.py — 清理 evidence 逻辑
Step 7:  sparkgraph_tool.py — 清理写入路径
Step 8:  新建 test_scoring.py（5个用例）
Step 9:  重建 test_recaller.py（6个用例）
Step 10: 重建集成测试（5个用例）
Step 11: 全量测试 + 回归验证
Step 12: 推送
```

---

## 五、向后兼容性

- `sg_evidence` 表：**保留 schema，但不再写入**（供数据迁移观察）
- 旧数据（已有的 CANDIDATE 节点）：在第一次 `run_flush_maintenance` 时统一降为 deprecated（因为它们没有 validated_count，且大多数 confidence < 0.70）
- `store.get_node()` 返回的 dict：**新增 `validated_count` 字段（老数据默认为0）**
