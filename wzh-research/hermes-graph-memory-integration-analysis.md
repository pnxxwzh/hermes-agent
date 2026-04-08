# Hermes 与 graph-memory 插件整合研究

> 配套实施设计：`wzh-research/hermes-graph-memory-software-design-v1.md`  
> 该文档给出了第一稿工程落地方案，包含模块拆分、逐文件改动位置、分阶段实施与测试矩阵。

> 最新收缩设计：`wzh-research/sparkgraph-lite-design-v1.md`  
> 当前最新共识已经从“重型 graph-memory 整体整合”收缩为 `SparkGraph Lite`：只做 durable knowledge graph backend，不再迁移 skill 图谱和 OpenClaw 风格 context-engine。

## 1. 研究范围

本文基于以下两部分源码做逐层比对：

- Hermes 主仓库
  - `run_agent.py`
  - `agent/prompt_builder.py`
  - `agent/context_compressor.py`
  - `agent/context_references.py`
  - `tools/memory_tool.py`
  - `tools/session_search_tool.py`
  - `tools/skill_manager_tool.py`
- 本地导入的 graph-memory 插件
  - `wzh-research/graph-memory/index.ts`
  - `wzh-research/graph-memory/src/**/*`

目标不是泛泛比较“谁更强”，而是精确回答四件事：

1. graph-memory 实际做了哪些工作，细到写入、召回、组装、维护每一步。
2. 这些能力与 Hermes 的哪些层冲突、重复、互补。
3. 两边的流程、决策树、节点职责分别是什么，优劣在哪里。
4. 如果要整合，怎样既吸收 graph-memory 的价值，又不破坏 Hermes 的上下文可控性。

---

## 2. 一句话结论

graph-memory 不是单纯的“记忆检索插件”，而是一个完整的 **知识图谱上下文引擎**：

1. 它把消息原文落到 `gm_messages`。
2. 每轮 `afterTurn` 并发跑三条写入线：自动抽取、显式记忆、自反思。
3. 通过向量/FTS5、社区扩展、图遍历、PPR、Recall Pool 形成持续召回。
4. 在 `assemble()` 阶段把图谱 XML、图谱使用说明、可见 transcript 一起拼进上下文。
5. 在 `session_end` 做 finalize、去重、PageRank、社区检测、社区摘要。

Hermes 的主线则完全不同：它是一个 **稳定 system prompt + 按需动态补层** 的上下文操作系统。  
它最强的是：分层清楚、缓存稳定、工程边界清晰、skills 过程性记忆成熟。

所以整合时最关键的判断是：

- **不能让 graph-memory 接管 Hermes 的 system prompt 主骨架**
- **应该把 graph-memory 放成 Hermes 的“受控 recall / durable knowledge backend”**
- **让 graph-memory 只贡献动态 recall 层，不直接污染 Hermes 的 frozen memory 和 skill 索引**

---

## 3. graph-memory 做了什么

### 3.1 总体模块图

graph-memory 实际包含 7 个层：

1. **存储层**
   - SQLite：`gm_nodes` / `gm_edges` / `gm_messages` / `gm_vectors` / `gm_communities`
2. **写入层**
   - `runTurnExtract()`
   - `runExplicitMemory()`
   - `selfReflect()`
   - `gm_record`
3. **召回层**
   - `Recaller.recall()`
   - precise 路径
   - generalized 路径
4. **池化层**
   - `RecallPool`
   - 每 session 一个 recall pool
5. **上下文组装层**
   - `buildSessionContextLayers()`
   - `assembleContext()`
   - `systemPromptAddition`
6. **上下文卫生层**
   - oversized tool result 摘要
   - tool use/result pairing repair
7. **维护层**
   - finalize
   - dedup
   - global PageRank
   - community detection
   - community summaries

### 3.2 数据模型

#### 节点

节点类型固定为：

- `TASK`
- `SKILL`
- `EVENT`

节点还带这些关键属性：

- `memoryClass`: `episodic` / `semantic`
- `sourceKind`: `auto` / `explicit` / `manual` / `reflection`
- `validatedCount`
- `communityId`
- `pagerank`
- `confidence`
- `supersededBy`
- `defaultInject`
- `meta`

这意味着 graph-memory 不只是“存文本”，而是在存一组带治理属性的知识单元。

#### 边

边类型固定为：

- `USED_SKILL`
- `SOLVED_BY`
- `REQUIRES`
- `PATCHES`
- `CONFLICTS_WITH`

边也带：

- `instruction`
- `condition`
- `confidence`
- `meta`

也就是说，它把“任务-方法-问题-替代关系”当成一等公民建模，而不是只靠自由文本描述。

### 3.3 DB 与 domain 隔离

`src/store/db.ts` 说明 graph-memory 的 DB 不是单例全局库，而是按 workspace / agent domain fork：

- 基础库：`data/base.db`
- 运行库：`{workspace}/graph memory/{agentId}.db`

关键特点：

1. 支持 per-agent DB fork。
2. subagent 默认复用父 agent DB。
3. 有“current db / current domain”运行时概念。

这说明 graph-memory 从设计上已经在做“记忆隔离”，不是一个简单公共记忆池。

---

## 4. graph-memory 的完整流程

## 4.1 启动阶段：`before_agent_start`

入口：`index.ts` 的 `api.on("before_agent_start", ...)`

流程：

1. 判断是否是 subagent。
2. 为主 agent 打开或 fork 当前 workspace/agent 的 DB。
3. 重建 `Recaller`。
4. 用初始 prompt 做一次 `recaller.recall(prompt)`。
5. 召回结果既放进 `recalled` map，也 seed 进 `RecallPool`。

目的：

- 对话一开始就把与首个 query 相关的 durable knowledge 放进后续 assemble 可用层。

### 4.1.1 这里的决策树

```text
before_agent_start
  ├─ 是 subagent? → 跳过独立 DB 初始化，沿用父 DB
  └─ 否
      ├─ 打开/创建 domain DB
      ├─ 清洗 prompt
      ├─ prompt 为空或像 /new /reset? → 不 recall
      └─ recall(prompt)
          ├─ recall 命中? → seed recallPool
          └─ 未命中 → 空启动
```

---

## 4.2 消息落库阶段：`ingest()` / `afterTurn()`

graph-memory 把消息原文先写入 `gm_messages`，而不是直接只保留结构化节点。

写入关键点：

- 有 `message_key + content_hash` 去重
- 有 `sourcePhase`
- 有 `extract_status`
- 有 `consumed_by_explicit`

这说明原始消息被当作：

1. 提取失败时的恢复来源
2. 显式记忆窗口的素材
3. finalize 的基础数据

---

## 4.3 每轮写入阶段：`afterTurn()`

这是 graph-memory 的核心。

在每轮结束后，它并发跑四件事：

1. `runExplicitMemory()`
2. `runTurnExtract()`
3. `selfReflect()`
4. `recallPool.recallTurn()`

### 4.3.1 自动抽取：`runTurnExtract()`

入口：

- 读取当前 session 尚未提取的消息 `getUnextracted()`
- 调用 `Extractor.extract()`
- 将结果写成节点和边
- 标记消息为 `extracted`
- 如有必要回填 pending TASK 的结果段

`Extractor` 的 system prompt 做了非常激进的结构化要求：

- 抽 `TASK` / `SKILL` / `EVENT`
- 边只允许 5 种
- 方向有严格约束
- 讨论、分析、对比也要尽量抽
- 用户纠正旧方案时要求补 `PATCHES`

还做了几层后处理：

1. 节点类型合法性校验
2. name 规范化
3. 边方向自动纠正
4. 非法边丢弃

这条线的本质是：

- **默认每轮都尝试把最近对话变成图谱知识**

### 4.3.2 显式记忆：`runExplicitMemory()`

触发条件不是工具调用，而是检测用户短语：

- “记住这个”
- “以后都按这个来”
- “这个很重要”
- 等

流程：

1. 找 trigger message
2. 从 `gm_messages` 选择一个 explicit window
3. 用 LLM 把窗口压成最多 3 条 durable candidate
4. 过滤掉低置信度/字段不完整候选
5. `persistExplicitCandidates()` 写入图谱
6. 标记窗口消息已被 explicit 消费

这条线的本质是：

- **把“用户明确要求记住”的片段升级成 durable semantic memory**

### 4.3.3 自反思：`selfReflect()`

这条线完全不同于 explicit。

它不是用户触发，而是每轮模型自己判断：

- 本轮是否学到值得跨 session 记住的知识

输出格式非常小：

- `type`: `skill|rule|preference|null`
- `content`

随后会被写成：

- `preference` → `EVENT`
- 其他 → `SKILL`
- `sourceKind="reflection"`
- `memoryClass="semantic"`

并给 reflection 节点一个额外的 ranking 补偿。

这条线的本质是：

- **让系统在没人说“记住”的情况下，仍能自发产出 durable memory**

### 4.3.3.1 你提到的 reflect 机制，本质上就是“每轮后的总结/反思机制”

如果按你们平时的使用感受来描述，`selfReflect()` 可以直接理解成：

- **每一轮对话结束后，系统都会额外跑一个小型复盘**
- 它不是继续回答用户，而是在判断：
  - 这一轮有没有值得长期记住的知识
  - 这次有没有学到一个方法、规则、偏好
  - 这些内容是否应该沉淀为 durable memory

对应源码就是：

- [index.ts](/Users/wzh/IsacHermes/wzh-research/graph-memory/index.ts)
  - `afterTurn()` 中并发调用 `selfReflect(...)`
- [selfReflect.ts](/Users/wzh/IsacHermes/wzh-research/graph-memory/src/engine/selfReflect.ts)

它的工作方式很具体：

1. 取最近约 10 条消息，压成一个简短 transcript。
2. 用专门的 reflection prompt 问模型：
   - “这轮有没有学到 skill / rule / preference？”
3. 如果答案不是空，就写入图数据库。
4. 写入时标记：
   - `sourceKind="reflection"`
   - `memoryClass="semantic"`
5. 后续 recall 排序时，如果 reflection 节点图结构较孤立，还会给一个额外补偿，避免它因为图边少而永远召不回来。

所以这个 reflect 机制不是 session 结束时才跑一次，而是：

- **默认每轮 `afterTurn` 都会尝试跑一次**

这和 `session_end finalize` 不一样：

- `selfReflect()`：每轮后的即时反思
- `finalize()`：整个 session 结束后的全局复盘

可以把两者理解成：

- `selfReflect()` = turn-level reflection
- `finalize()` = session-level reflection

### 4.3.4 Recall Pool 更新：`recallPool.recallTurn()`

Recall Pool 不是简单缓存，而是一个 session 内滚动记忆池。

流程：

1. 用本轮最后几条消息拼 `turn query`
2. 用这个 query 跑 `recaller.recall()`
3. 命中的节点入池
4. 已有节点 hitCount++
5. 池满时用 LRFU 驱逐

参数：

- 池容量：12
- 分数：`0.4 * hitCount + 0.6 * recencyDecay`

这条线的本质是：

- **让 session 中途的新主题，也能把相关知识拉进工作记忆场**

### 4.3.5 `afterTurn()` 决策树

```text
afterTurn
  ├─ 写新消息到 gm_messages
  ├─ 用户最后一条命中显式记忆触发词?
  │   ├─ 是 → summarize window → persist explicit
  │   └─ 否 → 跳过
  ├─ 当前 session 有未提取消息?
  │   ├─ 是 → extractor 提取 nodes/edges → 落库
  │   └─ 否 → 跳过
  ├─ 自反思开启?
  │   ├─ 是 → selfReflect → 可能写 reflection node
  │   └─ 否 → 跳过
  └─ recallTurn
      ├─ 构建 turn query
      ├─ 召回
      └─ pool 更新 / 驱逐
```

---

## 4.4 召回阶段：`Recaller.recall()`

`Recaller` 不是单路径搜索，而是双路径召回后合并。

### 4.4.1 precise 路径

流程：

1. 向量搜；不足则补 FTS5
2. 拿到 seed nodes
3. 社区扩展：每个 seed 拉少量 community peers
4. 图遍历：`graphWalk()`
5. personalized PageRank 排序
6. 取 top-N 节点和其内部边

适合：

- 找和当前 query 直接相关的具体知识

### 4.4.2 generalized 路径

流程：

1. 如果有社区 embedding，先做 community vector search
2. 否则拿社区代表节点
3. 一跳图遍历
4. personalized PageRank 排序

适合：

- 补足 precise 路径漏掉的知识域

### 4.4.3 合并与排序

合并逻辑：

1. precise 全保留
2. generalized 去重后补充
3. 最终按 `nodePriority()` 排序

排序权重里最值得注意的是：

- `semantic` memory 加权
- `explicit` 加权
- `manual` 加权
- `reflection` 在孤立图得分低时给强补偿
- `validatedCount`
- `confidence`
- `supersededBy` 惩罚

这意味着 graph-memory 的召回不是“相似度直接排序”，而是：

- **语义相关性 + 图结构邻近性 + 记忆来源可信度 + 节点成熟度** 的混合排序

### 4.4.4 recall 决策树

```text
recall(query)
  ├─ precise(query)
  │   ├─ vector search 可用?
  │   │   ├─ 是 → 向量搜
  │   │   └─ 否 → FTS
  │   ├─ 种子不足? → FTS 补齐
  │   ├─ 社区扩展
  │   ├─ 图遍历
  │   └─ PPR 排序
  ├─ generalized(query)
  │   ├─ 有社区 embedding?
  │   │   ├─ 是 → 社区向量召回
  │   │   └─ 否 → 社区代表节点
  │   ├─ 图遍历
  │   └─ PPR 排序
  └─ merge(precise, generalized)
```

---

## 4.5 上下文组装阶段：`assemble()`

这是 graph-memory 最容易与 Hermes 冲突的地方。

流程：

1. `buildSessionContextLayers(messages, cfg)`
   - 最近若干 user turn 保留完整消息
   - 更老的历史压成 `visibleTranscript`
2. 拿 `activeNodes`
   - 当前 session 写入的节点
3. 拿 `recallPool.get(sessionId)`
   - session 内池化 recalled nodes
4. `assembleContext()`
   - active/recalled 去重
   - active 优先于 recalled
   - `SKILL > TASK > EVENT`
   - `validatedCount` / `pagerank` 再排序
   - 控制 graph token budget
   - 只保留 surviving nodes 之间的边
   - 按 community 分组渲染成 `<kg>`
5. 再叠加：
   - graph 使用说明 `systemPrompt`
   - `<kg>` XML
   - `visibleTranscript`
6. 对 `recentMessages` 做：
   - oversized tool result 摘要
   - tool use/result pairing 修复
7. 最终返回：
   - `messages`
   - `estimatedTokens`
   - `systemPromptAddition`

### 4.5.1 graph-memory 最终给模型的结构

```text
Base Prompt (OpenClaw 原生)
+ Graph System Prompt
+ <kg> XML
+ Visible Transcript
+ Recent Messages
```

### 4.5.2 这一步的核心特征

1. 它不是固定 system prompt。
2. 它每轮都会重新 assemble。
3. durable memory 不只是 recall，一部分 old transcript 也被折成 visible layer 带入。
4. graph-memory 实际承担了“上下文管理器”角色，而不只是“记忆检索器”。

---

## 4.6 结束阶段：`session_end`

`session_end` 又做两件事：

1. `extractor.finalize()`
   - EVENT 是否升级为 SKILL
   - 本轮是否补新边
   - 是否要 invalidation 旧节点
2. `runMaintenance()`
   - dedup
   - global PageRank
   - community detection
   - community summaries

### 4.6.1 finalize 决策树

```text
session_end
  ├─ 本 session 有节点?
  │   ├─ 否 → 跳过 finalize
  │   └─ 是
  │       ├─ EVENT 具通用复用价值? → promotedSkills
  │       ├─ 跨节点关系有遗漏? → newEdges
  │       └─ 某旧节点被本轮知识取代? → invalidations
  └─ maintenance
      ├─ dedup
      ├─ global PageRank
      ├─ communities
      └─ community summaries
```

### 4.6.2 维护层的价值

这是 graph-memory 和普通 vector memory 最大不同点之一。

它不是“写了就不管”，而是有持续治理：

- 相似节点合并
- 全局重要度重算
- 知识域聚类
- 聚类摘要

所以 graph-memory 真正做的是：

- **知识图谱的写入、排序、聚类、衰减、去重、再组装**

---

## 5. Hermes 的对应流程

为便于整合，必须把 Hermes 画成同样颗粒度。

## 5.1 Hermes 启动阶段

Hermes 在 agent 初始化时做的事：

1. 读取配置。
2. 初始化 `MemoryStore`。
3. 读取 `MEMORY.md` / `USER.md` 并做 frozen snapshot。
4. 视配置启用 Honcho。
5. 准备 skills 索引。
6. `_build_system_prompt()` 构造稳定 system prompt 骨架。

关键点：

- system prompt 在 session 内尽量不变。
- frozen memory 在本 session 内不回流更新。

## 5.2 Hermes 每轮调用前

真实调用前再补动态层：

1. `ephemeral_system_prompt`
2. 插件 `pre_llm_call` context
3. `@file/@diff/@url` 展开
4. Honcho turn recall
5. 用户当前消息和历史 messages

Hermes 的结构是：

```text
Stable System Prompt
+ Ephemeral Prompt
+ Plugin Turn Context
+ Honcho Turn Context
+ Message History
+ Current User Message
```

## 5.3 Hermes 写入阶段

Hermes 不会每轮自动抽知识图谱。

它主要靠：

1. `memory` 工具
   - 小容量 curated facts
2. `skill_manage`
   - 过程性记忆
3. `session_search`
   - 大历史按需搜索
4. background review
   - 某些轮结束后由后台 review agent 做 memory/skill 沉淀
5. flush memories
   - session 即将 reset/expiry 时兜底整理

也就是说，Hermes 的写入核心是：

- **显式沉淀**
- **review 时机驱动**
- **面向 facts / SOP，而不是面向 typed KG**

## 5.4 Hermes 压缩与卫生

Hermes 有自己成熟的消息层卫生：

1. `ContextCompressor`
2. gateway session hygiene
3. `@` 引用预算控制
4. 工具消息保护/裁剪

这点在整合时特别关键，因为 graph-memory 也有 visible transcript、tool result summarization、pairing repair。

---

## 6. 对照：冲突、重复、独特价值

下表不是“优劣榜”，而是整合视角下的职责对照。

| 维度 | graph-memory | Hermes | 关系判断 |
|---|---|---|---|
| 稳定系统提示词 | 弱 | 强 | graph-memory 不应接管 |
| 每轮自动知识写入 | 强 | 弱 | graph-memory 可补 Hermes 短板 |
| 结构化知识关系 | 强 | 弱 | graph-memory 独特价值 |
| 事实型长期记忆 | 中 | 强 | 部分重复，Hermes 更可控 |
| 过程性记忆 / SOP | 中 | 强 | Hermes 更成熟 |
| 历史 transcript 检索 | 弱到中 | 强 | Hermes `session_search` 更合适 |
| 上下文压缩治理 | 中 | 强 | Hermes 主导更稳 |
| 社区/图治理 | 强 | 无 | graph-memory 独特价值 |
| prompt cache 友好 | 弱 | 强 | Hermes 核心优势 |
| session 内 recall 演化 | 强 | 中 | graph-memory 独特价值 |
| 维护复杂度 | 高 | 中 | graph-memory 成本更高 |

### 6.1 明显重复的层

#### A. 历史裁剪与 transcript 层

graph-memory 有：

- `visibleTranscript`
- tool result summary
- transcript repair

Hermes 有：

- `ContextCompressor`
- gateway hygiene
- own message history handling

结论：

- **这部分功能不能双重启用**
- 如果整合到 Hermes，消息层压缩必须仍由 Hermes 负责
- graph-memory 的 transcript 组装层应大幅弱化或去除

#### B. 用户偏好/长期事实层

graph-memory 通过 explicit/reflection/manual 也会写 preference/event 类节点。  
Hermes 则已有：

- `USER.md`
- `MEMORY.md`
- Honcho

结论：

- 这里存在概念重叠
- graph-memory 不应直接替代 Hermes 的 user/local memory
- 更适合作为“候选 durable knowledge 层”，由 Hermes 决定是否升级到 memory

### 6.2 明显冲突的层

#### A. 上下文主导权冲突

graph-memory 在 OpenClaw 中实际是 `contextEngine`，能主导：

- assemble
- compact
- visible transcript
- systemPromptAddition

Hermes 已有完整 prompt assembly 体系。

结论：

- **graph-memory 不能以 contextEngine 姿态平移进 Hermes**
- 否则会破坏 Hermes 的 cached system prompt 设计

#### B. 自动写入 aggressiveness 冲突

graph-memory：

- 每轮 afterTurn 默认并发写库

Hermes：

- 强调 memory/skill 精炼、克制、显式

结论：

- 不能把 graph-memory 自动产物直接回灌 `MEMORY.md` 或 skills
- 否则会破坏 Hermes curated memory 的纯度

### 6.3 真正有独特价值的层

#### A. 关系型 durable knowledge

Hermes 没有：

- `TASK ↔ SKILL ↔ EVENT`
- `REQUIRES/PATCHES/CONFLICTS_WITH`
- 社区
- PPR

这是 graph-memory 最大的独特价值。

#### B. session 内 recall pool

Hermes 的 recall 更偏：

- frozen local memory
- 按需 `session_search`
- Honcho turn recall

但缺少一个明确的 **session 内持续演化的 recall pool**。

这也是 graph-memory 的独特价值。

#### C. session_end 图治理

Hermes 会做 background review / flush，但不会：

- dedup semantic duplicates
- recompute graph centrality
- detect communities

所以这也是可以借用的独立价值层。

---

## 7. 两套系统的决策树

## 7.1 graph-memory 决策树

```text
新 prompt 到来
  ├─ before_agent_start recall?
  │   ├─ 命中 → seed recall pool
  │   └─ 未命中 → 空
  ├─ 对话进行
  │   └─ afterTurn
  │       ├─ 有显式 trigger? → 显式记忆窗口摘要 → 写 explicit
  │       ├─ 有未提取消息? → extractor → 写 auto nodes/edges
  │       ├─ selfReflection enabled? → 每轮 reflect / turn summary → 写 reflection
  │       └─ recallTurn → 更新 recall pool
  ├─ assemble
  │   ├─ recent messages
  │   ├─ visible transcript
  │   ├─ active nodes
  │   └─ recall pool nodes
  │       → graph XML + prompt addition
  └─ session_end
      ├─ finalize
      └─ maintenance
```

## 7.2 Hermes 决策树

```text
新 session / 继续 session
  ├─ 读取 frozen local memory
  ├─ 初始化 Honcho / skills / context files
  └─ 构造或复用 cached system prompt

每轮调用前
  ├─ 追加 ephemeral prompt
  ├─ 追加 plugin context
  ├─ 追加 @ 引用展开
  ├─ 追加 Honcho turn recall
  └─ 准备 messages
      ├─ 若过长 → compressor
      └─ 发模型

每轮结束后
  ├─ 是否到 background review 阈值?
  │   ├─ 是 → 后台 review agent 沉淀 memory/skills
  │   └─ 否 → 跳过
  └─ session 即将 reset / expiry?
      ├─ 是 → flush memories/skills
      └─ 否 → 正常结束
```

### 7.3 最核心的哲学区别

graph-memory：

- **先持续长记忆，再把记忆重新 assemble 进上下文**

Hermes：

- **先稳定住上下文骨架，再按需把记忆/工具/引用补进去**

---

## 8. 节点职责、作用、优劣

## 8.1 graph-memory 三类节点

### `TASK`

作用：

- 表达用户要完成的目标、分析主题、对比议题

优势：

- 能把“讨论和执行主题”显式结构化
- 便于之后把任务与技能、事件连起来

劣势：

- 生命周期通常短
- 在长期 durable memory 中容易积累噪声
- 如果自动抽取过宽，会把大量一次性主题都沉淀下来

### `SKILL`

作用：

- 表达可复用方法

优势：

- 比普通 note 更接近 procedure
- 能挂 `REQUIRES/PATCHES/CONFLICTS_WITH`

劣势：

- 仍然只是图节点文本，不等于 Hermes 的完整 skill package
- 缺少 `references/`, `scripts/`, `templates/` 这类过程性载体

### `EVENT`

作用：

- 表达报错、约束、偏好、一次性现象

优势：

- 很适合承载错误模式、环境异常、偏好信号

劣势：

- 容易膨胀
- “偏好”和“故障事件”同放 EVENT，会让语义层次变杂

## 8.2 Hermes 对应概念

Hermes 不是 node graph，但有功能等价层：

| graph-memory | Hermes 对应 |
|---|---|
| TASK | session history / current task state |
| SKILL node | Hermes skills |
| EVENT | MEMORY.md / USER.md / session history |
| sourceKind | memory/skills/session_search 来源差异 |
| community | 无直接对应 |
| pagerank | 无直接对应 |

### 8.2.1 谁更适合承载“技能”

- graph-memory `SKILL`：适合图检索和关系建模
- Hermes skills：适合真正执行和长期维护

结论：

- graph-memory `SKILL` 更像 “knowledge about a procedure”
- Hermes skill 更像 “runnable procedural package”

### 8.2.2 谁更适合承载“偏好”

- graph-memory `EVENT/preference`
- Hermes `USER.md`

结论：

- Hermes `USER.md` 更干净、更可控
- graph-memory 适合先做 preference candidate 层

---

## 9. 整合时哪些要保留，哪些不要搬

## 9.1 应保留的能力

### A. typed KG + edges

原因：

- 这是 Hermes 当前没有的结构优势
- 能承载“问题-方法-冲突-替代”关系

### B. dual-path recall + PPR

原因：

- 能让 durable knowledge 的召回比普通搜索更聪明

### C. Recall Pool

原因：

- 弥补 Hermes 缺少的 session 内滚动 recall 能力

### D. session_end maintenance

原因：

- 让知识库不是写完就烂，而是持续治理

## 9.2 只应部分保留的能力

### A. explicit memory

建议：

- 保留成 Hermes 一个独立 `graph_record` 或 `kg_record` 工具
- 不要直接等同 Hermes `memory`

### B. selfReflection

建议：

- 可保留，但应降权为“候选知识生成器”
- 不可直接进入 frozen memory 或 skills

### C. finalize promotedSkills

建议：

- 可作为“生成 Hermes skill 草稿”的信号源
- 不应直接创建/改写 Hermes 真 skill

## 9.3 不应直接搬入 Hermes 主链的能力

### A. `assemble()` 的 visible transcript 层

理由：

- Hermes 已有自己的 message history + compression 机制
- 双 transcript 层会导致复杂度翻倍

### B. graph `systemPromptAddition` 接管 system prompt

理由：

- 会破坏 Hermes cached prompt 结构

### C. graph-memory 自带 compact 逻辑

理由：

- Hermes 已有成熟 compressor

---

## 10. Hermes 友好的整合方案

以下方案以“保持 Hermes 上下文可控”为最高优先级。

## 10.1 推荐的分层

建议把整合后的体系分成 5 层：

1. **Stable Prompt Layer**
   - Hermes 原生 system prompt
   - frozen `MEMORY.md` / `USER.md`
   - skills index
   - context files
2. **Dynamic Recall Layer**
   - Honcho turn recall
   - graph-memory recall results
   - `session_search` 按需结果
3. **Message Layer**
   - Hermes 原生 messages
   - Hermes compressor
   - `@` 引用展开
4. **Durable Knowledge Store**
   - graph-memory KG DB
5. **Curated Memory Layer**
   - Hermes `MEMORY.md`
   - Hermes `USER.md`
   - Hermes skills

其中最关键的是：

- graph-memory 属于 **Durable Knowledge Store + Dynamic Recall Layer**
- 不属于 Stable Prompt Layer

## 10.2 推荐接入点

最合理的接入方式有两种。

### 方案 A：作为 `pre_llm_call` 动态 recall provider

流程：

1. Hermes 在当前轮拿到 user query / recent turn summary。
2. 调 graph-memory `recall(query)`.
3. 只返回 top-K 节点和边的受控摘要。
4. 把这份结果作为 `ephemeral_system_prompt` 或插件 context 附加。

优点：

- 不改 Hermes `_build_system_prompt()`
- 不破坏 cached prompt
- 最容易渐进接入

缺点：

- 首轮 recall 和每轮 recall 都要额外调用

### 方案 B：作为新工具 + 后台记忆后端

暴露工具：

- `graph_search`
- `graph_record`
- `graph_stats`
- `graph_maintain`

再加一个自动后台流程：

- 每轮结束后将当前 turn 投递给 graph writer

优点：

- 最符合 Hermes 工具化哲学
- 调试最容易

缺点：

- 如果只靠工具主动调用，session 内 recall 场较弱

### 最佳路线

我建议：

- **先做 A+B 混合**
- 工具层先落地
- 动态 recall 只做很薄一层

---

## 10.3 推荐的上下文注入格式

不要把 OpenClaw 风格的整段 `<kg>` XML 原样塞进 Hermes 主 prompt。  
应该变成受控的 `Graph Recall Block`，例如：

```text
<graph_recall>
Related durable knowledge:
1. [SKILL] docker-proxy-fix
   Trigger: Docker network/proxy conflict
   Key steps: check all_proxy, bridge, DNS
2. [EVENT] importerror-libgl1
   Solved by: apt-install-libgl1
Relations:
- importerror-libgl1 --SOLVED_BY--> apt-install-libgl1
</graph_recall>
```

原因：

1. Hermes 更强调可读、可调试、可控。
2. XML 图谱原文过重，不利于 token 控制。
3. graph-memory 可以内部保留完整图结构，但注入层应该是摘要后的结果。

## 10.4 推荐预算与护栏

### 注入预算

- graph recall block 默认不超过动态上下文预算的 10-15%
- 节点数默认 3-6 个
- 边默认 2-6 条

### 节点过滤优先级

建议顺序：

1. 当前 query 直接命中节点
2. `explicit/manual` 节点
3. `validatedCount` 高的 `SKILL`
4. `PATCHES/CONFLICTS_WITH` 边
5. 社区补充节点

### 不应注入的内容

- 纯一次性 `TASK`，除非与当前 query 高相关
- 低 confidence reflection
- 已 superseded 节点
- 长 transcript 摘要

## 10.5 写入策略建议

整合后不建议照搬 graph-memory 的“每轮三线并发直写”。

更好的 Hermes 版本应是：

### 写入分三档

#### 档 1：graph raw candidate

- 每轮 after-turn 可自动抽取
- 写入 KG DB
- 标记为 candidate/auto

#### 档 2：durable trusted graph node

- 经过多次命中、validatedCount 增长、或显式确认后升级

#### 档 3：curated Hermes memory/skill

- 只有在 background review / flush / agent 明确判断时
- 才升级为 `MEMORY.md` / `USER.md` / Hermes skill

这能保证：

- graph-memory 继续高召回、高生长
- Hermes curated layers 仍然干净

## 10.6 与 Hermes skills 的协作建议

最有价值的不是把 graph `SKILL` 节点直接等同 Hermes skill。  
建议这样做：

1. graph `SKILL` 先存成知识节点。
2. 当一个 graph `SKILL`
   - 被多次召回
   - `validatedCount` 高
   - 有较强步骤性
   - 和当前任务强关联
3. background review agent 再决定：
   - 是否生成/patch Hermes skill

换句话说：

- graph-memory 是 Hermes skill 的上游知识孵化层
- Hermes skill 是 graph-memory 高成熟度方法的正式制度化结果

---

## 11. 推荐的 Hermes 整合流程

## 11.1 每轮主流程

```text
用户消息到来
  ├─ Hermes 原生 system prompt 保持不变
  ├─ Hermes 处理 @ 引用 / plugin pre-llm
  ├─ graph-memory recall(query/recent turn)
  │   └─ 输出受控 graph recall block
  ├─ Hermes message compressor / hygiene
  └─ 调模型
```

## 11.2 每轮结束后

```text
回合结束
  ├─ 写 transcript / session history
  ├─ graph writer:
  │   ├─ auto extract
  │   ├─ explicit trigger path
  │   └─ optional selfReflection
  ├─ update recall pool
  └─ Hermes background review
      ├─ 必要时写 MEMORY.md / USER.md
      └─ 必要时 patch/create Hermes skill
```

## 11.3 session_end

```text
session_end
  ├─ graph finalize
  ├─ graph maintenance
  ├─ Hermes flush memories
  └─ Hermes flush skill review
```

这样两边职责就分清了：

- graph-memory 管“知识网络”
- Hermes 管“稳定上下文、事实精选、技能制度化”

---

## 12. 最终判断

## 12.1 什么是重复的

重复较多的是：

- transcript 压缩
- tool result 裁剪
- 历史上下文组装
- 偏好/事实层一部分能力

这些层不应双重保留。

## 12.2 什么是冲突的

最大冲突点是：

1. graph-memory 在 OpenClaw 中是 context engine，Hermes 已有自己的 prompt engine。
2. graph-memory 是默认积极写入，Hermes 是 curated 写入。
3. graph-memory 偏动态 assemble，Hermes 偏稳定骨架。

## 12.3 什么最有价值

最值得带入 Hermes 的只有三类：

1. **关系型 durable knowledge graph**
2. **session 内 evolving recall pool**
3. **session_end 图治理**

## 12.4 最推荐的整合原则

最关键的原则只有一句：

**让 graph-memory 成为 Hermes 的动态知识召回后端，而不是让它改写 Hermes 的上下文主骨架。**

如果按这个原则整合，结果会是：

- Hermes 保留稳定、可控、cache-friendly 的架构优势
- graph-memory 提供自动知识增长、关系召回、社区补全的能力
- 二者职责清楚，不会互相污染

这才是最符合 Hermes 架构、同时最大化 graph-memory 价值的整合路线。
