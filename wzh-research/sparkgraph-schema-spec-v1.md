# SparkGraph Schema 规格说明（Schema Spec v1）

> 目的：把 SparkGraph 的数据库设计从方向性描述压到可实施规格。  
> 作用：作为 `agent/graph_memory/db.py` 与迁移实现的直接依据。  
> 范围：只覆盖第一版必须的 durable knowledge graph 存储，不覆盖未来可能加入的重型社区/全图分析表。

## 1. 设计原则

1. **轻量**
   - 只保留 SparkGraph 第一版所需的主表
2. **稳定**
   - schema 尽量小、索引明确、迁移简单
3. **可回溯**
   - 节点要能追到来源 evidence
4. **可检索**
   - 既支持 FTS，也支持 embedding
5. **可治理**
   - 支持 `candidate / active / deprecated`

---

## 2. DB 路径规则

### 默认路径

1. default profile
   - `~/.hermes/graph-memory/default.db`
2. named profile
   - `~/.hermes/profiles/<name>/graph-memory/default.db`

### 原则

1. 以当前 `HERMES_HOME` 为根
2. profile 是 durable graph 的存储边界
3. 不在第一版引入更细粒度 namespace

---

## 3. Migration 元数据

必须保留 migration 表：

### `_migrations`

字段：

1. `version INTEGER PRIMARY KEY`
2. `applied_at INTEGER NOT NULL`

规则：

1. 每次 schema 变更必须增加 migration version
2. migration 必须幂等

初版版本号建议：

- `1`

---

## 4. 主表定义

## 4.1 `sg_nodes`

### 作用

存 durable knowledge 节点。

### 字段

1. `id TEXT PRIMARY KEY`
2. `type TEXT NOT NULL`
3. `summary TEXT NOT NULL`
4. `detail TEXT NOT NULL DEFAULT ''`
5. `status TEXT NOT NULL`
6. `confidence REAL NOT NULL DEFAULT 0`
7. `stability REAL NOT NULL DEFAULT 0`
8. `reuse_score REAL NOT NULL DEFAULT 0`
9. `source_kind TEXT NOT NULL`
10. `canonical_key TEXT NOT NULL`
11. `meta TEXT NOT NULL DEFAULT '{}'`
12. `created_at INTEGER NOT NULL`
13. `updated_at INTEGER NOT NULL`

### 枚举约束

#### `type`

允许：

1. `FACT`
2. `PREFERENCE`
3. `ISSUE`
4. `RESOURCE`
5. `DECISION`

#### `status`

允许：

1. `candidate`
2. `active`
3. `deprecated`

#### `source_kind`

允许：

1. `auto`
2. `explicit`
3. `manual`
4. `reflection`
5. `review`

### 字段语义

#### `summary`

短摘要，用于 recall block 主展示。

#### `detail`

补充性说明，不一定总注入 prompt。

#### `confidence`

对“这条知识是否可信”的综合置信。

#### `stability`

对“这条知识是否稳定、长期成立”的综合评分。

#### `reuse_score`

对“未来再次被用到的概率/价值”的评分。

#### `canonical_key`

用于去重与 merge 的稳定键，不等于用户可读标题。

#### `meta`

JSON 字符串，存放：

1. entities
2. language
3. classification diagnostics
4. merge lineage
5. custom flags

### 索引

1. `UNIQUE INDEX ux_sg_nodes_canonical_type ON sg_nodes(canonical_key, type)`
2. `INDEX ix_sg_nodes_status_type ON sg_nodes(status, type)`
3. `INDEX ix_sg_nodes_updated_at ON sg_nodes(updated_at)`

---

## 4.2 `sg_edges`

### 作用

存节点关系。

### 字段

1. `id TEXT PRIMARY KEY`
2. `from_id TEXT NOT NULL`
3. `to_id TEXT NOT NULL`
4. `type TEXT NOT NULL`
5. `weight REAL NOT NULL DEFAULT 0`
6. `meta TEXT NOT NULL DEFAULT '{}'`
7. `created_at INTEGER NOT NULL`

### 枚举约束

#### `type`

允许：

1. `RELATED_TO`
2. `DEPENDS_ON`
3. `CONFLICTS_WITH`
4. `DERIVED_FROM`
5. `APPLIES_TO`

### 约束

1. `from_id` 和 `to_id` 必须引用现有 `sg_nodes.id`
2. 禁止自环：`from_id != to_id`

### 索引

1. `INDEX ix_sg_edges_from_id ON sg_edges(from_id)`
2. `INDEX ix_sg_edges_to_id ON sg_edges(to_id)`
3. `INDEX ix_sg_edges_type ON sg_edges(type)`
4. `UNIQUE INDEX ux_sg_edges_unique ON sg_edges(from_id, to_id, type)`

---

## 4.3 `sg_evidence`

### 作用

记录节点来源，支撑回溯、promotion、deprecation 与人工审查。

### 字段

1. `id TEXT PRIMARY KEY`
2. `node_id TEXT NOT NULL`
3. `session_id TEXT NOT NULL`
4. `turn_index INTEGER NOT NULL`
5. `source_hash TEXT NOT NULL`
6. `source_kind TEXT NOT NULL`
7. `created_at INTEGER NOT NULL`

### 约束

1. `node_id` 必须引用现有 `sg_nodes.id`

### 索引

1. `INDEX ix_sg_evidence_node_id ON sg_evidence(node_id)`
2. `INDEX ix_sg_evidence_session_turn ON sg_evidence(session_id, turn_index)`
3. `UNIQUE INDEX ux_sg_evidence_unique ON sg_evidence(node_id, session_id, turn_index, source_hash)`

---

## 4.4 `sg_vectors`

### 作用

存 embedding，用于 semantic dedup 与 recall。

### 字段

1. `node_id TEXT PRIMARY KEY`
2. `content_hash TEXT NOT NULL`
3. `embedding BLOB NOT NULL`
4. `dims INTEGER NOT NULL`
5. `updated_at INTEGER NOT NULL`

### 约束

1. `node_id` 必须引用现有 `sg_nodes.id`

### 索引

1. 主键足够

---

## 5. FTS 设计

建议为 `sg_nodes` 增加 FTS5 虚表：

### `sg_nodes_fts`

字段：

1. `summary`
2. `detail`

### 原则

1. content table 绑定 `sg_nodes`
2. 插入 / 更新 / 删除触发器自动同步

### 用途

1. semantic dedup 初筛
2. recall 初筛
3. graph_search 文本检索

---

## 6. ID 生成策略

### `sg_nodes.id`

建议：

1. 不直接暴露 canonical key
2. 使用稳定 UUID / ULID 均可
3. canonical key 单独存字段

### `sg_edges.id`

建议：

1. 可使用 UUID
2. 但主唯一性依靠 `(from_id, to_id, type)`

### `sg_evidence.id`

建议：

1. UUID 即可

---

## 7. canonical_key 规则

### 设计目标

1. 不依赖具体自然语言词组
2. 能支持多语言
3. 可用于稳定 merge

### 初版规则

由 classifier / dedup 流程共同生成：

1. 先得到结构化 `summary`
2. 抽取规范化实体集合
3. 结合 `type` 与 summary normalization 生成 canonical candidate
4. 如有近邻高相似项，优先复用已有 canonical key

### 注意

这里故意不把 canonical_key 算法写死成“某个字符串变换规则”。  
因为这部分需要实现时结合多语言样本验证。

### 已知风险

1. canonical_key 仍是第一版的风险点
2. 若算法太弱，会导致 merge 不稳定
3. 若算法太激进，会导致误合并

因此：

1. 需要单独单测
2. 需要 fixture eval
3. 需要 shadow mode 观测

---

## 8. JSON `meta` 字段约定

### `sg_nodes.meta`

建议字段：

1. `entities: string[]`
2. `language: string`
3. `session_bound: boolean`
4. `classification_source: string`
5. `relation_hints: array`
6. `merge_parent_id: string | null`
7. `notes: string | null`

### `sg_edges.meta`

建议字段：

1. `support_source: string`
2. `confidence_components: object`
3. `evidence_count: number`

---

## 9. 初版不进入 schema 的内容

明确不进第一版：

1. community table
2. pagerank table
3. transcript message warehouse
4. recall pool persistence
5. dream artifacts persistence

这些能力若需要，后续通过 migration 增加。

---

## 10. 读写路径设计

## 10.1 写入

写入路径：

1. create candidate node
2. merge into existing node
3. update active node scores
4. create/update edges
5. attach evidence
6. update vector

## 10.2 读取

读取路径：

1. FTS query
2. vector query
3. relation expansion
4. active node listing
5. candidate review queries

---

## 11. 迁移策略

### 初版

1. migration `1` 初始化所有表与索引

### 要求

1. migration 幂等
2. 每步 migration 要么完整成功，要么回滚
3. 迁移失败不应损坏已有 DB

### 当前现实

第一版因为没有旧 SparkGraph DB 历史包袱，迁移复杂度低。  
但必须把 migration 机制一开始就立住，不然后续加字段会很痛苦。

---

## 12. 必测用例

### `test_db_init_creates_all_tables`

检查：

1. 所有主表存在
2. FTS 表存在
3. 所有索引存在

### `test_db_migration_idempotent`

检查：

1. 重复初始化不会报错
2. schema 不重复创建

### `test_default_db_path_scopes_to_profile`

检查：

1. default profile 路径正确
2. named profile 路径正确

### `test_node_unique_by_canonical_key_and_type`

检查：

1. 同 `canonical_key + type` 不允许重复

### `test_edge_unique_constraint`

检查：

1. `(from_id, to_id, type)` 不允许重复

### `test_evidence_unique_constraint`

检查：

1. 相同 node/session/turn/source_hash 不重复

### `test_fts_trigger_sync`

检查：

1. insert/update/delete 时 FTS 自动同步

### `test_vector_row_tracks_content_hash`

检查：

1. content 变化后 vector row 可被识别为 stale

---

## 13. 已知缺陷与未决点

### 13.1 canonical_key 算法尚未冻结

schema 已预留字段，但算法本身仍需在实现和评测中验证。

### 13.2 向量存储格式尚未最终定型

当前只规定为 `BLOB`，具体序列化格式实现时再定。

### 13.3 是否需要把 qualification 结果入库

当前不建议放进 SparkGraph DB，优先放运行时/配置层。  
但后续若需要长期统计，可能新增独立表。

---

## 14. 结论

本 schema 已经足够进入实现。

它的特点是：

1. 轻量
2. 与 SparkGraph 目标一致
3. 足够支持 durable knowledge / dedup / recall / evidence
4. 没有把旧重型系统的复杂度带进来

如果后续需要更复杂能力，必须通过 migration 增量扩展，而不是一开始把 schema 做重。
