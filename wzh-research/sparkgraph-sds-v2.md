# SparkGraph 正式软件设计说明书（SDS v2）

> 状态：Draft v2  
> 作用：本文件是 SparkGraph 的最新正式软件设计说明书。  
> 目标：用一份主设计文档，取代 v1 阶段分散且已部分过时的设计主线。  
> 适用范围：Hermes 源码级整合；不修改参考代码目录 `wzh-research/graph-memory/`。  
> supersedes：`wzh-research/sparkgraph-sds-v1.md` 及其对应的“前置分类器优先”主线设计。

## 1. 文档定位

本文件回答以下问题：

1. SparkGraph 在 v2 中究竟是什么，不是什么
2. 为什么要从“小模型前置分类”切换为“flush 集成式知识提取”
3. Hermes 当前对话后处理流程有哪些现成能力可以复用
4. SparkGraph v2 如何在不明显增加模型调用次数的前提下完成知识提取、入库、召回
5. 它的数据库、运行时、接入点、错误处理、测试与实施方式如何设计
6. 它有哪些优点、代价、风险与尚未解决的问题

本文是当前 SparkGraph 项目的主设计依据。

## 2. v2 结论总览

### 2.1 一句话定义

**SparkGraph 是 Hermes 内部的 Durable Knowledge Graph Backend，并在 v2 中采用 flush 集成式知识提取方案。**

### 2.2 v2 的核心变化

相对于 v1，v2 做了 4 个关键修正：

1. **不再把“小模型前置分类器”作为主入口**
2. **知识提取优先复用 Hermes 已有的大模型后处理调用**
3. **删除小模型旁路主线，只保留主模型 flush 抽取**
4. **SparkGraph 正式成为 Hermes 后处理阶段的知识沉淀后端，而不是独立高频抽取引擎**

### 2.3 只做两件事

1. 记录具体且有价值的 durable knowledge
2. 在需要时准确召回相关知识点及其关系邻域

### 2.4 明确不做

1. 不做 OpenClaw 式 context engine
2. 不做 visible transcript / assemble / compact 主链
3. 不做 Hermes skill 的替代系统
4. 不做第二套全能长期记忆操作系统
5. 不做依赖词组匹配的多语言硬编码判断
6. 不自行发明单 profile 内多 agent namespace
7. 不重写 Hermes cached system prompt 主骨架
8. 不让 SparkGraph 直接改写 `MEMORY.md` / `USER.md` / skills

## 3. 为什么 v1 需要被修正

### 3.1 v1 的主要问题

v1 主线一度把 SparkGraph 的主入口设计成：

1. 让小模型承担前置 durable/type/session 分类
2. 让小模型承担主链候选知识抽取
3. 再由规则层与图层做后处理

这个方向的问题已经在真实测试里暴露出来：

1. 0.8B 模型在分类题上明显不稳
2. 即使使用 choice/structured output，前置分类任务仍然对小模型过重
3. 如果源码级接入过度依赖小模型质量，SparkGraph 会变成 Hermes 主链的脆弱点
4. 这与 Hermes 的工程哲学冲突：Hermes 更适合低频、保守、强后处理，而不是高频、强依赖小模型判断

### 3.2 真实测试带来的修正

经过对原版 graph-memory 和多个本地模型的测试，得到以下更稳的结论：

1. 原版 graph-memory 更偏“抽取任务”，不偏“分类考试”
2. Hermes 当前已经自带后处理调用链，适合承载知识提取
3. 如果能让大模型在已有后处理调用里顺手提取知识点，就能显著降低架构风险
4. 保留小模型旁路会引入额外配置、评测与维护复杂度，当前版本不值得继续保留

### 3.3 v2 的设计立场

SparkGraph v2 的根本原则是：

**源码级接入不得建立在任何小模型持续高质量这一前提上。**

因此：

1. 主入口必须复用 Hermes 已有的强模型后处理链
2. 即使没有任何小模型旁路，SparkGraph 仍应成立

## 4. Hermes 当前对话后处理流程复盘

为了决定 SparkGraph 应接在哪，必须先准确理解 Hermes 现在对对话信息做了什么。

主要入口都在：

- [run_agent.py](/Users/wzh/IsacHermes/run_agent.py)

### 4.1 每轮开始前

Hermes 在每轮调用前，已经会对当前对话信息做这些准备：

1. 统计用户轮数与 memory nudge 计数
2. 预取 Honcho context
3. 构建或复用 cached system prompt
4. 必要时做 preflight compression
5. 调插件 `pre_llm_call` 给当前轮加临时上下文

这部分是“回合前上下文组装”，不是知识沉淀。

### 4.2 每轮执行中

Hermes 会持久化运行时信息：

1. 保存 session log
2. 增量写入 SQLite session DB
3. 持久化 assistant/tool messages
4. 统计 token、cost、cache hit

这部分是运行记录，不是知识抽取。

### 4.3 每轮结束后的 background review

这是 v2 最重要的复用点。

Hermes 在满足条件时，会在响应发出后启动后台 review agent：

1. `memory review`
   - 看是否有值得写入 `MEMORY.md` / `USER.md` 的偏好、纠错、行为要求
2. `skill review`
   - 看是否有值得新建或 patch 的 skill

特点：

1. 使用大模型
2. 已有完整的 review agent fork 机制
3. 读取整段 conversation snapshot
4. 不打断用户主回复
5. 只在阈值满足时触发，而不是每轮固定跑

对应逻辑：

- [run_agent.py:1550](/Users/wzh/IsacHermes/run_agent.py#L1550)
- [run_agent.py:1588](/Users/wzh/IsacHermes/run_agent.py#L1588)
- [run_agent.py:8268](/Users/wzh/IsacHermes/run_agent.py#L8268)

### 4.4 flush_memories

这是第二个关键复用点。

当上下文即将压缩、reset、退出时，Hermes 会：

1. 注入一条 flush 提示
2. 再调用一次模型
3. 只开放 `memory` 工具
4. 尝试把值得保留的信息写入 memory
5. 然后把 flush 痕迹从消息历史中移除

特点：

1. 这是“上下文丢失前的兜底保存”
2. 也是现成的大模型后处理调用
3. 天然适合作为 SparkGraph finalize / fallback 的入口

对应逻辑：

- [run_agent.py:5162](/Users/wzh/IsacHermes/run_agent.py#L5162)
- [run_agent.py:5331](/Users/wzh/IsacHermes/run_agent.py#L5331)

## 5. SparkGraph v2 的核心思路

### 5.1 新主入口

SparkGraph v2 的知识提取主入口不是“每轮独立小模型抽取”，而是：

1. **flush 集成式知识提取**
2. **flush 内的 finalize / merge / evidence append**

### 5.2 这意味着什么

SparkGraph v2 不再假设：

1. 每轮都自动长知识图
2. 小模型必须稳定判断 durable/type/session

SparkGraph v2 改为假设：

1. Hermes 已有的强模型后处理调用足以完成高质量知识抽取
2. 抽取频率可以更低，但质量应更高
3. 自动知识增长是一层“增益”，不是主链必需前提

### 5.3 设计目标

SparkGraph v2 要同时满足：

1. 尽量不新增模型调用
2. 不破坏 Hermes 主链
3. 不把系统正确性建立在小模型质量上
4. 仍然保留数据库、图谱、召回这些核心能力

## 6. v2 生命周期设计

### 6.1 主对话回合

普通对话回合中，SparkGraph 不主动发起独立模型调用。

默认流程：

```text
user turn
  -> Hermes main loop
  -> answer delivered
  -> no SparkGraph extraction this turn
  -> when context is about to be compressed/reset/exit:
       SparkGraph extraction/finalize runs inside flush
```

### 6.2 flush 集成式提取与 finalize

当 Hermes 触发 `flush_memories()` 时，SparkGraph 可参与两类工作：

1. 知识提取
   - 在上下文即将丢失前，通过扩展 flush prompt 完成 durable knowledge 提取
2. graph finalize / merge
   - 对已有 candidate 做 evidence append、merge、status update

注意：

1. v2 优先追求“不额外加调用”
2. 因此只选择 flush，不接 background review
3. flush 中的 SparkGraph finalize 可以先只做轻量处理

### 6.3 turns without flush

如果某一段对话在当前时刻没有触发 flush：

1. SparkGraph 不做自动知识抽取
2. Hermes 正常工作
3. 这不是错误，而是 v2 的有意取舍

这是 v2 的核心 tradeoff：

**以较低的抽取频率，换更低的架构风险和更高的抽取质量。**

## 7. 与 Hermes 的职责边界

### 7.1 Hermes 继续负责

1. `SOUL.md`
2. `MEMORY.md`
3. `USER.md`
4. skills
5. `session_search`
6. cached system prompt
7. compression / flush / review 主链
8. 最终制度化写入

### 7.2 SparkGraph 负责

1. durable knowledge candidate / active 节点管理
2. graph persistence
3. graph recall
4. semantic dedup / merge
5. lightweight maintenance
6. flush-integrated extraction bridging

### 7.3 明确不负责

1. skill 最终创建与 patch
2. curated memory 最终写入
3. 主 prompt 骨架控制
4. compression 主逻辑
5. Hermes 生命周期主调度权

### 7.4 统一定位：memory 的知识点补充库

SparkGraph 在 v2 中应被理解为：

1. `MEMORY.md / USER.md` 的结构化知识补充层
2. Hermes memory stack 的图谱面
3. curated memory 的补充库，而不是竞争库

这意味着：

1. curated memory 负责“少而精”
2. SparkGraph 负责“具体知识点与关系”
3. 两者应共用同一条后处理生命周期
4. SparkGraph 的整理/遗忘机制也应尽量与 memory flush 对齐

## 8. 命名空间与存储边界

SparkGraph 必须严格跟随 Hermes 官方组织方式：

1. 以当前 `HERMES_HOME` 为根
2. 以当前 profile 为 durable graph 存储边界
3. 不新增单 profile 内多长期 agent namespace

默认路径：

1. default profile
   - `~/.hermes/sparkgraph/default.db`
2. named profile
   - `~/.hermes/profiles/<name>/sparkgraph/default.db`

说明：

1. 这里正式统一命名为 `sparkgraph`
2. 历史文档中的 `graph_memory` 路径、变量与目录命名均视为过时设计

## 9. 数据模型

### 9.1 节点类型

第一版只保留：

1. `FACT`
2. `PREFERENCE`
3. `ISSUE`
4. `RESOURCE`
5. `DECISION`

### 9.2 边类型

第一版只保留：

1. `RELATED_TO`
2. `DEPENDS_ON`
3. `CONFLICTS_WITH`
4. `DERIVED_FROM`
5. `APPLIES_TO`

### 9.3 节点状态

第一版只保留：

1. `candidate`
2. `active`
3. `deprecated`

状态原则：

1. flush 抽取出来的节点默认写入 `candidate`
2. `active` 只能通过规则层升级
3. `deprecated` 用于替代、低质量或过时知识

### 9.4 数据库表

第一版保留 4 张核心表：

1. `sg_nodes`
2. `sg_edges`
3. `sg_evidence`
4. `sg_vectors`

辅助表：

1. `_migrations`
2. `sg_nodes_fts`

### 9.5 `sg_nodes`

建议字段：

1. `id`
2. `canonical_key`
3. `type`
4. `status`
5. `summary`
6. `detail`
7. `confidence`
8. `stability`
9. `reuse_score`
10. `source_kind`
11. `created_at`
12. `updated_at`
13. `last_recalled_at`
14. `meta_json`

### 9.6 `sg_edges`

建议字段：

1. `id`
2. `from_id`
3. `to_id`
4. `type`
5. `weight`
6. `source_kind`
7. `created_at`
8. `updated_at`
9. `meta_json`

### 9.7 `sg_evidence`

建议字段：

1. `id`
2. `node_id`
3. `session_id`
4. `turn_index`
5. `source_role`
6. `source_kind`
7. `evidence_text`
8. `created_at`
9. `meta_json`

### 9.8 `sg_vectors`

建议字段：

1. `node_id`
2. `embedding_model`
3. `dimensions`
4. `vector_blob`
5. `updated_at`

## 10. 模块划分

v2 正式统一实现包名为：

```text
agent/sparkgraph/
  __init__.py
  types.py
  config.py
  db.py
  store.py
  prompting.py
  dedup.py
  scoring.py
  recaller.py
  formatter.py
  maintenance.py
  manager.py
  runtime.py
  tools.py
```

说明：

1. 旧设计中出现的 `agent/graph_memory/*` 视为历史过渡命名
2. v2 正式采用 `agent/sparkgraph/*`

### 10.1 `prompting.py`

职责：

1. flush-integrated SparkGraph prompt 片段
2. 可选 lightweight finalize prompt 片段

### 10.2 `dedup.py`

职责：

1. canonical key 生成
2. FTS 近似候选搜索
3. embedding 近邻搜索
4. merge / reject 建议

### 10.3 `scoring.py`

职责：

1. confidence 计算
2. support / stability / reuse_score 计算
3. `candidate -> active` 判定
4. `active -> deprecated` 判定

### 10.4 `recaller.py`

职责：

1. query retrieval
2. FTS + vector search
3. one-hop expansion
4. filtering / ranking

### 10.5 `formatter.py`

职责：

1. recall block 格式化
2. 控制 token / char budget
3. 保证只进入动态层

### 10.6 `tools.py`

第一版至少提供：

1. `sparkgraph_record`
2. `sparkgraph_search`
3. `sparkgraph_stats`

其中：

1. `sparkgraph_record` 主要供 flush 流程调用
2. `sparkgraph_search` 可供显式工具查询使用
3. `sparkgraph_stats` 用于调试与诊断

## 11. 知识提取设计

### 11.1 主体原则

SparkGraph v2 的知识提取不是“每轮实时抽”，而是“在 Hermes 已决定进行后处理时，顺手完成知识提取”。

### 11.2 flush prompt 扩展

现有 `flush_memories()` 提示需要扩展成 v2 版，在不改变“memory 优先”前提下追加 SparkGraph 要求：

1. 只提 durable knowledge
2. 不提 skill 流程
3. 不提纯会话态内容
4. 不提寒暄
5. 不要把本轮未稳定的推测写入图
6. 只有确实值得未来召回的知识点才写入
7. 若无 durable knowledge，则不要调用 `sparkgraph_record`

### 11.3 `sparkgraph_record`

flush 过程中的模型若发现值得记录的知识点，调用：

```text
sparkgraph_record(
  items=[...],
  evidence_policy="append",
  source_kind="flush"
)
```

工具职责：

1. 校验输入
2. 规范化节点
3. 去重与合并
4. 写 evidence
5. 生成或更新关系
6. 计算初步分数

### 11.4 flush-only 的实现边界

Phase 1 默认不要求 flush 新增一次单独 SparkGraph 调用。  
flush 侧第一版优先做：

1. 轻量 finalize
2. evidence append
3. pending merge
4. 必要时兜底 record

## 11.5 flush 对齐的整理与遗忘机制

SparkGraph v2 的整理与遗忘机制，也应与 memory flush 放在同一条生命周期里。

### 写入阶段

在 `flush_memories()` 同一时机完成：

1. durable knowledge 提取
2. `sparkgraph_record`
3. evidence append
4. 基础 dedup

### 轻量整理阶段

在同一次 flush 后处理里顺手完成：

1. merge 明显重复 candidate
2. 更新 confidence / stability / reuse_score
3. 给新节点设置初始状态
4. 清理明显无效候选

### 轻量遗忘/降级阶段

也优先与 flush 对齐，而不是每轮运行：

1. 长期未命中的低信号 candidate 删除或降级
2. 被新知识替代的节点标记 `deprecated`
3. 孤立且低 evidence 的弱节点降级

### 明确暂缓

以下机制不进入 Phase 1：

1. dream 式重整理
2. 社区级重分析
3. 大规模全图重写

### 设计结论

SparkGraph 的整理/遗忘机制应当和 Hermes memory flush 放在一起，以保持：

1. 生命周期一致
2. 行为可解释
3. 实现边界清晰
4. “memory + SparkGraph” 的一体两面关系

## 12. 主模型与 embedding 在 v2 中的地位

### 12.1 主模型

主模型承担：

1. flush 阶段的 SparkGraph 知识提取
2. `sparkgraph_record` 的工具调用决策

### 12.2 embedding runtime

embedding runtime 只承担：

1. 向量召回
2. 语义近邻辅助

若 embedding 不可用：

1. Recall 自动降级到 FTS5
2. 不影响 Hermes 主对话

## 13. Recall 设计

Recall 是 SparkGraph 的另一半核心，v2 保留并强化 v1 的受控设计。

### 13.1 触发时机

SparkGraph recall 只在：

1. 每轮模型调用前
2. 当前 query 有意义时
3. SparkGraph runtime 健康时

进行动态注入。

### 13.2 不允许

1. 不允许写入 cached system prompt
2. 不允许污染持久化消息历史
3. 不允许把大量图谱内容倾倒进 prompt

### 13.3 流水线

```text
query
  -> vector/FTS retrieval
  -> one-hop expansion
  -> active-only filtering
  -> rank
  -> budget trim
  -> dynamic recall block
```

### 13.4 默认限制

第一版建议保留以下限制：

1. FTS `top_k = 8`
2. vector `top_k = 8`
3. one-hop expansion only
4. `max_related = 4`
5. 最终注入节点数 `<= 4`
6. 同类型上限 `<= 2`
7. 优先使用 `graph_budget_ratio` 和 `max_chars` 双重限制

### 13.5 recall block 格式

建议简短、稳定：

```text
[SparkGraph Recall]
- FACT: ...
- PREFERENCE: ...
- ISSUE: ...
```

只放：

1. 高 confidence
2. 高 stability
3. 与当前 query 直接相关

## 14. 配置设计

v2 顶层配置块统一为：

```yaml
sparkgraph:
  mode: flush_integrated
  db_path: ""
  recall:
    enabled: true
    max_items: 4
    max_related: 4
    budget_ratio: 0.12
    max_chars: 1800
  embedding:
    provider: ...
    model: ...
    base_url: ...
    api_key: ...
    timeout: 20
```

### 14.1 设计说明

1. v2 不再要求配置一个“正式 extraction 模型”
2. 主 extraction 默认复用 Hermes 主模型的 flush 调用
3. embedding runtime 仍是正式能力

## 15. 运行时与错误处理

### 15.1 主运行时

SparkGraph v2 依赖两种运行时：

1. Hermes 主模型运行时
   - 用于 flush-integrated extraction
2. SparkGraph embedding runtime
   - 用于向量召回

### 15.2 错误处理原则

1. SparkGraph 任意错误不得阻塞主回复
2. flush-integrated extraction 失败时：
   - 记录日志
   - 不写库
   - Hermes 正常继续
3. recall 失败时：
   - 当前轮跳过 recall block
4. embedding 失败时：
   - 回退到 FTS only

### 15.3 结构化错误

建议引入：

1. `SPARKGRAPH_DB_ERROR`
2. `SPARKGRAPH_EMBEDDING_ERROR`
3. `SPARKGRAPH_RECALL_DEGRADED`
4. `SPARKGRAPH_REVIEW_PARSE_ERROR`
5. `SPARKGRAPH_RUNTIME_TIMEOUT`

## 16. Hermes 接入点

### 16.1 必须接入的位置

1. `run_agent.py`
   - flush prompt / tools 扩展
   - pre-LLM recall block 注入
   - flush finalize 接线
2. `hermes_cli/setup.py`
   - SparkGraph config/runtime probe
3. `hermes_cli/config.py`
   - `sparkgraph` 顶层配置支持
4. `gateway/status.py`
   - SparkGraph health / degraded state
5. `tools/__init__.py`
   - 注册 SparkGraph 工具

### 16.2 绝对禁止

1. 不把 SparkGraph 核心逻辑直接写进 `run_agent.py`
2. 不复制散落判断逻辑到多个主线文件
3. 不通过 monkey patch 接入

## 17. 测试设计

### 17.1 软件测试

必须覆盖：

1. schema / migration / CRUD
2. dedup / scoring / promotion
3. recall filtering / budget trim
4. flush-integrated extraction bridge
5. flush finalize bridge
6. degraded fallback

### 17.2 模型评测

v2 的模型评测分两类：

1. **主链评测**
   - 主模型在 flush prompt 下是否能稳定提取 durable knowledge
2. **检索降级评测**
   - embedding 不可用时，FTS5-only recall 是否保持安全与可解释

### 17.3 最关键的新测试

必须新增：

1. `flush-integrated extraction does not add extra calls beyond flush`
2. `sparkgraph extraction failure does not break user response`
3. `flush finalize does not leak prompt artifacts`
4. `recall block never enters cached system prompt`

## 18. 实施计划调整

相对 v1，实施顺序需要调整：

### Phase 1

1. SparkGraph core package
2. schema/store
3. recall
4. `sparkgraph_record`
5. flush-integrated bridge
6. flush finalize bridge
7. embedding runtime / degraded fallback
8. 测试与 smoke

### 延后项

1. 任何小模型旁路正式接线
2. 更复杂的 maintenance / dream
3. setup UI 深度打磨
4. 高级 graph analytics

## 19. 已知 tradeoff 与缺陷

v2 不是没有代价，以下问题必须诚实保留：

1. **知识增长频率下降**
   - 因为主入口依赖 flush，而非每轮抽取
2. **coverage 受 flush 触发条件影响**
   - 某些对话在压缩/reset/退出前不会立即增长
3. **flush prompt 更复杂**
   - memory 与 graph 两种沉淀共用一条后处理链，提示设计必须足够谨慎
4. **对主模型质量仍有依赖**
   - 只是比依赖小模型风险小得多
5. **当前不保留小模型旁路**
   - 若未来要重新引入，必须重新审批

## 20. v2 最终结论

SparkGraph v2 的核心结论可以压成一句话：

**保留数据库、去重、评分、召回这些图谱核心能力；把知识提取主入口迁移到 Hermes 已有的 flush 后处理链上；主链只依赖主模型 flush 抽取与 embedding/FTS5 recall，不保留小模型旁路。**

这条路线比 v1 更符合：

1. Hermes 的工程哲学
2. 上下文可控性要求
3. 源码级整合的稳定性要求
4. 当前真实模型能力边界
