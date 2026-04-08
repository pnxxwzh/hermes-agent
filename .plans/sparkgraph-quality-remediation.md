# SparkGraph 质量整改专项文档
**状态**：规划中
**版本**：v1.0
**日期**：2026-04-03
**对比基准**：graph-memory（v1）

---

## 一、背景

在完成 SparkGraph 简化重构（移除 CANDIDATE 状态、sg_evidence 表）后，对 SparkGraph 与 graph-memory 进行了系统性对比，发现以下问题：

| 类别 | 数量 |
|---|---|
| 过度设计（需删除） | 3 |
| 功能缺失（需补充） | 7 |
| 语义错误（需修复） | 1 |

---

## 二、过度设计清理

### 2.1 清理死字段：`stability` 和 `reuse_score`

**现状**：`SparkGraphNodeInput` 仍接受 `stability` 和 `reuse_score` 参数，`store.insert_node` 和 `store.update_node_scoring` 仍写入/读取它们，schema 仍有这两列。但 `initial_score_for()` 完全不使用它们。

**graph-memory 参考**：无这两个字段。`confidence` 直接是一个 0~1 浮点数，无内部公式。

**整改方案**：

```
1. db.py
   - NODES_TABLE DDL：删除 stability、reuse_score 列
   - SCHEMA_VERSION: 4 → 5

2. store.py
   - SparkGraphNodeInput：删除 stability、reuse_score 字段
   - insert_node()：删除这两个字段的 INSERT
   - update_node_scoring()：删除这两个字段的 UPDATE

3. scoring.py
   - 删除 should_deprecate_active() 中的 stability 参数
   - 删除 STALE_STABILITY_THRESHOLD 常量

4. maintenance.py
   - 删除 should_deprecate_active() 的 stability 调用

5. tools/sparkgraph_tool.py
   - 写入时不再传 stability/reuse_score

6. 测试
   - test_store.py：删除相关字段的测试
   - test_maintenance.py：删除 stability 相关断言
```

**验收标准**：
- `sg_nodes` 表只有这些列：`id, type, summary, detail, status, confidence, source_kind, canonical_key, meta, created_at, updated_at, last_recalled_at, validated_count`
- `should_deprecate_active` 只需 `days_since_recall_hit` + `validated_count` 两个条件

---

### 2.2 删除冗余字段：`recall_hits`（meta 内）

**现状**：`increment_validated_count()` 同时写两处：
```python
meta["recall_hits"] = int(meta.get("recall_hits", 0)) + 1  # meta JSON
UPDATE sg_nodes SET validated_count = validated_count + 1        # 独立列
```

`recall_hits` 和 `validated_count` 功能完全重复。

**整改方案**：
```
store.py
  - increment_validated_count()：只写 validated_count 列，不碰 meta
  - get_node() 返回的 dict 仍含 meta，但不包含 recall_hits
```

**验收标准**：
- meta JSON 内无 `recall_hits` 字段（历史数据迁移时忽略该字段）
- `validated_count` 是唯一的命中计数

---

### 2.3 清理 `default_type_priors` 及其调用

**现状**：`sparkgraph_tool.py` 仍调用 `default_type_priors()` 计算 `stability`/`reuse_score`，传给 `SparkGraphNodeInput`，但这两个字段已无意义。

**整改方案**：
```
scoring.py
  - 删除 default_type_priors() 函数

tools/sparkgraph_tool.py
  - 写入时不再调用 default_type_priors，不再传 stability/reuse_score
```

---

## 三、功能缺失补充

### 3.1 去重命中时 `validated_count++`（高优）

**现状**：去重命中时只更新 confidence 和 status，`validated_count` 不变。

```python
# sparkgraph_tool.py 当前逻辑（错误）
score_result = initial_score_for(source_kind)
store.update_node_scoring(existing.node_id, confidence=score_result.confidence, ...)
# validated_count 没有 +1
```

**语义问题**：去重命中意味着这条知识再次被确认，理应算一次有效使用。

**整改方案**：
```
tools/sparkgraph_tool.py
  - 去重路径：命中后调用 store.increment_validated_count([node_id])

store.py
  - 已有 increment_validated_count()，直接复用
```

**验收标准**：
- 去重命中后，该节点的 `validated_count` 比命中前 +1
- 多次去重同一节点，validated_count 持续累加

---

### 3.2 添加 `tokenEstimate`（中优）

**现状**：`recall_nodes()` 返回 `(nodes, edges)`，无大小提示，上游无法据此做截断决策。

**graph-memory 参考**：
```typescript
// 估算：总字符数 / 3 ≈ token 数
tokenEstimate: Math.ceil(nodes.reduce((s, n) => s + n.content.length + n.description.length, 0) / 3)
```

**整改方案**：
```
recaller.py
  - recall_nodes() 返回改为三元组：(nodes, edges, token_estimate)
  - token_estimate = sum(len(n.summary) + len(n.detail)) / 3 for n in nodes

tools/sparkgraph_tool.py（上游调用方）
  - 适配新的三元组返回值
  - 用于决定 recall 结果是否值得注入 context
```

**验收标准**：
- `recall_nodes()` 返回 `token_estimate` 字段
- 估算误差在 ±20% 以内

---

### 3.3 添加 `source_sessions` 追踪（中优）

**现状**：无 session 血缘记录，无法知道一个节点来自哪些会话。

**graph-memory 参考**：
```typescript
const sessions = JSON.stringify(Array.from(new Set([...ex.sourceSessions, sessionId])));
const count = ex.validatedCount + 1;
db.prepare("UPDATE gm_nodes SET ... validated_count=?, source_sessions=? ...")
```

**整改方案**：
```
db.py
  - NODES_TABLE DDL 新增 source_sessions TEXT（JSON 数组）
  - 默认值："[]"

store.py
  - insert_node()：写入 source_sessions=[session_id]
  - increment_validated_count()：不去动 source_sessions（gm在upsert时合并，sg在去重时合并）
  - 新增方法 merge_source_sessions(node_id, new_session_id)：合并 session 列表

tools/sparkgraph_tool.py
  - 去重路径：调用 merge_source_sessions()
  - 新建节点：写入 source_sessions=[session_id]

scoring.py / recaller.py
  - source_sessions 仅作记录，不参与召回排序
```

**验收标准**：
- 节点可追溯其来自哪些 session
- 同一节点在多个 session 中被更新，session 列表合并去重

---

### 3.4 `getBySourceKind` 补充召回（中优）

**现状**：召回只有全文搜索，无按来源类型精确补充的能力。

**graph-memory 参考**：
```typescript
// assemble 阶段：只注入与当前 query 相关的 explicit/manual 记忆
export function getBySourceKindFiltered(
  db, kinds: string[], query: string, limit=6
): GmNode[]
```

**整改方案**：
```
store.py
  - 新增 get_by_source_kind(kinds: list[str], query="", limit=6) -> list[dict]
  - 逻辑：查 source_kind IN kinds 的 active 节点 → 按 query 关键词匹配排序

recaller.py
  - 在 recall_nodes() 末尾，可选补充：
    explicit_nodes = store.get_by_source_kind(["explicit", "manual"], query)
    高置信节点混入最终结果
  - 或者：作为第三通道（高于 FTS/向量），确保 explicit 记忆优先出现
```

**验收标准**：
- `get_by_source_kind(["explicit", "manual"], "proxy pac")` 只返回 explicit/manual 来源的相关节点
- 结果按 validated_count + updated_at 排序

---

### 3.5 `mergeNodes` 节点合并能力（低优）

**现状**：只有 dedup（覆盖），无节点合并能力。

**graph-memory 参考**：
```typescript
// 合并两个节点：keepId 保留，mergeId deprecated
// 边迁移 + validatedCount 累加 + sourceSessions 合并
export function mergeNodes(db, keepId: string, mergeId: string): void
```

**整改方案**：
```
store.py
  - 新增 merge_nodes(keep_id: str, merge_id: str)
  - 逻辑：
    1. keep 的 validated_count += merge 的 validated_count
    2. 迁移 merge 的边到 keep（from_id/to_id 替换）
    3. 删自环（同 from_id == to_id）
    4. 去重边（同 from_id + to_id + type 只留一条）
    5. merge 节点标记 deprecated
```

**验收标准**：
- merge 后 keep 的 validated_count = 合并后总和
- merge 节点 status = deprecated
- merge 的边全部迁移到 keep

---

### 3.6 `defaultInject` 可注入性控制（低优）

**现状**：所有 active 节点一视同仁，无优先级控制。

**整改方案**：
```
db.py
  - NODES_TABLE 新增 default_inject INTEGER DEFAULT 1（1=默认注入，0=按需注入）

store.py
  - insert_node()：写入 default_inject=1
  - 去重时保持 default_inject 不变

tools/sparkgraph_tool.py
  - explicit 节点：default_inject=1
  - reflection/shadow：default_inject=0
  - 其他：default_inject=1

recaller.py
  - default_inject=0 的节点在 recall 结果中降权（或单独标记）
  - 上游 assemble 可据此决定是否注入
```

---

### 3.7 `communityId` 社区概念（低优）

**现状**：无社区能力，泛化召回依赖边关系，无法回答"我们处理过哪些领域的问题"这类宽泛问题。

**整改方案**：作为后续 phase，整改文档暂不包含社区检测算法的具体实现（需要 Louvain/Leiden 等算法或预训练模型）。此条目标记为 future work。

---

## 四、语义错误修复

### 4.1 去重命中 `validated_count++`（已在 3.1 覆盖）

见 3.1。

---

## 五、整改实施计划

### Phase 1：立即修复（语义错误 + 过度设计清理）

| 顺序 | 任务 | 文件 | 产出 |
|---|---|---|---|
| 1.1 | 删除 `stability`、`reuse_score` 字段及所有调用 | db.py, store.py, scoring.py, maintenance.py, sparkgraph_tool.py | schema v5 |
| 1.2 | 删除 meta 内的 `recall_hits` | store.py | 单一计数 |
| 1.3 | 删除 `default_type_priors()` | scoring.py, sparkgraph_tool.py | 死代码清理 |
| 1.4 | 去重命中时 `validated_count++` | tools/sparkgraph_tool.py, store.py | 语义正确 |
| 1.5 | 更新所有受影响测试 | test_*.py | 回归通过 |

**验收**：全量测试通过，无 schema 警告。

---

### Phase 2：功能补充（tokenEstimate + sourceSessions + getBySourceKind）

| 顺序 | 任务 | 文件 | 产出 |
|---|---|---|---|
| 2.1 | `recall_nodes()` 返回三元组加 `token_estimate` | recaller.py | 返回值扩展 |
| 2.2 | 添加 `source_sessions` 列和合并逻辑 | db.py, store.py, sparkgraph_tool.py | session 血缘 |
| 2.3 | 实现 `get_by_source_kind()` | store.py | 来源过滤 |
| 2.4 | `get_by_source_kind` 接入 recall 流程 | recaller.py | explicit 优先 |
| 2.5 | 更新集成测试 | test_sparkgraph_v2_flow.py | 新场景覆盖 |

**验收**：新功能有测试，回归通过。

---

### Phase 3：能力补充（mergeNodes + defaultInject）

| 顺序 | 任务 | 文件 | 产出 |
|---|---|---|---|
| 3.1 | 实现 `merge_nodes()` | store.py | 节点合并 |
| 3.2 | 添加 `default_inject` 列 | db.py, store.py | 可注入控制 |
| 3.3 | 更新 `sparkgraph_tool.py` 写入逻辑 | tools/sparkgraph_tool.py | 注入标记 |
| 3.4 | 更新测试 | test_store.py | 新功能覆盖 |

---

### Phase 4：文档与收尾

| 任务 | 内容 |
|---|---|
| 更新 SOUL.md 中的 SparkGraph 描述 | 反映新架构 |
| 更新 AGENTS.md 中的 SparkGraph 说明 | 说明简化后的状态机 |
| 推送所有变更 | 提交记录 |

---

## 六、Schema 演进

```
v1: 初始（candidate/active/deprecated + evidence）
v2: 移除 candidate 状态
v3: 移除 recall channel 枚举
v4: 添加 validated_count 列（当前）
v5: 删除 stability, reuse_score；添加 source_sessions, default_inject
```

**v5 最终 schema（目标）：**

```sql
CREATE TABLE sg_nodes (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('active', 'deprecated')),
    confidence REAL NOT NULL DEFAULT 0.0,
    source_kind TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    meta TEXT DEFAULT '{}',
    source_sessions TEXT DEFAULT '[]',     -- 新增
    default_inject INTEGER DEFAULT 1,       -- 新增
    validated_count INTEGER DEFAULT 0,     -- 已有
    last_recalled_at INTEGER DEFAULT 0,    -- 已有
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(type, canonical_key)
);
```

---

## 七、测试全景（整改后目标）

### 7.1 单元测试（按文件）

| 文件 | 测试内容 | 预期用例数 |
|---|---|---|
| test_types.py | 两状态枚举 | 3 |
| test_scoring.py | 查表、recall_priority_score、should_deprecate | 9 |
| test_recaller.py | 两通道召回、排序、limit | 8 |
| test_maintenance.py | stale deprecated 逻辑 | 3 |
| test_store.py | CRUD + increment_validated_count + merge_nodes | 10 |

### 7.2 集成测试

| 文件 | 测试场景 | 预期用例数 |
|---|---|---|
| test_sparkgraph_v2_flow.py | flush→active、reflection→deprecated、validated_count++、去重+1、去重合并session、maintenance deprecated | 6 |

**目标总用例数**：约 39 个

---

## 八、未纳入本次整改的事项

以下事项需更大规模重构，暂不纳入：

| 事项 | 原因 |
|---|---|
| `communityId` + 社区检测算法 | 需引入 Louvain/Leiden 等算法，工程量大 |
| `memoryClass`（episodic/semantic） | 需 LLM 配合标注，当前无标注流程 |
| 向量索引优化（FAISS/MILvus） | 当前用暴力遍历，向量规模不大时够用 |
| 跨语言（Python→TypeScript）接口对齐 | 独立工作流，不影响功能正确性 |
