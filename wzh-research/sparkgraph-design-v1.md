# SparkGraph 软件设计（第一稿）

> 本文是对早期重型方案的收缩版重构。  
> 新共识：SparkGraph 不是第二套“全能记忆/上下文引擎”，而是 **Hermes 内部的 Durable Knowledge Graph Backend**。

> 配套评测设计：`wzh-research/sparkgraph-evaluation-design-v1.md`  
> 该文档给出了软件测试、模型评测、多语言基线、shadow mode、用户自定义小模型准入机制。

## 1. 新定位

SparkGraph 只做两件事：

1. 记录 **具体且有价值的 durable knowledge**
2. 在当前对话需要时，**准确召回相关知识点及其图关系邻域**

明确不做：

1. 不做 OpenClaw 式 context engine
2. 不做 visible transcript / compact / assemble 主链
3. 不做 Hermes skill 的替代系统
4. 不做“大而全”的第二套长期记忆操作系统
5. 不做依赖特定词组的多语言脆弱硬编码过滤

一句话：

**Hermes 负责方法和制度，SparkGraph 负责知识点和知识关系。**

---

## 2. 与 Hermes 的职责边界

### Hermes 继续负责

1. `SOUL.md`
2. `MEMORY.md`
3. `USER.md`
4. skills
5. `session_search`
6. cached system prompt
7. compression / flush / background review 主链

### SparkGraph 负责

1. durable knowledge candidate extraction
2. knowledge node persistence
3. semantic dedup / merge
4. relation graph construction
5. graph recall
6. lightweight graph maintenance
7. qualification-safe candidate pipeline

### 关键边界

1. **Skill 不进入 SparkGraph**
   - 过程性经验、SOP、工作流，全部交给 Hermes skills
2. **SparkGraph 不直接改写 Hermes curated memory**
   - 它只提供 durable knowledge backend
3. **SparkGraph 的 recall block 只进入动态层**
   - 不进入 cached system prompt
4. **未通过 qualification 的小模型，不得进入正式 automatic candidate pipeline**
   - 这类模型只能运行在 preflight、shadow mode 或 manual/explicit-only 辅助路径

## 2.1 源码级整合方式

由于 SparkGraph 不是纯外挂，而是对 Hermes 的源码级整合，所以实现方式必须遵循：

1. **核心逻辑独立**
   - DB、store、classifier、dedup、recall、maintenance 等应尽量集中在 `agent/graph_memory/` 内
2. **接线点正式**
   - prompt 动态注入、setup、runtime provider、health/status、flush/session_end 等必须在 Hermes 正式注册点接入
3. **主线改动薄**
   - `run_agent.py`、`gateway/run.py`、`hermes_cli/setup.py` 等文件只保留必要桥接逻辑
4. **升级友好**
   - 设计上优先让未来 Hermes 官方升级时，只需要处理接入层，而不是重写 SparkGraph 核心

一句话：

**SparkGraph 应该是“独立核心 + 官方接线点注册”，而不是“把核心逻辑揉进 Hermes 主线文件”。**

---

## 3. 为什么要收缩

从现有 `cat.db` 经验看，旧 graph-memory 的主要问题不是“没有价值”，而是“太重且太容易脏”：

1. reflection 过强，写入了很多会话态总结
2. `TASK/SKILL/EVENT` 三分法让大量内容被硬塞进 `SKILL`
3. 去重更接近“名字去重”，不是稳定的语义归一
4. active 图过大，低信号节点太多
5. recall 的目标不够聚焦

因此，新版本要改成：

1. **少记**
2. **只记知识点**
3. **关系简单但可靠**
4. **召回小而准**

---

## 4. 新数据模型

第一版建议只保留轻量节点类型：

1. `FACT`
   - 稳定事实、兼容性、配置事实、外部知识结论
2. `PREFERENCE`
   - 用户长期偏好、协作偏好、输出偏好
3. `ISSUE`
   - 有持续价值的问题模式、故障模式、踩坑模式
4. `RESOURCE`
   - 值得反复引用的工具、库、端点、文档、路径对象
5. `DECISION`
   - 做过且后续仍然有影响的长期决策

### 为什么不保留 `SKILL`

因为 Hermes 已经有更好的 skill 机制：

1. skill 是过程性记忆
2. skill 需要文档化、patch、references、scripts
3. graph 适合表达知识点，不适合承载 SOP 包

所以：

**SparkGraph 只保留知识点，不保留过程包。**

### 边类型建议只保留

1. `RELATED_TO`
2. `DEPENDS_ON`
3. `CONFLICTS_WITH`
4. `DERIVED_FROM`
5. `APPLIES_TO`

这是一个更轻、更稳的关系集。

---

## 5. 新写入原则

## 5.1 不做词组硬编码

不依赖：

1. 某个中文词
2. 某个英文短语
3. 某种固定句式

因为这会：

1. 多语言失效
2. 可迁移性差
3. 提示词一变就崩

### 替代方案

采用 **结构化语义判定**，而不是词组匹配。

小模型只需要回答这些语言无关问题：

1. 这条内容能否脱离当前轮独立成立？
2. 它是否对未来仍有复用价值？
3. 它是否依赖“当前会话/当前任务状态”？
4. 它更像 durable knowledge，还是 turn summary？
5. 它最接近哪类知识点？

---

## 5.2 三段式写入状态

新版本不允许“抽到就进正式图”。

节点状态建议改成：

1. `candidate`
2. `active`
3. `deprecated`

### `candidate`

表示：

1. 已通过基础解析
2. 但尚未被证明是高质量 durable knowledge

### `active`

表示：

1. 已通过升级条件
2. 可参与正式 recall

### `deprecated`

表示：

1. 旧知识
2. 被替代
3. 低质量降级

### 升级原则

只有满足以下条件之一，candidate 才能变 active：

1. explicit durable memory 请求
2. 多次独立会话/回合验证
3. 与其他 active 节点形成稳定关系支持
4. 被人工/工具明确确认
5. 被 Hermes review 判定为长期有价值

---

## 5.3 Reflection 降级为候选器

reflection 在 SparkGraph 中仍然保留，但职责大幅收缩：

1. 它只负责提名 durable knowledge candidate
2. 它不能直接生成 active durable node
3. 它不再负责“大段会话总结”

也就是说：

**reflection 是提名器，不是录用器。**

---

## 6. 决策流水线

## 6.1 写入流水线

```text
recent turns
  -> candidate extraction
  -> small-model structural classification
  -> semantic dedup / merge search
  -> score
  -> reject / candidate / active / merge / deprecate
```

### 第一步：candidate extraction

输入：

1. 最近窗口消息
2. 显式 durable memory 请求
3. 手工 graph_record

输出：

1. 候选知识点 summary
2. 候选 type
3. 可能关系
4. evidence span

约束：

1. 只有 `qualified` 小模型才允许进入正式 automatic extraction
2. `unverified` / `restricted` 模型下，只允许：
   - preflight
   - shadow mode
   - manual/explicit-only 辅助提取

### 第二步：small-model structural classification

小模型只输出结构，不写自然语言大报告。

输出示例：

```json
{
  "is_durable": true,
  "knowledge_type": "FACT",
  "needs_current_session": false,
  "reusability": 0.82,
  "stability": 0.77,
  "relation_hints": [
    {"type": "RELATED_TO", "target_query": "OpenAI client SOCKS proxy"}
  ]
}
```

约束：

1. 正式 classification 只允许 `qualified` 模型参与
2. `unverified` / `restricted` 模型不能成为正式 candidate -> active 流程的必要前提
3. 第一版必须显式支持：
   - `qualified mode`
   - `shadow mode`
   - `manual/explicit-only mode`

### 第三步：semantic dedup / merge search

不靠词组硬编码，靠：

1. FTS
2. embedding 近邻
3. 同 type 相似候选

### 第四步：score

建议评分项：

1. `durability_score`
2. `reuse_score`
3. `stability_score`
4. `support_score`
5. `source_score`
6. `session_bound_penalty`

### 第五步：落决策

可能结果：

1. `reject`
2. `candidate`
3. `promote_to_active`
4. `merge_into_existing`
5. `deprecate_existing`

---

## 7. Recall 设计

SparkGraph 的 recall 目标不是“多”，而是“准”。

## 7.1 Recall 输入

1. 当前用户消息
2. 最近一到两轮简要上下文
3. 显式 graph search 请求

## 7.2 Recall 过程

```text
query
  -> vector/FTS initial retrieval
  -> relation expansion (1 hop)
  -> candidate filtering
  -> score ranking
  -> budget trim
  -> graph recall block
```

## 7.3 Recall 过滤

必须过滤：

1. `candidate` 状态的低分节点
2. 低稳定、低支持节点
3. 已被明确 superseded/deprecated 的节点
4. 同类重复知识

## 7.4 Recall 输出

输出的不是 XML 大图，而是一个小而清晰的 recall block，例如：

```text
# SparkGraph Knowledge Recall
- FACT: 当前环境启用 SOCKS 代理时，httpx 需要 socksio 支持
- ISSUE: OpenAI client 初始化在 SOCKS 代理缺失依赖时会直接失败
- RELATED: 正式安装环境与源码工作区依赖可能不同步
```

---

## 8. 数据库简化建议

旧版表太重。Lite 版建议先缩成三张主表：

### `sg_nodes`

字段建议：

1. `id`
2. `type`
3. `summary`
4. `detail`
5. `status`
6. `confidence`
7. `stability`
8. `reuse_score`
9. `source_kind`
10. `created_at`
11. `updated_at`
12. `meta`

### `sg_edges`

字段建议：

1. `id`
2. `from_id`
3. `to_id`
4. `type`
5. `weight`
6. `meta`
7. `created_at`

### `sg_evidence`

字段建议：

1. `id`
2. `node_id`
3. `session_id`
4. `turn_index`
5. `source_hash`
6. `created_at`

### 可选扩展

第一版可以保留 `sg_vectors`，但社区表可以先不做，等 recall 质量稳定后再加。

---

## 9. 模型与性能策略

## 9.1 不走“全模型化”

Lite 版不应该让模型参与每个环节。

### 模型参与点

1. candidate extraction
2. structural classification
3. 少量 difficult merge arbitration

### 不建议模型参与

1. recall 排序主链
2. 每次查询后的重写格式化大段总结
3. 常规 maintenance 扫描

## 9.2 小模型负担如何减轻

通过这三点：

1. 不再做 skill 抽取
2. 不再做“大段反思总结”
3. 不再做复杂 community/全图分析作为主路径

这样本地小模型只需要做：

1. durable vs non-durable 判断
2. 知识点类型判断
3. 是否依赖当前会话的判断

## 9.3 qualification-first 原则

SparkGraph 不能建立在“用户提供的小模型默认可用”这个假设上。

第一版必须明确支持：

1. **qualified mode**
   - 小模型允许进入正式 candidate pipeline
2. **shadow mode**
   - 跑 extraction/classification，但不影响正式 active/recall
3. **manual/explicit-only mode**
   - 不依赖小模型自动提取，SparkGraph 仍能依靠显式输入、store、dedup、recall 正常工作

这意味着：

1. SparkGraph 的核心价值必须落在 schema、store、scoring、dedup、recall 上
2. 小模型只是增强层，而不是系统成立前提

这比旧 graph-memory 轻很多。

---

## 10. 测试与检验

## 10.1 我们检验什么

Lite 版重点不检验“抽了多少”，而检验：

1. durable knowledge precision
2. active recall usefulness
3. duplicate merge quality
4. low-value node suppression

## 10.2 测试层次

### 单测

验证：

1. DB schema
2. status transition
3. dedup / merge
4. recall ranking
5. budget trim

### fixture eval

构建一批真实 transcript，人工标注：

1. 应 reject 的
2. 应 candidate 的
3. 应 active 的
4. 应 merge 的
5. 应 recall 的

并额外验证：

1. `unverified` 模型不会被错误接入正式主链
2. `qualified` 与 `shadow` 两种模式下的系统质量差异可观测
3. `manual/explicit-only mode` 下 recall 与 store 主链仍能工作

### shadow mode

上线前先旁路运行：

1. 不写 active
2. 只记录候选
3. 对比召回质量和脏数据率

更完整的测试与评测体系见：

- `wzh-research/sparkgraph-evaluation-design-v1.md`

---

## 11. 对现有设计的影响

与旧版 `Hermes + graph-memory` 方案相比，Lite 版的关键变化是：

1. **删除 Skill 图谱职责**
2. **删除重型 context-engine 野心**
3. **删除依赖词组硬编码的过滤方案**
4. **把 graph 目标收缩为 durable knowledge backend**
5. **把 recall 目标收缩为“小而准”**

这更符合 Hermes 的架构气质，也更适合长期维护。

---

## 12. 当前建议

后续实施应以 SparkGraph 为准，优先做：

1. 模型 preflight 与 qualification 前置
2. 数据模型
3. candidate/active/deprecated 状态流
4. semantic dedup
5. recall block 注入
6. testing fixture、shadow mode、manual/explicit-only mode

而不是继续推进旧的重型 graph-memory 全量迁移方案。

说明：

1. 自动 extraction / classification 不应在 qualification 之前被视为已稳定能力
2. 如果目标小模型长期停留在 `unverified`，Phase 1 仍应允许 SparkGraph 以非自动模式落地
