# SparkGraph 正式软件设计说明书（SDS v1）

> 状态：Draft v1  
> 作用：本文件是 SparkGraph 的正式软件设计说明书。  
> 目标：把目前已经确认的讨论结果，收敛为一份可实施、可测试、可评审、可跟踪的工程设计。  
> 适用范围：Hermes 源码级整合，不修改参考代码目录 `wzh-research/graph-memory/`。

## 1. 文档目的

本文回答以下问题：

1. SparkGraph 究竟是什么，不是什么
2. 它在 Hermes 架构中的职责边界是什么
3. 它需要新增哪些模块、修改哪些注册点
4. 它的数据库、配置、运行时、评测、测试如何设计
5. 它的失败模式、降级模式、超时策略如何工作
6. 它如何在尽量解耦的前提下接入 Hermes 主线
7. 它目前有哪些已知风险、缺陷与尚未解决的问题

本文是当前 SparkGraph 项目的正式设计依据。

配套边界规格：

- `wzh-research/sparkgraph-small-model-scope-spec-v1.md`

---

## 2. 设计结论总览

### 2.1 一句话定义

**SparkGraph 是 Hermes 内部的 Durable Knowledge Graph Backend。**

### 2.2 只做两件事

1. 记录具体且有价值的 durable knowledge
2. 在当前对话需要时准确召回相关知识点及其关系邻域

### 2.3 明确不做

1. 不做 OpenClaw 式 context engine
2. 不做 visible transcript / assemble / compact 主链
3. 不做 Hermes skill 的替代系统
4. 不做第二套“全能记忆操作系统”
5. 不做依赖固定词组的多语言硬编码过滤
6. 不自行发明单 profile 内多长期 agent namespace

### 2.4 架构原则

1. 尽量与 Hermes 主线解耦
2. 但必须在 Hermes 正式注册点接入
3. 未来主线升级时，尽量只处理接入层冲突
4. 核心逻辑不内联到主线大文件
5. 以 profile 为 durable 存储边界，严格跟随 `HERMES_HOME`

---

## 3. 背景与问题陈述

### 3.1 现状

Hermes 当前已经有：

1. `SOUL.md`
2. `MEMORY.md`
3. `USER.md`
4. skills
5. `session_search`
6. context compression
7. background review / flush

这些能力在工程稳定性、上下文可控性、长期可维护性方面较强，但缺少一个结构化的 durable knowledge layer。

### 3.2 旧方案的问题

参考 graph-memory 插件虽然展示了强图谱能力，但原始方向过重，主要问题包括：

1. reflection 过强，容易把会话摘要写进长期图
2. `TASK/SKILL/EVENT` 三分法与 Hermes 自身 `skills` 重叠
3. 图谱承担了过多上下文装配责任，容易与 Hermes 冲突
4. 多数判断过度依赖模型，治理成本高
5. 脏数据率高时，召回质量很快恶化

### 3.3 SparkGraph 的目标

SparkGraph 不是复制旧系统，而是把其中最有价值的部分抽出来，变成一个更轻、更准、更符合 Hermes 的知识图后端。

---

## 4. 术语

### 4.1 Durable Knowledge

能脱离当前轮对话单独成立，且未来仍有复用价值的知识点。

### 4.2 Candidate

已被提取，但尚未确认足够稳定，不能直接视为正式长期知识。

### 4.3 Active

已通过升级条件，允许参与正式 recall。

### 4.4 Deprecated

过时、被替代、低质量或不再推荐召回的知识。

### 4.5 Shadow Mode

运行完整 SparkGraph 流程，但不写入 `active`、不影响正式 recall 的旁路验证模式。

### 4.6 Qualification

对用户配置的小模型进行基线评测后赋予的准入状态。

---

## 5. 系统边界

### 5.1 Hermes 负责

1. 身份与人格：`SOUL.md`
2. curated memory：`MEMORY.md`、`USER.md`
3. 过程性经验：skills
4. 历史回忆：`session_search`
5. prompt 主骨架
6. compression / flush / review 主链
7. 最终制度化写入

### 5.2 SparkGraph 负责

1. durable knowledge candidate extraction
2. structural classification
3. semantic dedup / merge
4. lightweight graph persistence
5. graph recall
6. lightweight maintenance
7. small-model qualification / shadow mode 支撑
8. unverified-model safe fallback modes

### 5.3 不允许 SparkGraph 直接负责

1. 生成 Hermes skill 最终版本
2. 改写 `MEMORY.md` / `USER.md`
3. 直接接管 prompt assembly
4. 接管 Hermes compression
5. 在小模型 `unverified` 时把自动 extraction/classification 强行接入正式主链

---

## 6. 总体架构

```text
User Turn
  -> Hermes main loop
    -> SparkGraph integration hook
      -> SparkGraph core
        -> extraction
        -> classification
        -> dedup / merge
        -> store
        -> recall
        -> maintenance
    -> Hermes model call
    -> Hermes flush/review/session lifecycle
```

### 6.1 分层

#### Core Layer

位于：

- `agent/graph_memory/*`

职责：

1. DB
2. store
3. types
4. config parse
5. extraction
6. classifier
7. dedup
8. recaller
9. formatter
10. maintenance
11. manager

#### Integration Layer

位于少量 Hermes 主线文件中。

职责：

1. 生命周期注册
2. 配置桥接
3. runtime provider 解析桥接
4. health/status 桥接
5. setup / restore / migration 接线
6. flush / session_end 接线

### 6.2 不允许的结构

1. 不在 `run_agent.py` 内实现 SparkGraph 核心逻辑
2. 不在多个主线文件中复制判断规则
3. 不通过隐式全局变量旁路接入

---

## 7. 模块设计

建议新增目录：

```text
agent/graph_memory/
  __init__.py
  types.py
  config.py
  db.py
  store.py
  classifier.py
  extractor.py
  dedup.py
  scoring.py
  recaller.py
  formatter.py
  maintenance.py
  manager.py
  runtime.py
```

### 7.1 `types.py`

职责：

1. 定义节点类型
2. 定义边类型
3. 定义状态枚举
4. 定义 classifier 输出结构
5. 定义 recall result 结构
6. 定义 runtime health/result 结构

首批枚举：

#### NodeType

1. `FACT`
2. `PREFERENCE`
3. `ISSUE`
4. `RESOURCE`
5. `DECISION`

#### EdgeType

1. `RELATED_TO`
2. `DEPENDS_ON`
3. `CONFLICTS_WITH`
4. `DERIVED_FROM`
5. `APPLIES_TO`

#### NodeStatus

1. `candidate`
2. `active`
3. `deprecated`

### 7.2 `config.py`

职责：

1. 读取 `graph_memory` 顶层配置块
2. 提供默认值
3. 校验 runtime 子块
4. 解析默认 db path
5. 读取 qualification / shadow mode / probe 配置

### 7.3 `db.py`

职责：

1. schema 初始化
2. schema migration
3. path resolution
4. profile 边界 enforcement

### 7.4 `store.py`

职责：

1. node CRUD
2. edge CRUD
3. evidence CRUD
4. vector CRUD
5. active/candidate/deprecated 查询

### 7.5 `extractor.py`

职责：

1. 从最近消息窗口抽取候选知识点
2. 产出结构化 `candidate_summary`
3. 产出 evidence span
4. 仅提取候选，不决定最终状态
5. 根据 qualification 状态决定正式、shadow 或禁用模式

### 7.6 `classifier.py`

职责：

1. 判断是否 durable
2. 判断节点类型
3. 判断是否 session-bound
4. 产出稳定性、复用性、关系提示

注意：

1. 只输出结构化 JSON
2. 不生成长自然语言报告
3. 不能直接写 active 节点
4. `unverified` / `restricted` 模型不能进入正式 candidate pipeline
5. 第一版正式主 schema 只要求：
   - `is_durable`
   - `knowledge_type`
   - `needs_current_session`
   - 可选 `strength`

### 7.7 `dedup.py`

职责：

1. FTS 初筛
2. embedding 近邻检索
3. merge candidate 排名
4. 判断新建还是合并

### 7.8 `scoring.py`

职责：

1. durability/reuse/stability/support/source 评分
2. session-bound penalty
3. active promotion 判定
4. deprecation 判定
5. recall ranking score

### 7.9 `recaller.py`

职责：

1. query 检索 top candidates
2. relation one-hop expansion
3. 过滤 candidate/low-signal/deprecated
4. 排序

### 7.10 `formatter.py`

职责：

1. 把 recall results 格式化成短 recall block
2. 控制 token/char budget
3. 保证不污染 cached prompt

### 7.11 `maintenance.py`

职责：

1. candidate promotion scan
2. low-signal active downgrade
3. stale node cleanup
4. vector backfill
5. optional dream jobs 的底层支持

### 7.12 `manager.py`

职责：

1. SparkGraph 主入口
2. 统一协调 extraction/classification/dedup/recall/maintenance
3. 对 Hermes 暴露集成友好的 API
4. 根据模型状态切换：
   - `qualified mode`
   - `shadow mode`
   - `manual/explicit-only mode`

### 7.13 `runtime.py`

职责：

1. SparkGraph 子 runtime 解析
2. probe
3. health 聚合
4. qualification 状态读取

---

## 7A. 运行模式设计

SparkGraph v1 必须显式支持三种运行模式。

### 7A.1 `qualified mode`

条件：

1. 小模型通过 preflight
2. 小模型通过 baseline eval
3. 小模型通过 system-quality eval

行为：

1. 允许正式 automatic extraction
2. 允许正式 automatic classification
3. 允许候选结果进入 scoring / promotion 主链

### 7A.2 `shadow mode`

条件：

1. 用户显式启用 shadow
2. 或小模型状态为 `unverified`

行为：

1. extraction/classification 可以运行
2. 结果只进入 shadow 记录
3. 不得驱动 active promotion
4. 不得影响正式 recall 主链

### 7A.3 `manual/explicit-only mode`

条件：

1. 小模型不可用
2. 小模型持续不合格
3. 用户主动关闭自动提取

行为：

1. 禁用自动 extraction/classification
2. 保留显式输入、store、dedup、recall 能力
3. SparkGraph 仍然应能作为 durable knowledge store 正常工作

---

## 8. 数据库设计

### 8.1 路径规则

默认 DB 路径：

1. default profile：
   - `~/.hermes/graph-memory/default.db`
2. named profile：
   - `~/.hermes/profiles/<name>/graph-memory/default.db`

### 8.2 主表

#### `sg_nodes`

建议字段：

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

索引建议：

1. `UNIQUE(canonical_key, type)`
2. `(status, type)`
3. `(updated_at)`

#### `sg_edges`

建议字段：

1. `id TEXT PRIMARY KEY`
2. `from_id TEXT NOT NULL`
3. `to_id TEXT NOT NULL`
4. `type TEXT NOT NULL`
5. `weight REAL NOT NULL DEFAULT 0`
6. `meta TEXT NOT NULL DEFAULT '{}'`
7. `created_at INTEGER NOT NULL`

索引建议：

1. `(from_id)`
2. `(to_id)`
3. `(type)`

#### `sg_evidence`

建议字段：

1. `id TEXT PRIMARY KEY`
2. `node_id TEXT NOT NULL`
3. `session_id TEXT NOT NULL`
4. `turn_index INTEGER NOT NULL`
5. `source_hash TEXT NOT NULL`
6. `source_kind TEXT NOT NULL`
7. `created_at INTEGER NOT NULL`

索引建议：

1. `(node_id)`
2. `(session_id, turn_index)`

#### `sg_vectors`

建议字段：

1. `node_id TEXT PRIMARY KEY`
2. `content_hash TEXT NOT NULL`
3. `embedding BLOB NOT NULL`
4. `dims INTEGER NOT NULL`
5. `updated_at INTEGER NOT NULL`

### 8.3 当前明确不进第一版的表

1. community summaries
2. heavy graph clustering tables
3. transcript assembly tables
4. OpenClaw 风格 `gm_messages` 大消息仓库

### 8.4 为什么不保留消息仓库

因为 SparkGraph 的目标不是重建第二套 session system。  
证据应保留必要的轻量映射，而不是复制整套消息流水。

---

## 9. 配置设计

### 9.1 顶层配置块

配置文件中保留：

```yaml
graph_memory:
  db_path: ""
  required: false
  probe_on_startup: true
  graph_budget_ratio: 0.12
  recall_max_nodes: 4
  recall_max_depth: 1
  auto_extract_enabled: true
  explicit_enabled: true
  reflection_enabled: true
  maintenance_enabled: true
  shadow_mode: false
  qualifier:
    auto_run_smoke: true
    auto_run_full_eval: false
  reflection:
    provider: "auto"
    model: ""
    base_url: ""
    api_key: ""
    timeout: 8
  extraction:
    provider: "auto"
    model: ""
    base_url: ""
    api_key: ""
    timeout: 20
  maintenance:
    provider: "auto"
    model: ""
    base_url: ""
    api_key: ""
    timeout: 60
  embedding:
    provider: "auto"
    model: ""
    base_url: ""
    api_key: ""
    dimensions: 0
    timeout: 15
```

约束：

1. `auto_extract_enabled=true` 不等于无条件自动运行
2. 若模型状态不是 `qualified`，系统必须自动降级到 `shadow mode` 或 `manual/explicit-only mode`
3. 不能因为用户填写了 provider/model/base_url/api_key，就默认视为可上线

### 9.2 配置原则

1. 不问“开不开 SparkGraph”
2. SparkGraph 是核心配置块
3. setup 只问 runtime 怎么配
4. 允许 `Keep current` / `Use main provider` / `Use auto` / `Custom endpoint`

### 9.3 setup 生命周期

必须覆盖：

1. 初次 setup
2. rerun setup
3. 单项 reconfigure
4. restore defaults
5. migration/backfill
6. CLI bridge
7. gateway bridge
8. doctor / status / health

---

## 10. Runtime 设计

### 10.1 子 runtime

SparkGraph 至少有四类 runtime：

1. `reflection`
2. `extraction`
3. `maintenance`
4. `embedding`

### 10.2 runtime 要求

每类 runtime 都必须支持：

1. provider resolution
2. startup probe
3. timeout
4. degraded mode
5. structured error surface
6. health visibility

### 10.3 不能作为外挂处理

小模型和 embedding 模型必须像主模型一样进入正式运行时体系，而不是“内部偷偷开一个 httpx client”。

---

## 11. 决策流水线设计

### 11.1 写入流水线

```text
recent turns
  -> extract candidates
  -> classify structurally
  -> semantic dedup search
  -> score
  -> reject / candidate / active / merge / deprecate
```

### 11.2 extract 阶段

输入：

1. 最近 N 条消息
2. 显式 durable memory 请求窗口
3. 手工 `graph_record`

输出：

1. `candidate_summary`
2. `candidate_detail`
3. `evidence_span`

要求：

1. 不输出长报告
2. 不直接做 active 判定

### 11.3 classify 阶段

结构化输出字段至少包括：

1. `is_durable`
2. `knowledge_type`
3. `needs_current_session`
4. `reusability`
5. `stability`
6. `relation_hints`

### 11.4 dedup 阶段

步骤：

1. FTS 搜索
2. embedding 近邻
3. 同类型近似候选比较
4. 选择：
   - merge
   - create candidate
   - create active

### 11.5 scoring 阶段

评分项：

1. `durability_score`
2. `reuse_score`
3. `stability_score`
4. `support_score`
5. `source_score`
6. `session_bound_penalty`

### 11.6 决策输出

可能结果：

1. `reject`
2. `candidate`
3. `promote_to_active`
4. `merge_into_existing`
5. `deprecate_existing`

### 11.7 confidence 设计要求

SparkGraph 必须把 `confidence` 视为正式的一等决策信号，而不是装饰字段。

因此：

1. `confidence` 必须由多信号组合生成
2. 必须可解释、可测试、可调试
3. 必须避免出现“大量节点长期全是同一默认值”的退化
4. 必须避免因为阈值设置不当导致 durable knowledge 被系统性全部筛空

实现要求：

1. 保存 `confidence_components`
2. 保存 `confidence_version`
3. 对 candidate/active 流程分别做 coverage test

---

## 12. 状态迁移设计

### 12.1 `candidate -> active`

满足以下之一即可升级：

1. explicit durable memory 请求
2. 多个独立回合/会话证据支持
3. 被现有 active 节点稳定关系支持
4. 人工或工具明确确认
5. Hermes review 明确提升

### 12.2 `active -> deprecated`

满足以下之一即可降级：

1. 被新的 active 节点替代
2. 被明确冲突或废弃
3. 长期低命中且低支持
4. 被维护任务判为低质量残留

### 12.3 Reflection 限制

1. reflection 默认只到 `candidate`
2. reflection 不能直接写 `active`

---

## 13. Recall 设计

### 13.1 输入

1. 当前用户消息
2. 最近一到两轮上下文摘要
3. 显式 graph search 请求

### 13.2 流程

```text
query
  -> vector/FTS retrieval
  -> one-hop relation expansion
  -> filter
  -> score rank
  -> budget trim
  -> recall block
```

### 13.3 过滤规则

必须过滤：

1. `candidate` 中的低分项
2. `deprecated`
3. 低稳定、低支持节点
4. 明显重复项

### 13.4 输出形式

输出短 recall block，不输出大 XML。

示例：

```text
# SparkGraph Recall
- FACT: ...
- ISSUE: ...
- RELATED: ...
```

### 13.5 动态层原则

recall block 只进入当前轮动态层，不进入 cached system prompt。

---

## 14. 小模型与性能策略

### 14.1 为什么不能纯模型化

1. 成本高
2. 延迟高
3. 质量波动大
4. 多语言稳定性难保证

### 14.2 为什么不能纯规则化

1. 多语言脆弱
2. 语义边界不稳
3. 抽象能力不足

### 14.3 折中方案

1. 核心软件逻辑用规则和状态流
2. 小模型做结构化语义判断
3. 强模型只做少量高价值升级

### 14.3.1 小模型责任边界

SparkGraph v1 中，小模型责任范围已单独冻结，详见：

- `wzh-research/sparkgraph-small-model-scope-spec-v1.md`

核心约束：

1. 小模型只负责候选层最小结构化判断
2. 不负责最终长期层决策
3. 不负责 recall 主排序
4. 不负责 Hermes curated memory / skill 的制度化写入

### 14.4 性能控制

1. 小模型只看最近窗口
2. 输出 JSON，避免长解释
3. recall 主链尽量不用模型
4. maintenance 放后台
5. embedding 失败可回退 FTS

---

## 15. Timeout / Failure / Degraded Mode

### 15.1 独立 timeout

必须为各 runtime 单独设置 timeout：

1. reflection timeout
2. extraction timeout
3. maintenance timeout
4. embedding timeout
5. recall timeout

### 15.2 失败后行为

1. extraction 超时：
   - 本轮跳过或标记 pending
2. classifier 超时：
   - candidate 不提升
3. embedding 超时：
   - 回退 FTS recall
4. recall 超时：
   - 当前轮无 SparkGraph block
5. maintenance 超时：
   - 延后重试

### 15.3 原则

**任何 SparkGraph side task 都不能拖垮 Hermes 主聊天链。**

---

## 16. Setup / Health / Doctor 设计

### 16.1 setup

SparkGraph 必须进入正式 setup。

建议入口：

1. `hermes setup`
2. `hermes setup sparkgraph`

### 16.2 doctor/status/health

必须能看到：

1. db path
2. runtime provider/model
3. probe 结果
4. qualification 状态
5. degraded mode 状态

### 16.3 错误风格

必须像 Hermes 原生错误一样：

1. 有明确类别
2. 有可理解提示
3. 能指向恢复动作

---

## 17. 模型评测与准入设计

### 17.1 两套保障

SparkGraph 必须同时有：

1. 软件测试
2. 模型评测

### 17.2 baseline eval

第一版至少 100 条样本，覆盖：

1. 中文
2. 英文
3. 日文
4. 韩文
5. 法文
6. 德文
7. 混合语言

### 17.3 核心指标

1. durable precision
2. durable recall
3. ephemeral rejection precision
4. type accuracy
5. session-bound accuracy

### 17.4 初版门槛

1. durable precision >= 0.90
2. ephemeral rejection precision >= 0.92
3. type accuracy >= 0.85
4. session-bound accuracy >= 0.90
5. parse failure rate <= 1%
6. timeout rate <= 3%

### 17.5 模型状态

1. `unverified`
2. `qualified`
3. `restricted`

### 17.6 不合格模型限制

不合格模型只能运行：

1. shadow mode
2. 非关键辅助任务

不能负责：

1. 正式 durable classification
2. active promotion 关键判断

---

## 18. Shadow Mode 设计

### 18.1 目标

1. 跑完整候选流程
2. 记录结果
3. 不写 `active`
4. 不影响正式 recall

### 18.2 用途

1. 验证新模型
2. 观察脏数据率
3. 观察多语言表现
4. 在线调参

---

## 19. 集成点设计

### 19.1 允许修改的主线接入点

#### `run_agent.py`

只允许：

1. 初始化 SparkGraph manager
2. 调用 recall block 注入
3. turn-end 调度 extraction/classification
4. flush/session_end 调用 finalize

#### `gateway/run.py`

只允许：

1. 启动期 runtime/health/probe 接线
2. shutdown / flush / dream job 接线

#### `hermes_cli/setup.py`

只允许：

1. SparkGraph setup/reconfigure 子流程

#### `hermes_cli/config.py`

只允许：

1. 默认配置
2. migration/backfill

#### `hermes_cli/runtime_provider.py`

只允许：

1. SparkGraph runtime provider resolution

#### `gateway/status.py`

只允许：

1. SparkGraph health/status 展示

### 19.2 不应成为主线负担的逻辑

以下逻辑必须保持在 `agent/graph_memory/`：

1. extraction prompt/parse
2. classifier prompt/parse
3. scoring
4. dedup
5. recall ranking
6. maintenance decisions

---

## 20. 代码风格与接入要求

### 20.1 必须遵守

1. 与 Hermes 当前风格一致
2. 使用正式注册点
3. 不引入难维护旁路
4. 统一 error surface
5. 统一 config style

### 20.2 评审时必须问

1. 这段逻辑是否可以留在 core layer？
2. 为什么必须改 Hermes 主线文件？
3. 是否有更薄的桥接方式？

---

## 21. 测试设计

### 21.1 单测

模块级单测必须覆盖：

1. schema
2. store
3. classifier
4. dedup
5. recaller
6. formatter
7. maintenance
8. timeout/fallback

### 21.2 集成测试

必须覆盖：

1. recall 注入不污染 cached prompt
2. profile 路径隔离
3. embedding fallback
4. flush/finalize 不阻塞主链
5. setup/reconfigure/restore/migration

### 21.3 回归测试

每个 PR 必须声明要跑哪些既有 Hermes 回归测试。

---

## 22. 实施分期

### Phase 0

范围与设计冻结。

状态：

- 已完成

### Phase 1

最小可运行内核：

1. schema
2. store
3. config/runtime
4. extraction
5. classifier
6. dedup
7. recall block
8. 基础单测/集成测试

### Phase 2

正式配置与运行时：

1. setup
2. restore/migration
3. doctor/status/health
4. structured errors
5. degraded mode

### Phase 3

评测与准入：

1. baseline fixtures
2. qualification gate
3. shadow mode

### Phase 4

后台治理：

1. finalize
2. maintenance
3. internal dream

---

## 23. 已知缺陷与风险

这里不回避问题，当前明确存在这些风险：

### 23.1 评分规则尚未最终量化

虽然已确定评分维度，但具体阈值还没冻结。  
这意味着早期实现阶段必须保留调参空间。

### 23.2 小模型多语言能力可能极不均匀

即使总分合格，也可能在日语、韩语、德语上明显掉点。  
所以 qualification 必须看分语言结果，不能只看总分。

### 23.3 “durable knowledge” 的边界仍有灰区

再好的结构化分类，也仍会遇到模糊案例。  
因此必须保留 shadow mode 和人工 spot check。

### 23.4 与 Hermes review 的边界需要实现时再验证

SparkGraph 不直接写 curated memory，这是正确方向。  
但后续具体如何把高价值 graph node 输送给 Hermes review，还需要在实现中验证是否自然。

### 23.5 旧 `GM-*` Feature ID 命名仍未迁到 `SG-*`

当前还能用，但长期会造成命名污染。  
建议在 Phase 1 前半段完成 ID 迁移。

### 23.6 setup 文案和命令仍有历史命名残留风险

当前设计上已决定统一叫 SparkGraph，但部分早期历史文档仍保留旧名。  
实现时要特别避免把旧名重新带入 CLI/UI。

### 23.7 数据库 schema 仍需在实现前最终审查一次

当前 schema 已经足够进入编码设计，但在正式建库前仍需做一次索引与迁移审查。

---

## 24. 当前结论

SparkGraph 的正式设计已经达到可以进入工程实施准备的程度。

当前最重要的执行纪律是：

1. 只按本文边界做
2. 不把旧重型方案偷偷带回来
3. 不把核心逻辑散进 Hermes 主线
4. 不跳过模型评测与准入
5. 不省略 timeout / degraded / shadow mode

如果后续设计发生变化，必须优先修改本文，而不是只停留在聊天记录中。
