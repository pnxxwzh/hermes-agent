# SparkGraph 功能实现说明（2026-04-02）

## 1. 文档目的

本文档用于完整记录本轮 SparkGraph 集成与落地改动，说明：

1. 为什么要做这次改动
2. 本次实际完成了哪些功能
3. 这些功能挂接到了哪些真实业务入口
4. 如何在真实 Hermes 对话中验证它们
5. 当前仍然保留了哪些 v1 边界，没有继续扩复杂度

本文档对应的是一次“从设计走向真实业务”的实现记录，而不是纯设计稿。

## 2. 背景与目标

Hermes 原有的长期记忆主要依赖 `MEMORY.md` / `USER.md`。这种方式有两个问题：

1. 长期排障经验、稳定事实、资源和决策会不断挤占常驻 prompt 空间
2. 即使某些知识更适合“以后按需召回”，也只能以文本条目形式常驻注入

本轮改动的目标，是给 Hermes 增加一层可持久化、可检索、可维护、可召回的结构化长期知识层：SparkGraph。

目标不是“再存一份 memory 备份”，而是把长期知识分成两层：

1. `memory`
   - 体积小
   - 直接影响当前对话
   - 常驻注入
2. `SparkGraph`
   - 容纳更多 durable knowledge
   - 写入 SQLite
   - 后续按 query 动态 recall
   - 支持维护、降级、关系扩展

## 3. 本次功能范围

本轮真正落地并已接到真实业务链路的能力如下。

### 3.1 SparkGraph 节点持久化

实现内容：

1. 新增 SparkGraph sqlite store
2. 支持节点类型：
   - `FACT`
   - `PREFERENCE`
   - `ISSUE`
   - `RESOURCE`
   - `DECISION`
3. 支持 evidence 追加、节点更新、评分和状态维护

真实业务入口：

1. 主流程 `memory(target="memory")` 成功后，对 durable troubleshooting knowledge 同步镜像到 SparkGraph
2. background review agent 可调用 `sparkgraph_record`
3. flush/compression 路径可调用 `sparkgraph_record`

用户可见信号：

```text
┊ ✨ sparkgraph +ISSUE: "..."
```

### 3.2 Background review 链路修复

本轮不仅接入了 SparkGraph，还顺手修复了 Hermes 原有的一个 runtime 继承问题：

1. 原 background review agent 没有完整继承主 agent 的 `base_url/api_key/api_mode`
2. 在第三方 API / 自定义端点场景下，background review 可能跑偏或失败

修复后：

1. background review agent 会继承主 agent 的 runtime transport config
2. `memory review`、`skill review`、SparkGraph review 写入都能稳定使用相同 provider/base_url

这不是 SparkGraph 私有 hack，而是 Hermes 原有 background review 机制的 bugfix。

### 3.3 SparkGraph recall 动态注入

实现内容：

1. 每轮会根据当前用户问题构造 SparkGraph recall block
2. recall block 是临时注入，不污染常驻系统 prompt
3. recall 命中时支持可见 CLI 提示

用户可见信号：

```text
┊ ✨ recall    +N: "..."
```

这使得“节点命中但用户不知道是否真的注入 prompt”这个问题得到明显改善。

### 3.4 Embedding 写入与语义召回

本轮补齐了之前只是“配置壳子”的 embedding 链路。

已完成：

1. 写入节点时生成 embedding
2. 向量写入 `sg_vectors`
3. recall 时结合向量相似度与 FTS 结果混合召回
4. embedding runtime 不可用时自动退回 FTS-only
5. 旧节点支持向量回填

实际价值：

1. 召回不再只靠词面 совпal
2. 改写问法、概括问法更容易命中
3. 旧知识不需要手工重写一遍，maintenance 会逐步补齐 embedding

### 3.5 生命周期治理与 maintenance

本轮把 SparkGraph 从“只进不出”推进为“有基础治理能力”的长期知识层。

已完成：

1. `candidate -> deprecated`
   - 对陈旧、低信号、低稳定、低 evidence 的 candidate 节点执行降级
2. `active -> deprecated`
   - 引入 `last_recalled_at`
   - recall 时记账 `recall_hits`
   - maintenance 可依据 recall/支持度/stability 进行 active 节点治理
3. maintenance 会记录基础结果
   - `scanned`
   - `deprecated`
   - `vectors_backfilled`

真实业务已验证：

1. 维护逻辑不只在单测中生效
2. 真实 Hermes 会话写入能触发 maintenance
3. probe 节点在真实库里可被降级为 `deprecated`

### 3.6 新写入的近重复去重

本轮修复了一个很容易在真实业务中积累脏数据的问题：

1. 同一条排障经验可能被主流程写成 `ISSUE`
2. 又被 review/flush 写成 `FACT`
3. 造成同主题节点分叉

为此已完成：

1. 同类型 dedup
2. `ISSUE/FACT` 跨类型 dedup
3. evidence 追加而不是反复建新 node

真实业务已验证：

1. Redis 近义改写不会继续长出第三条节点
2. 而是 update 已有节点并追加 evidence

### 3.7 边能力第一阶段落地

这是本轮后半段新增的功能。

目的：

让 SparkGraph 不再只是“节点列表”，而是开始有真实业务里的图关系数据。

v1 只做了最小范围：

1. 只写一种边：`RELATED_TO`
2. 只在同一次 `sparkgraph_record` 写入批次里生效
3. 只对兼容类型生效：
   - `FACT`
   - `ISSUE`
   - `RESOURCE`
   - `DECISION`
4. 只在“文本明显相关”时自动建边

这个判断目前由代码规则完成，不交给模型。

真实业务已验证：

1. 两条 Elasticsearch 相关排障经验写入后，数据库出现真实 `RELATED_TO` 边
2. 后续泛化查询能同时带出“字段映射问题”和“排序/聚合/分页开销问题”两类知识

## 4. 本次改动的核心文件

### 核心运行时

1. [run_agent.py](/Users/wzh/IsacHermes/run_agent.py)
2. [model_tools.py](/Users/wzh/IsacHermes/model_tools.py)
3. [agent/display.py](/Users/wzh/IsacHermes/agent/display.py)

### SparkGraph 核心模块

1. [agent/sparkgraph/config.py](/Users/wzh/IsacHermes/agent/sparkgraph/config.py)
2. [agent/sparkgraph/db.py](/Users/wzh/IsacHermes/agent/sparkgraph/db.py)
3. [agent/sparkgraph/store.py](/Users/wzh/IsacHermes/agent/sparkgraph/store.py)
4. [agent/sparkgraph/dedup.py](/Users/wzh/IsacHermes/agent/sparkgraph/dedup.py)
5. [agent/sparkgraph/recaller.py](/Users/wzh/IsacHermes/agent/sparkgraph/recaller.py)
6. [agent/sparkgraph/manager.py](/Users/wzh/IsacHermes/agent/sparkgraph/manager.py)
7. [agent/sparkgraph/maintenance.py](/Users/wzh/IsacHermes/agent/sparkgraph/maintenance.py)
8. [agent/sparkgraph/runtime.py](/Users/wzh/IsacHermes/agent/sparkgraph/runtime.py)
9. [agent/sparkgraph/embedding.py](/Users/wzh/IsacHermes/agent/sparkgraph/embedding.py)

### 工具与 setup

1. [tools/sparkgraph_tool.py](/Users/wzh/IsacHermes/tools/sparkgraph_tool.py)
2. [tools/__init__.py](/Users/wzh/IsacHermes/tools/__init__.py)
3. [hermes_cli/setup.py](/Users/wzh/IsacHermes/hermes_cli/setup.py)
4. [hermes_cli/status.py](/Users/wzh/IsacHermes/hermes_cli/status.py)
5. [hermes_cli/doctor.py](/Users/wzh/IsacHermes/hermes_cli/doctor.py)
6. [gateway/status.py](/Users/wzh/IsacHermes/gateway/status.py)

## 5. 真实业务行为总结

### 5.1 现在用户在 CLI 中能看到什么

#### 写入 memory

```text
┊ 🧠 memory    +memory: "..."
```

#### 写入 SparkGraph

```text
┊ ✨ sparkgraph +ISSUE: "..."
```

#### SparkGraph recall 命中

```text
┊ ✨ recall    +N: "..."
```

#### 异步汇总状态

```text
💾 Memory updated · SparkGraph updated (...)
```

### 5.2 写入链路

现在 durable troubleshooting knowledge 的主路径是：

```text
用户说“记住这个长期可复用的排障经验” 
-> 主流程 memory 写入
-> 同步镜像 SparkGraph
-> CLI 立刻显示 memory/sparkgraph
-> 后台 review 异步补漏
```

### 5.3 recall 链路

```text
用户提问
-> build_recall_block(query)
-> FTS + vector recall
-> one-hop related expansion
-> 注入 [SparkGraph Recall]
-> CLI 显示 ✨ recall
```

## 6. 已完成的真实业务验证

本轮不仅跑了测试，也做了多轮真实 `./hermes` 对话验证。

已验证通过：

1. 主流程 `memory -> SparkGraph` 同步写入
2. background review 写图
3. recall 注入
4. embedding 写入与旧节点回填
5. Redis 近义改写更新旧节点而不是继续长新节点
6. candidate/active 生命周期治理能在真实业务触发
7. Elasticsearch 两条相关知识自动建 `RELATED_TO`
8. 相关边会帮助泛化问法下的联合回答

## 7. 测试覆盖

本轮相关测试覆盖包括但不限于：

1. [tests/test_run_agent.py](/Users/wzh/IsacHermes/tests/test_run_agent.py)
2. [tests/test_flush_memories_codex.py](/Users/wzh/IsacHermes/tests/test_flush_memories_codex.py)
3. [tests/integration/test_sparkgraph_flush_flow.py](/Users/wzh/IsacHermes/tests/integration/test_sparkgraph_flush_flow.py)
4. [tests/sparkgraph/test_db.py](/Users/wzh/IsacHermes/tests/sparkgraph/test_db.py)
5. [tests/sparkgraph/test_store.py](/Users/wzh/IsacHermes/tests/sparkgraph/test_store.py)
6. [tests/sparkgraph/test_recaller.py](/Users/wzh/IsacHermes/tests/sparkgraph/test_recaller.py)
7. [tests/sparkgraph/test_maintenance.py](/Users/wzh/IsacHermes/tests/sparkgraph/test_maintenance.py)
8. [tests/sparkgraph/test_dedup.py](/Users/wzh/IsacHermes/tests/sparkgraph/test_dedup.py)
9. [tests/tools/test_sparkgraph_record_tool.py](/Users/wzh/IsacHermes/tests/tools/test_sparkgraph_record_tool.py)
10. [tests/hermes_cli/test_sparkgraph_setup.py](/Users/wzh/IsacHermes/tests/hermes_cli/test_sparkgraph_setup.py)

## 8. v1 的明确边界

为了避免 v1 复杂度失控，本轮特意没有继续做这些更重的能力。

### 8.1 没有做复杂边类型扩展

当前只落地了：

1. `RELATED_TO`

没有继续做：

1. `DEPENDS_ON`
2. `CONFLICTS_WITH`
3. `DERIVED_FROM`
4. `APPLIES_TO`

### 8.2 没有让模型自由产边

当前边判断来自代码规则，而不是模型自由输出。

原因：

1. v1 更稳
2. 可测
3. 不容易把图谱写脏

### 8.3 没有做复杂 merge/edge migration

当前重点是：

1. 防止继续制造新重复
2. 让 graph recall 有真实关系数据

尚未做：

1. loser 节点边迁移到 winner
2. 历史重复节点自动 fully merge
3. 多类型边的 ranking / support 深度参与

## 9. 当前仍保留的后续项

这些仍记录在 backlog 中，但不属于本次 v1 完成范围：

1. 历史重复节点 winner/loser 收敛
2. 边信号进入 active support/ranking
3. merge 后边迁移
4. 更多 relation 类型进入 review/flush

对应记录见：

[sparkgraph-optimization-backlog-2026-04-02.md](/Users/wzh/IsacHermes/wzh-research/sparkgraph-optimization-backlog-2026-04-02.md)

## 10. 最终结论

本轮改动的意义，不是“给 Hermes 多加一个库”。

真正完成的是：

1. 把 SparkGraph 从设计能力变成真实业务能力
2. 把长期知识从单层 memory 升级为“memory + structured graph memory”
3. 让写入、召回、维护、语义检索、基础图关系都在真实 Hermes 对话中得到验证
4. 同时把复杂度控制在 v1 可维护范围内

到本文档落地时，SparkGraph v1 已具备以下可用能力：

1. durable knowledge 持久化
2. recall 动态注入
3. embedding 语义召回
4. 基础 lifecycle 维护
5. 近重复写入收敛
6. `RELATED_TO` 第一阶段业务化落地

可以把它视为：“Hermes 的结构化长期知识层，已经从实验特性进入可用的 v1 状态。”
