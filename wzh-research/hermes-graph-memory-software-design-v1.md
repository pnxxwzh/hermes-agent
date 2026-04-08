# Hermes + graph-memory 整合软件设计（第一稿）

> 配套追踪矩阵：`wzh-research/graph-memory-feature-test-matrix.md`  
> 该文档用于约束后续实现与测试覆盖，确保每个 feature 都能追踪到改动文件与测试用例。

> 配套配置设计：`wzh-research/graph-memory-config-setup-design.md`  
> 该文档细化了 graph-memory 作为核心配置块时的 setup / reconfigure / restore / bridge / probe 流程。

> 新方向收缩稿：`wzh-research/sparkgraph-lite-design-v1.md`  
> 该文档代表当前最新共识：从重型 graph-memory 迁移方案收缩为 `SparkGraph Lite`，只承担 durable knowledge graph backend 职责，不再承载 skill 图谱与 context-engine 野心。

> 设计范围修订（当前共识）  
> 只整合 graph-memory 中：
> 1. 记忆提取能力  
> 2. 图数据库/治理能力  
> 3. 对话中的节点召回能力  
>
> 明确不整合：
> 1. OpenClaw 风格的 context engine 主导权  
> 2. visible transcript / assemble / compact 主链  
> 3. graph 直接改写 Hermes 的 curated memory / skills  
>
> 额外优化方向：
> 将 Hermes 中合适的后台 side-task 下放给本地小模型，尤其是候选提取、反思、轻量总结，而不是最终制度化写入。

> 重要补充（最新共识）  
> 本文保留作为“早期重型整合方案”参考，但后续实现应优先以 `SparkGraph Lite` 方案为准。  
> 也就是说：
> 1. 不再让 graph 子系统承担 `SKILL` 图谱职责  
> 2. 不再追求 OpenClaw 式重型图谱上下文引擎  
> 3. 只保留 durable knowledge extraction / graph store / accurate recall

## 1. 设计目标

本设计面向一个真实可实施的大项目，不是概念验证。目标是把现有 graph-memory 的核心价值整合进 Hermes，同时保住 Hermes 当前最重要的架构优势：

1. 稳定的 cached system prompt
2. 可控的动态上下文注入
3. 现有 `MEMORY.md` / `USER.md` / skills / `session_search` 分层
4. 可渐进上线、可回滚、可测试

本设计明确采用：

- **Hermes 为主架构**
- **graph-memory 作为新增 durable knowledge 子系统**
- **不把 graph-memory 以 OpenClaw `contextEngine` 形态直接塞进 Hermes**
- **只迁入 graph-memory 的 extraction / graph store / recall 能力**
- **把 Hermes 中合适的后台整理任务改造成“小模型候选层 + 强模型最终决策层”**

---

## 2. 非目标

第一版不做这些事：

1. 不替换 Hermes 的 `ContextCompressor`
2. 不替换 Hermes 的 `session_search`
3. 不替换 Hermes 的 `MEMORY.md` / `USER.md`
4. 不让 graph-memory 直接生成或覆盖 Hermes skills
5. 不引入 OpenClaw 风格的 `visibleTranscript` 主导层
6. 不做“每轮都改 cached system prompt”

这些都容易破坏 Hermes 的稳定性，必须后置。

---

## 3. 核心架构决策

## 3.0 Namespace and Storage Compatibility Rule

这是当前整合方案的硬约束：

1. **graph-memory 的存储组织默认严格跟随 Hermes 官方 `HERMES_HOME/profile` 模型**
2. **不在第一版自行发明单 profile 内多长期独立 agent namespace**
3. **官方如何组织 `SOUL.md` / `MEMORY.md` / `USER.md` / `sessions` / `skills`，graph-memory 就如何组织**
4. **如未来 Hermes 官方引入更细粒度 agent namespace，再顺着官方抽象扩展 graph-memory**

这样做的原因：

1. 维护成本最低
2. 后续升级冲突最少
3. 避免 graph-memory 在命名空间上成为 Hermes 的平行体系
4. 避免我们为了“多 agent”过早重写 Hermes 的核心假设

当前具体落地原则：

1. 默认 graph DB 路径必须位于当前 `HERMES_HOME` 内
2. named profile 下，graph-memory 自然落到对应 profile 目录
3. graph-memory 的生命周期、配置作用域、doctor/health/status 作用域全部与当前 profile 保持一致
4. session 级状态可以临时隔离，但 durable graph 存储默认是 profile 级

## 3.1 采用“第一方子系统”而不是“纯插件”

虽然 Hermes 已有 `pre_llm_call` / `post_llm_call` 插件 hook，但**不建议最终把 graph-memory 完全做成外置插件**。

原因：

1. 插件 hook 是同步调用，适合轻量上下文注入，不适合承载完整 after-turn 写入流水线。
2. graph-memory 需要自己的 DB、维护任务、辅助模型路由、生命周期治理。
3. 如果完全靠插件，会把关键状态藏在插件层，后续调试、配置、测试都会变差。

所以本设计采用：

- **graph-memory 核心做成 Hermes 内置子系统**
- 可选再提供轻量插件接口给外部扩展

## 3.2 采用“双层记忆”而不是“单层替换”

整合后保留两类长期信息：

1. **Curated Memory**
   - `MEMORY.md`
   - `USER.md`
   - Hermes skills
2. **Durable Knowledge Graph**
   - graph-memory SQLite 图谱

二者关系：

- graph-memory 负责“自动长知识网络”
- Hermes curated layers 负责“精选事实和制度化方法”

## 3.3 采用“两阶段写入”

这是最重要的质量控制策略。

### 阶段 A：候选知识写入 graph DB

来源：

- extractor
- explicit memory
- self-reflection
- manual graph record

### 阶段 B：高置信知识升级进 Hermes curated layers

触发：

- background review
- flush
- 明确工具调用

这能避免：

- graph 自动抽取污染 `MEMORY.md`
- 小模型误把临时结论写成 skill

---

## 4. 目标运行流程

## 4.1 调用前

```text
Hermes cached system prompt
  + ephemeral_system_prompt
  + plugin pre_llm_call context
  + graph recall block
  + Honcho turn recall
  + current messages
```

其中 `graph recall block` 是动态层，不进 cached prompt。

## 4.2 调用后

```text
post-turn pipeline
  ├─ persist transcript/session as usual
  ├─ graph ingest recent turn
  ├─ graph extractor (candidate nodes/edges)
  ├─ graph explicit memory path
  ├─ graph reflection path
  ├─ graph recall pool update
  └─ Hermes background review / flush (现有机制)
```

## 4.3 session_end / reset / compression 前

```text
Hermes flush_memories
  + graph finalize
  + graph maintenance
```

---

## 5. 模块拆分

建议新增一个正式 Python 包：

`agent/graph_memory/`

建议文件结构：

```text
agent/graph_memory/
  __init__.py
  config.py
  types.py
  db.py
  store.py
  write_policy.py
  extractor.py
  reflection.py
  explicit.py
  recaller.py
  recall_pool.py
  formatter.py
  maintenance.py
  manager.py
```

### 模块职责

- `config.py`
  - 读取/校验 graph-memory 配置
- `types.py`
  - 节点、边、召回结果、配置类型
- `db.py`
  - SQLite schema、迁移、路径解析
  - 强制遵守 `HERMES_HOME/profile` 存储边界
- `store.py`
  - node/edge/message CRUD
- `write_policy.py`
  - 同名节点合并策略、confidence/validatedCount 合并
- `extractor.py`
  - turn extract LLM 调用与 parse
- `reflection.py`
  - turn-level reflect
- `explicit.py`
  - trigger detection、window、summary、persist
- `recaller.py`
  - precise/generalized recall、PPR 排序
- `recall_pool.py`
  - session 内 evolving pool
- `formatter.py`
  - 将 recalled graph 变成 Hermes 可注入的 `graph recall block`
- `maintenance.py`
  - dedup、pagerank、communities、finalize
- `manager.py`
  - Hermes 侧主入口，协调所有子模块

---

## 6. 逐文件改动计划

下面按“新增文件”和“修改现有文件”拆。

## 6.1 新增文件

### A. `agent/graph_memory/types.py`

功能点：

1. 定义 `GraphNodeType`, `GraphEdgeType`
2. 定义 `GraphNode`, `GraphEdge`
3. 定义 `GraphRecallResult`
4. 定义 `GraphWriteCandidate`
5. 定义 `GraphMemoryConfig`

测试：

- `tests/graph_memory/test_types.py`
  - 枚举和值校验
  - dataclass/default 值校验

### B. `agent/graph_memory/config.py`

功能点：

1. 从 `config.yaml` 读取 `graph_memory` 配置块
2. 提供默认值
3. provider/model/base_url/api_key 支持
4. 区分：
   - `recall_model`
   - `reflection_model`
   - `extraction_model`
   - `maintenance_model`
5. 支持禁用某些子能力：
   - `enabled`
   - `reflection_enabled`
   - `explicit_enabled`
   - `auto_extract_enabled`
   - `maintenance_enabled`
6. graph-memory 默认路径解析必须跟随当前 `HERMES_HOME/profile`

测试：

- `tests/graph_memory/test_config.py`
  - 默认值
  - 部分覆盖
  - 非法配置报错
  - profile 路径解析正确

### C. `agent/graph_memory/db.py`

功能点：

1. SQLite schema 创建与迁移
2. graph DB 路径解析
3. session/domain 隔离
4. base DB 初始化

路径建议：

- `~/.hermes/graph-memory/default.db`
- 可选 session/project/domain 分层：
  - `~/.hermes/graph-memory/{workspace_hash}/{domain}.db`

测试：

- `tests/graph_memory/test_db.py`
  - migrate 幂等
  - schema 存在
  - path resolve 正确

### D. `agent/graph_memory/store.py`

功能点：

1. nodes/edges/messages/vectors CRUD
2. search
3. get-by-session
4. mark-extracted / mark-explicit-consumed
5. vector 保存与 hash 去重

测试：

- `tests/graph_memory/test_store.py`
  - upsert node
  - upsert edge
  - search fallback
  - getBySession
  - message idempotent save

### E. `agent/graph_memory/extractor.py`

功能点：

1. 移植 graph-memory extractor prompt
2. 输出 parse 与合法性修正
3. 边方向修正
4. 最终产出 `GraphWriteCandidate`

测试：

- `tests/graph_memory/test_extractor.py`
  - 正常 JSON parse
  - `<think>` / fenced code 清洗
  - 非法边剔除
  - TASK→SKILL 自动修成 `USED_SKILL`

### F. `agent/graph_memory/reflection.py`

功能点：

1. recent-turn transcript 渲染
2. 小模型 reflect
3. `skill/rule/preference` parse
4. reflection node 写入

测试：

- `tests/graph_memory/test_reflection.py`
  - 空结果不写入
  - 规则/偏好类型映射
  - 长消息截断

### G. `agent/graph_memory/explicit.py`

功能点：

1. 显式 trigger 识别
2. explicit window 选择
3. summary candidate 过滤
4. persist explicit candidates

测试：

- `tests/graph_memory/test_explicit.py`
  - trigger 匹配
  - window 选择
  - confidence filter
  - preference signal persist

### H. `agent/graph_memory/recaller.py`

功能点：

1. precise recall
2. generalized recall
3. PPR / graph walk
4. merge results
5. ranking priority

测试：

- `tests/graph_memory/test_recaller.py`
  - vector 不可用时 fallback 到 FTS
  - precise/generalized merge
  - explicit/manual/reflection ranking

### I. `agent/graph_memory/recall_pool.py`

功能点：

1. per-session pool
2. LRFU eviction
3. seed
4. recallTurn update

测试：

- `tests/graph_memory/test_recall_pool.py`
  - seed 行为
  - 命中次数更新
  - 超容驱逐

### J. `agent/graph_memory/formatter.py`

功能点：

1. 把 recall 结果转成 Hermes 可读的动态注入块
2. 控制预算
3. 控制节点数/边数
4. 过滤 superseded / low-confidence nodes

建议输出：

```text
<graph_recall>
...
</graph_recall>
```

而不是原始 `<kg>` XML。

测试：

- `tests/graph_memory/test_formatter.py`
  - token budget 生效
  - SKILL/TASK/EVENT 排序
  - 边过滤

### K. `agent/graph_memory/maintenance.py`

功能点：

1. dedup
2. global pagerank
3. community detection
4. community summaries
5. session finalize

测试：

- `tests/graph_memory/test_maintenance.py`
  - duplicate merge
  - pagerank 写回
  - community labels
  - finalize invalidations

### L. `agent/graph_memory/manager.py`

功能点：

作为 Hermes 内部统一入口，提供：

1. `build_turn_context(...)`
2. `ingest_turn(...)`
3. `run_turn_extract(...)`
4. `run_reflection(...)`
5. `run_explicit_memory(...)`
6. `update_recall_pool(...)`
7. `finalize_session(...)`
8. `run_maintenance(...)`

测试：

- `tests/graph_memory/test_manager.py`
  - 端到端单 turn
  - recall block 构建
  - session_end finalize

---

## 6.2 新增工具文件

### `tools/graph_memory_tools.py`

功能点：

第一版建议提供 4 个工具：

1. `graph_search`
2. `graph_record`
3. `graph_stats`
4. `graph_maintain`

说明：

- 直接沿用 graph-memory 当前 `gm_*` 能力，但命名更贴近 Hermes
- 避免与已有工具集冲突

测试：

- `tests/tools/test_graph_memory_tools.py`
  - `graph_search` 返回格式
  - `graph_record` 创建节点/边
  - `graph_stats` 输出聚合
  - `graph_maintain` 调 maintenance

### 修改 `tools/__init__.py`

功能点：

1. 导出 graph-memory 工具
2. 保持懒加载风格一致

测试：

- `tests/tools/test_graph_memory_tools.py`
  - 工具导入可用

---

## 6.3 修改现有 Hermes 核心文件

### A. `run_agent.py`

这是核心改动点。

#### 改动 1：初始化 graph-memory manager

位置：

- `AIAgent.__init__()` memory/skills 初始化附近

功能点：

1. 读取 `graph_memory` 配置
2. 如果启用，实例化 `GraphMemoryManager`
3. 绑定：
   - session_id
   - platform
   - model/provider

#### 改动 2：在 `pre_llm_call` 之后拼 graph recall block

位置：

- `pre_llm_call` hook 收集后，进入主 loop 之前

功能点：

1. 以当前 user message + recent summary/query 构造 recall query
2. 调 `GraphMemoryManager.build_turn_context(...)`
3. 将结果追加进 `_plugin_turn_context` 或新增 `_graph_turn_context`
4. 注入点必须是 ephemeral，不进 cached prompt

#### 改动 3：在 `post_llm_call` 之后触发 graph ingest

位置：

- `post_llm_call` hook 之后，background review 之前

功能点：

1. 把本轮 user/assistant/new tool messages 交给 graph manager
2. 更新 recall pool
3. 可同步写 message rows
4. extractor / reflection / explicit memory 可异步或后台执行

#### 改动 4：在 `on_session_end` 前后增加 graph finalize

位置：

- 现有 `on_session_end` 附近

功能点：

1. `finalize_session(session_id, messages)`
2. 可选择只在：
   - completed session
   - flush/reset path
   - session_end path
   触发

#### 改动 5：background review 与 graph 协同

位置：

- `_spawn_background_review()`

功能点：

第一版不改 review agent 主流程，但增加可选逻辑：

1. 当 review 发现值得制度化的信息时
2. 可读取 graph candidates 作为额外参考
3. 仍然由 Hermes review agent 决定是否写 memory / skill

测试：

- `tests/test_run_agent_graph_memory.py`
  - 初始化启用/禁用 graph manager
  - pre-llm 注入 graph context 不污染 cached prompt
  - post-turn ingest 被调用
  - session_end finalize 被调用

### B. `agent/context_compressor.py`

第一版尽量不改核心压缩算法。  
只补一个兼容点：

功能点：

1. 压缩后 session 切换时，通知 graph manager：
   - 旧 session 被压缩/切换
   - 如需 flush graph finalize，可安全触发

测试：

- `tests/test_compression_persistence.py`
- 新增：
  - `tests/test_graph_memory_compression_boundary.py`

### C. `gateway/run.py`

功能点：

1. 在 gateway reset / inactivity flush 前，调用 graph finalize
2. 在 async flush 路径保证 graph side effects 不丢
3. session key / workspace cwd 传给 graph manager

测试：

- `tests/gateway/test_async_memory_flush.py`
- 新增：
  - `tests/gateway/test_graph_memory_flush.py`
  - `tests/gateway/test_graph_memory_session_reset.py`

### D. `hermes_cli/config.py`

功能点：

新增 `graph_memory` 配置段，并作为 **核心配置结构** 进入 `DEFAULT_CONFIG` 与后续迁移流程。

设计原则：

1. 不做“是否启用 graph-memory”的 setup 开关题。
2. graph-memory runtime 像 `auxiliary.vision` / `tts` 一样，始终有默认配置位。
3. 即使用户不主动配置，也应存在可解析的默认结构。
4. 真正的运行态是否激活，由功能调用与 runtime health 决定，而不是靠 setup 漏配字段。

建议结构：

```yaml
graph_memory:
  db_path: ""
  required: false
  probe_on_startup: true
  recall_max_nodes: 4
  recall_max_depth: 2
  graph_budget_ratio: 0.12
  auto_extract_enabled: true
  explicit_enabled: true
  reflection_enabled: true
  maintenance_enabled: true
  reflection_timeout: 8
  extraction_timeout: 20
  maintenance_timeout: 60
  dream_timeout: 120
  review_triage_timeout: 10
  reflection:
    provider: "auto"
    model: ""
    base_url: ""
    api_key: ""
  extraction:
    provider: "auto"
    model: ""
    base_url: ""
    api_key: ""
  maintenance:
    provider: "auto"
    model: ""
    base_url: ""
    api_key: ""
  embedding:
    provider: "auto"
    model: ""
    base_url: ""
    api_key: ""
    dimensions: 0
```

不建议把它塞进 `auxiliary.*` 下面，因为：

1. graph-memory 已经不只是 side-task
2. 它包含 DB、recall、maintenance、dream 等完整子系统
3. 将其作为顶层核心配置块更符合长期演进

但它的 runtime 子项组织形式，应 **参考 auxiliary/tts**：

1. `provider`
2. `model`
3. `base_url`
4. `api_key`
5. `timeout`

并且必须参与：

1. `DEFAULT_CONFIG`
2. config migration / `_config_version`
3. config show / edit / set
4. 初次 setup
5. 重新配置
6. 配置恢复/迁移
7. gateway runtime bridge
8. runtime health/status

测试：

- `tests/hermes_cli/test_graph_memory_config.py`

### D.1 `hermes_cli/setup.py`

这是这轮设计里必须新增的重点。

graph-memory runtime 应像主模型 / TTS / vision 一样进入正式 setup 流程。

功能点：

1. 初次 setup 中增加 `graph_memory` 配置步骤
2. 组织方式参考 `auxiliary.vision` / `tts`
3. 支持：
   - 保留当前配置
   - 重新配置某一子 runtime
   - 恢复默认
4. 不问“开不开 graph-memory”，只问“runtime 怎么配”

建议 setup 组织为 4 个子步骤：

1. `Graph reflection runtime`
2. `Graph extraction runtime`
3. `Graph maintenance runtime`
4. `Graph embedding runtime`

每一步都支持：

1. `Keep current (...)`
2. `Use main provider`
3. `Use auto provider`
4. `Use custom OpenAI-compatible endpoint`

对于 embedding，额外要求：

1. 明确提示“必须是 embedding 模型”
2. 支持单独设置 `dimensions`

### D.2 重新配置流程

必须支持这些正式路径：

1. `hermes setup`
   - 初次或全量重跑
2. `hermes config edit`
   - 手工改
3. `hermes config set`
   - 精确改单个字段
4. 未来若有专门子命令，建议：
   - `hermes graph-memory setup`
   - `hermes graph-memory doctor`

### D.3 配置恢复/迁移

graph-memory 配置加入后，还必须参与：

1. config schema migration
2. 默认值回填
3. 旧配置恢复
4. setup 中 `Keep current (...)`

具体要求：

1. 旧用户升级到新版本时，自动补全 `graph_memory` 默认结构
2. 若已有用户手工配置 `graph_memory`，setup 不应覆盖
3. 若某个 runtime 配置损坏，setup/doctor 应能指出具体子项

测试：

- 新增：
  - `tests/hermes_cli/test_graph_memory_setup.py`
  - `tests/hermes_cli/test_graph_memory_config_migration.py`
  - `tests/hermes_cli/test_graph_memory_config_restore.py`

### E. `agent/auxiliary_client.py`

功能点：

为 graph side tasks 增加明确 task route：

- `graph_reflection`
- `graph_extraction`
- `graph_maintenance`

原因：

- 让 graph 侧小模型/便宜模型与主模型解耦
- 避免直接复用主模型

测试：

- `tests/test_auxiliary_graph_memory.py`
  - task route 解析
  - provider/model/base_url 覆盖

### F. `website/docs/user-guide/configuration.md`

功能点：

补文档：

1. `graph_memory` 配置
2. 小模型与强模型分层建议
3. 与 `memory` / `skills` / `session_search` 的关系

测试：

- 无自动化测试，文档校对

### G. `gateway/status.py`

功能点：

将 graph-memory 使用的本地小模型、embedding 模型纳入 Hermes runtime health。

新增健康项建议：

1. `graph_reflection_model`
2. `graph_extraction_model`
3. `graph_maintenance_model`
4. `graph_embedding_model`

每项状态建议包含：

- `state`
  - `ok`
  - `degraded`
  - `fatal`
- `provider`
- `model`
- `base_url`
- `last_error_code`
- `last_error_message`
- `last_success_at`
- `last_failure_at`

测试：

- `tests/hermes_cli/test_gateway_runtime_health.py`
- 新增：
  - `tests/gateway/test_graph_memory_runtime_health.py`

### H. `hermes_cli/runtime_provider.py`

功能点：

graph-memory 相关模型也必须走 Hermes 正式 runtime 解析链，而不是自己偷偷拼 base_url/api_key。

建议新增专门解析入口：

1. `resolve_graph_reflection_runtime()`
2. `resolve_graph_extraction_runtime()`
3. `resolve_graph_maintenance_runtime()`
4. `resolve_graph_embedding_runtime()`

要求：

1. 行为与主模型/auxiliary 保持一致
2. 支持 config/env/explicit override
3. 支持本地端点探测
4. embedding 模型必须区分于 text model

测试：

- 新增：
  - `tests/hermes_cli/test_graph_memory_runtime_provider.py`

### I. `cli.py` / `gateway/run.py` 配置桥接

现有 Hermes 会把 `auxiliary.*` 部分配置桥接到运行时环境变量/解析逻辑。  
graph-memory 既然是核心配置，也必须参与正式 bridge。

功能点：

1. CLI 启动时加载 `graph_memory` runtime 配置
2. gateway 启动时同样加载
3. 保证 CLI/gateway/cron/background jobs 使用同一套配置源

注意：

- 这里不是简单复制 env var 逻辑，而是要保证 runtime resolver 能统一读到
- graph-memory 不应成为“CLI 有配置，gateway 忘了桥接”的半成品

测试：

- 新增：
  - `tests/test_graph_memory_config_bridge.py`
  - `tests/hermes_cli/test_graph_memory_gateway_bridge.py`

---

## 7. 关键行为设计

## 7.1 graph recall 注入策略

第一版必须满足：

1. 不写入 `_cached_system_prompt`
2. 不保存到 session DB 的 stable system prompt
3. 仅作为 turn-level 动态上下文

注入顺序建议：

```text
ephemeral_system_prompt
+ plugin pre_llm_call context
+ graph recall block
+ Honcho turn context
```

## 7.2 skill 升级策略

第一版不允许 graph 自动直接 patch Hermes skill。

只允许：

1. graph 产出 `skill_candidate`
2. Hermes background review 读取 graph candidate
3. 最终由 Hermes `skill_manage` 决策

## 7.3 memory 升级策略

第一版不允许 graph 自动直接写 `MEMORY.md` / `USER.md`。

只允许：

1. graph 写入 graph DB
2. Hermes review / flush 或 agent 显式工具调用时升级

## 7.4 小模型策略

推荐：

- `reflection`：小模型
- `explicit summary`：小模型
- `community summary`：小模型
- `finalize invalidation`：中等以上模型
- Hermes `skill_manage` 最终 patch/create：主模型或强模型

## 7.5 小模型/Embedding 模型的正式运行时地位

这一点必须明确：

**graph-memory 使用的小模型与 embedding 模型，在 Hermes 中应被视为正式运行时依赖，而不是“次要外挂依赖”。**

这意味着它们必须具备与主模型同等级别的以下能力：

1. runtime resolution
2. startup validation
3. health visibility
4. structured error surface
5. timeout / fallback policy
6. automated test coverage

### 7.5.1 运行时分类

建议把 graph-memory 模型显式分成四类 runtime endpoint：

1. `graph_reflection_runtime`
2. `graph_extraction_runtime`
3. `graph_maintenance_runtime`
4. `graph_embedding_runtime`

其中前 3 个是 text generation / reasoning 类，最后一个是 embedding 类。

### 7.5.2 与 Hermes 主模型的关系

这些 runtime 不应共享主模型客户端状态，但应共享 Hermes 的：

1. provider resolution 规则
2. config 解析规则
3. health/status 记录方式
4. 错误分级
5. 日志规范

换句话说：

- **客户端可以独立**
- **运行时治理必须统一**

## 7.5.3 作为核心配置参与所有处理流程

graph-memory runtime 既然属于系统机制的一部分，就必须参与所有正式配置处理流程：

1. `DEFAULT_CONFIG`
2. setup wizard
3. reconfigure / rerun setup
4. `config edit`
5. `config set`
6. 配置迁移
7. 配置恢复
8. runtime bridge
9. runtime health
10. doctor/status 输出

这意味着：

- 它不能只是研究文档里的“未来配置块”
- 必须像 `tts`、`stt`、`auxiliary.vision` 一样，成为 Hermes 配置表的一等公民

## 7.5.4 推荐配置表组织方式

建议在配置文档中新增正式章节，和 `auxiliary` / `tts` 同级呈现：

```yaml
graph_memory:
  db_path: "~/.hermes/graph-memory/default.db"
  required: false
  probe_on_startup: true
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

并在表格文档中明确说明：

| Slot | 作用 |
|---|---|
| `graph_memory.reflection` | 每轮 turn-level reflect / candidate rule extraction |
| `graph_memory.extraction` | turn extraction / node-edge generation |
| `graph_memory.maintenance` | finalize / dedup / community summary / dream |
| `graph_memory.embedding` | vector recall / dedup / community vector search |

## 7.5.5 setup 交互原则

既然不做 enable/disable 问题，setup 交互应遵循：

1. 默认展示当前配置
2. 支持 `Keep current (...)`
3. 支持 `Use main provider`
4. 支持 `Use auto`
5. 支持 `Custom endpoint`
6. embedding 步骤单独提醒模型能力要求

这与现有 TTS / vision 的体验更一致，也符合你说的“作为核心配置，参与一切处理流程”。

## 7.6 正式错误分级与用户可见提示

第一版必须为 graph-memory side-task 定义正式错误模型。

建议错误类别：

1. `GRAPH_RUNTIME_CONFIG_ERROR`
   - 配置缺失、provider/base_url/api_key 非法
2. `GRAPH_RUNTIME_CONNECT_ERROR`
   - 无法连到本地模型服务
3. `GRAPH_RUNTIME_TIMEOUT`
   - 调用超时
4. `GRAPH_RUNTIME_AUTH_ERROR`
   - 认证失败
5. `GRAPH_RUNTIME_MODEL_CAPABILITY_ERROR`
   - 误把 text model 当 embedding model，或 embedding 端点不支持当前接口
6. `GRAPH_EXTRACTION_PARSE_ERROR`
   - 模型输出不合规
7. `GRAPH_RECALL_DEGRADED`
   - recall 注入被跳过，但主链继续
8. `GRAPH_MAINTENANCE_DEFERRED`
   - maintenance/dream 被延期

### 7.6.1 用户可见行为

不是每个错误都该直接打断用户，但必须有统一规则：

#### 对用户直接可见

1. graph runtime 启动失败，且用户显式调用 graph 工具
2. graph_search / graph_record / graph_maintain 执行失败
3. graph feature 在配置中启用，但关键 runtime 全部不可用

建议提示风格：

- 明确说明失败子系统
- 说明主对话是否仍正常
- 提示检查项（模型服务、base_url、api_key、embedding endpoint）

例如：

```text
Graph memory is enabled but its embedding runtime is unavailable.
Main Hermes chat is still working.
Check: graph_memory.embedding.base_url, model availability, and local server health.
```

#### 对用户不直接打断，但记录到状态/日志

1. turn reflection 超时
2. extraction 延后
3. maintenance/dream 延后
4. recall 当前轮跳过

这些应：

1. 写日志
2. 更新 runtime health
3. 必要时向 `hermes status` / gateway health 暴露 degraded 状态

### 7.6.2 工具层错误返回

所有 graph 工具必须像 Hermes 原生工具一样，返回正式结构：

```json
{
  "success": false,
  "error_code": "GRAPH_RUNTIME_TIMEOUT",
  "error": "Graph reflection model timed out after 8s",
  "retryable": true,
  "subsystem": "graph_memory"
}
```

而不是只打印字符串错误。

## 7.7 启动校验与运行时探针

### 7.7.1 启动校验

当 `graph_memory.enabled=true` 时，Hermes 启动阶段应做最小校验：

1. text runtime 是否可解析
2. embedding runtime 是否可解析
3. base_url 是否可连
4. 模型 capability 是否匹配

但要注意：

- 不应因为 graph runtime 不可用而让 Hermes 主服务完全不可启动
- 应进入 `degraded`，除非用户显式设置 `graph_memory.required=true`

### 7.7.2 运行时探针

建议新增轻量 probe：

1. text runtime probe
   - ping models endpoint 或做极小 completion
2. embedding runtime probe
   - ping models endpoint 或做极小 embedding 请求

结果写入 runtime health。

## 7.8 超时与降级必须进入正式软件设计

这不应只是“实现细节”，而是设计级要求。

### 7.8.1 核心原则

1. graph side-task 的失败不得拖垮 Hermes 主对话
2. embedding/runtime 不可用时要降级，不是静默挂掉
3. 所有超时都要有统一错误码和健康状态更新

### 7.8.2 降级顺序

#### recall 路径

1. 向量 recall 失败
2. 回退到 FTS/keyword recall
3. 再失败则本轮跳过 graph recall block

#### extraction/reflection 路径

1. 小模型失败
2. 标记 pending/deferred
3. dream/idle 时重试
4. 不影响主对话

#### embedding 路径

1. embedding 不可用
2. 关闭 vector search / dedup / community vector search
3. 保留 FTS + graph walk

这点非常重要：

**embedding 模型故障不能让整套 graph-memory 直接报废，只能进入能力降级模式。**

---

## 8. 分阶段实施计划

## Phase 1：最小可用整合

目标：

1. graph DB 跑起来
2. 提供 `graph_search / graph_record / graph_stats / graph_maintain`
3. turn-level graph recall block 能动态注入

不做：

- auto extraction
- reflection
- maintenance 自动调度
- review 协同

完成标准：

- 用户能手动记录/搜索 graph memory
- LLM 每轮可看到 graph recall block

## Phase 2：自动写入与 recall pool

目标：

1. auto extraction
2. explicit memory
3. recall pool

完成标准：

- 一轮对话后，graph candidates 可持续进入后续回合的 recall

## Phase 3：reflection 与 finalize

目标：

1. self-reflection
2. session_end finalize
3. maintenance

完成标准：

- durable knowledge 能自动增长并治理

## Phase 4：Hermes review 协同

目标：

1. background review 读取 graph candidates
2. graph → memory/skill 升级通路打通

完成标准：

- graph 与 curated memory 形成双层闭环

## Phase 5：Dream 内部运行机制

目标：

1. 将现有夜间 dream 整理机制收编进 Hermes 内部运行时
2. 不再依赖外部 cron 作为唯一触发方式
3. 让 dream 与 graph maintenance / review / flush 共用统一任务调度框架

完成标准：

- 支持 idle 触发、夜间窗口触发、积压阈值触发
- 可手动触发
- dream 任务失败不会影响主对话链路

---

## 8.1 Dream 机制重构设计

## 8.1.1 结论

本次重构应当把 dream 从“外部 cron 驱动的离线整理脚本”改造成 **Hermes 内部低优先级后台维护机制**。

原因：

1. graph-memory 的 maintenance、candidate promotion、session finalize 与 dream 本质相邻。
2. 外部 cron 看不到 Hermes 的真实运行态：
   - 当前是否空闲
   - 是否有待 flush session
   - 是否已有积压候选
3. 内部任务机制更容易统一 timeout、重试、日志、指标与回滚策略。

## 8.1.2 Dream 的职责边界

dream 不负责：

1. 主链路 recall
2. 当前轮上下文注入
3. 最终直接写 `MEMORY.md` / `USER.md` / skills

dream 负责：

1. 聚合最近一段时间的 graph candidates
2. 跑批量 dedup / cluster / relation completion
3. 生成 promotion suggestions
4. 为 Hermes review 准备高置信候选包

## 8.1.3 建议触发条件

dream 触发不再只依赖“每天夜里固定时间”，而采用混合触发：

1. `night_window`
   - 本地时间进入夜间窗口，例如 02:00-05:00
2. `idle_trigger`
   - Hermes 长时间无活跃对话
3. `backlog_trigger`
   - graph candidate 数量、未维护节点数、待 promotion 数量超过阈值
4. `manual_trigger`
   - 用户或工具显式执行
5. `session_tail_trigger`
   - 一批 session_end 后延迟合并执行

## 8.1.4 代码设计

建议新增：

- `agent/graph_memory/dream.py`
- `agent/background_jobs.py`

`dream.py` 功能点：

1. 读取最近窗口内新增/高频节点
2. 聚合 recall 热点
3. 批量补关系
4. 生成 promotion queue
5. 调 maintenance 子步骤

`background_jobs.py` 功能点：

1. 注册低优先级后台任务
2. 管理：
   - 调度
   - timeout
   - concurrency
   - retry
   - cancellation

## 8.1.5 第一版约束

第一版 dream 只做：

1. manual trigger
2. idle trigger
3. night window trigger

第一版不做：

1. 分布式队列
2. 多 worker 并发 dream
3. 复杂优先级调度

这样可以先把 cron 依赖去掉，但不把运行时搞得太重。

---

## 9. 测试矩阵

## 9.1 单元测试

新增目录建议：

```text
tests/graph_memory/
  test_types.py
  test_config.py
  test_db.py
  test_store.py
  test_extractor.py
  test_reflection.py
  test_explicit.py
  test_recaller.py
  test_recall_pool.py
  test_formatter.py
  test_maintenance.py
  test_manager.py
```

目标：

- 算法正确性
- schema 正确性
- 边界行为
- fallback 行为

## 9.2 工具测试

新增：

- `tests/tools/test_graph_memory_tools.py`

目标：

- 工具 schema 正确
- handler 正确
- 输出格式稳定

## 9.3 Agent 集成测试

新增：

- `tests/test_run_agent_graph_memory.py`

关键用例：

1. 开启 graph_memory 后，当前轮出现动态注入块
2. `_cached_system_prompt` 不包含 graph recall block
3. `post_llm_call` 后 graph ingest 被触发
4. session_end 时 finalize 被触发
5. graph disabled 时完全无 side effects

## 9.4 Gateway 集成测试

新增：

- `tests/gateway/test_graph_memory_flush.py`
- `tests/gateway/test_graph_memory_session_reset.py`

关键用例：

1. inactivity flush 前 graph finalize 被调用
2. reset/new session 不丢 graph side effects
3. session key / cwd 传递正确

## 9.5 回归测试

必须跑现有相关测试：

- [test_run_agent.py](/Users/wzh/IsacHermes/tests/test_run_agent.py)
- [test_compression_persistence.py](/Users/wzh/IsacHermes/tests/test_compression_persistence.py)
- [test_compression_boundary.py](/Users/wzh/IsacHermes/tests/test_compression_boundary.py)
- [test_memory_tool.py](/Users/wzh/IsacHermes/tests/tools/test_memory_tool.py)
- [test_skill_manager_tool.py](/Users/wzh/IsacHermes/tests/tools/test_skill_manager_tool.py)
- [test_session_search.py](/Users/wzh/IsacHermes/tests/tools/test_session_search.py)
- [test_plugins.py](/Users/wzh/IsacHermes/tests/test_plugins.py)
- [test_async_memory_flush.py](/Users/wzh/IsacHermes/tests/gateway/test_async_memory_flush.py)
- [test_flush_memory_stale_guard.py](/Users/wzh/IsacHermes/tests/gateway/test_flush_memory_stale_guard.py)

---

## 9.6 Feature → Test 追踪矩阵（必须落地）

仅有测试列表还不够，必须建立 **需求-改动-测试** 三向追踪表。  
后续每个开发任务、每个 PR、每次回归，都必须引用 `Feature ID`。

建议新增文档：

- `wzh-research/graph-memory-feature-test-matrix.md`

建议字段：

1. `Feature ID`
2. `Feature Name`
3. `Change Type`
   - new / modified
4. `Primary Code Paths`
5. `Supporting Code Paths`
6. `Unit Tests`
7. `Integration Tests`
8. `Regression Tests`
9. `Negative Tests`
10. `Timeout/Failure Tests`
11. `Status`

示例：

| Feature ID | Feature | Primary Code Paths | Tests |
|---|---|---|---|
| `GM-CTX-001` | graph recall block 动态注入 | `run_agent.py`, `formatter.py`, `manager.py` | `test_graph_context_is_ephemeral`, `test_cached_prompt_excludes_graph_block`, `test_graph_budget_enforced` |
| `GM-REF-002` | turn-level reflection | `reflection.py`, `auxiliary_client.py` | `test_reflection_empty_result_no_write`, `test_reflection_timeout_fallback`, `test_reflection_uses_aux_model` |
| `GM-DRM-001` | internal dream trigger | `dream.py`, `background_jobs.py`, `gateway/run.py` | `test_dream_idle_trigger`, `test_dream_night_window_trigger`, `test_dream_job_timeout_does_not_block_agent` |

## 9.7 质量评估测试：本地小模型专项

除普通自动化测试外，必须增加 **小模型质量评估集**。

建议新增：

- `tests/graph_memory/evals/fixtures/*.json`
- `tests/graph_memory/test_small_model_eval_smoke.py`

评测场景至少包含：

1. 用户偏好
2. 环境事实
3. 一次性任务
4. 报错事件
5. 技能候选
6. 对比/分析结论
7. 用户纠正旧方案
8. 不应记忆的闲聊

评测指标至少包含：

1. node precision
2. node recall
3. edge precision
4. false positive rate
5. invalid promotion rate
6. preference misclassification rate
7. skill candidate usefulness rate

第一版要求：

1. 至少有离线 smoke eval
2. 至少有人工 spot check checklist
3. 每次更换本地小模型或 prompt 时必须重跑

## 9.8 覆盖性要求

后续开发必须遵守：

1. **每个新增 feature 必须至少有 1 个单测**
2. **每个跨模块改动必须至少有 1 个集成测试**
3. **每个 timeout / fallback 路径必须至少有 1 个失败测试**
4. **每个修改 Hermes 核心主链的 PR，必须列出受影响的回归测试**
5. **没有写入 Feature Matrix 的改动，不允许进入实现阶段**

这不能保证绝对“不漏、不多、不错”，但这是当前最接近工程可控的方式。

---

## 9.9 本地小模型 timeout / failure 设计

本地小模型不是“可能会慢一点”，而是必须按失败优先设计。

### 9.9.1 每类任务单独 timeout

建议配置项：

```yaml
graph_memory:
  reflection_timeout: 8
  extraction_timeout: 20
  maintenance_timeout: 60
  dream_timeout: 120
  review_triage_timeout: 10
```

### 9.9.2 失败处理策略

必须明确：

1. `reflection` 超时
   - 直接跳过
   - 不影响主链
2. `extraction` 超时
   - 标记该批消息为 `pending_retry`
   - 后续 idle/dream 可补跑
3. `graph recall` 超时
   - 当前轮不注入 graph recall block
   - Hermes 正常继续
4. `maintenance` 超时
   - 推迟到下次 dream / idle
5. `dream` 超时
   - 记录失败并中止本轮 job
   - 不阻塞 gateway / run_conversation
6. `memory_review_triage` / `skill_review_triage` 超时
   - 回退为“不做 triage”
   - 不得卡死 background review

### 9.9.3 输入预算护栏

除 timeout 外，还必须加：

1. `reflection_max_messages`
2. `extraction_max_messages`
3. `explicit_window_max_rows`
4. `community_summary_max_nodes`
5. `recall_max_nodes`
6. `recall_max_edges`

### 9.9.4 测试要求

新增测试必须覆盖：

- `test_reflection_timeout_fallback`
- `test_extraction_timeout_marks_pending`
- `test_graph_recall_timeout_skips_injection`
- `test_maintenance_timeout_reschedules`
- `test_dream_timeout_does_not_block_runtime`
- `test_review_triage_timeout_degrades_safely`

## 9.10 模型运行时与错误表面的正式测试

既然 graph 小模型和 embedding 模型进入 Hermes 源码主体系，就必须进入正式测试流。

新增测试建议：

### A. 运行时解析测试

- `tests/hermes_cli/test_graph_memory_runtime_provider.py`
  - `test_resolve_graph_reflection_runtime_from_config`
  - `test_resolve_graph_embedding_runtime_from_config`
  - `test_graph_runtime_local_base_url_detection`
  - `test_graph_embedding_runtime_rejects_text_only_model`

### A.1 setup / reconfigure / restore 测试

- `tests/hermes_cli/test_graph_memory_setup.py`
  - `test_setup_persists_graph_memory_runtime_config`
  - `test_setup_keep_current_graph_runtime`
  - `test_setup_reconfigure_single_graph_runtime`
  - `test_setup_embedding_prompts_for_dimensions`
- `tests/hermes_cli/test_graph_memory_config_restore.py`
  - `test_restore_preserves_existing_graph_memory_block`
  - `test_restore_backfills_missing_graph_memory_defaults`
- `tests/hermes_cli/test_graph_memory_config_migration.py`
  - `test_old_config_version_migrates_graph_memory_block`

### B. 启动/健康测试

- `tests/gateway/test_graph_memory_runtime_health.py`
  - `test_graph_runtime_health_reports_degraded_text_runtime`
  - `test_graph_runtime_health_reports_degraded_embedding_runtime`
  - `test_graph_runtime_health_includes_last_error`

### C. 工具错误表面测试

- `tests/tools/test_graph_memory_tools.py`
  - `test_graph_search_returns_structured_runtime_error`
  - `test_graph_record_returns_structured_runtime_error`
  - `test_graph_maintain_returns_structured_runtime_error`

### D. 降级路径测试

- `tests/graph_memory/test_recaller.py`
  - `test_recall_degrades_from_vector_to_fts`
- `tests/graph_memory/test_maintenance.py`
  - `test_maintenance_runs_without_embeddings`
- `tests/test_run_agent_graph_memory.py`
  - `test_graph_recall_skip_does_not_break_main_response`

### E. 端到端失败测试

- `tests/test_run_agent_graph_memory.py`
  - `test_graph_runtime_down_main_chat_still_works`
  - `test_graph_required_mode_blocks_startup_with_clear_error`

## 9.11 覆盖性结论

如果按当前设计执行，测试目标不是“graph-memory 自己能跑”，而是：

1. **graph side runtimes 被纳入 Hermes 正式运行时**
2. **所有关键失败路径都有正式错误表面**
3. **所有降级路径都有测试验证**
4. **主链不被 side-task 故障破坏**

这才符合你说的“像原生 Hermes 服务一样”的标准。

## 9.12 配置生命周期覆盖要求

graph-memory 作为核心配置后，测试覆盖必须再增加一层：

1. 初次 setup
2. rerun setup
3. keep current
4. 单项 reconfigure
5. config migration
6. restore/backfill defaults
7. CLI bridge
8. gateway bridge
9. doctor/status visibility

没有覆盖这些流程，就不能说它真正“进入 Hermes 的正式配置生命周期”。

---

## 10. 风险与规避

## 10.1 最大风险

### 风险 1：污染 Hermes 稳定 prompt

规避：

- graph recall 只能走 dynamic context

### 风险 2：自动抽取污染 curated memory

规避：

- graph 只先写 graph DB
- 升级到 memory/skills 必须走 review

### 风险 3：小模型误判

规避：

- 小模型只做候选层
- 最终制度化写入仍由强模型决策

### 风险 4：双压缩系统互相打架

规避：

- 第一版彻底禁用 graph 的 transcript assembly 逻辑
- Hermes 继续掌控 message compression

### 风险 5：集成面过大，首版难落地

规避：

- 严格按 Phase 1 → 4 渐进

### 风险 6：dream 内部化后抢占主链资源

规避：

- dream 仅以低优先级后台 job 执行
- 严格并发限制
- 夜间/空闲窗口优先
- 超时后立即中止

### 风险 7：本地小模型质量波动导致候选污染

规避：

- 小模型仅做候选层
- 强模型做最终写入
- 引入 eval fixtures + 人工 spot check

---

## 11. 第一稿结论

这套整合可以做，而且值得做，但必须按 Hermes 的架构哲学来做：

- **graph-memory 负责知识网络**
- **Hermes 负责稳定上下文、精选记忆、技能制度化**

如果按这个设计推进，最终系统会形成：

1. `MEMORY.md` / `USER.md`：精选事实层
2. Hermes skills：过程性制度层
3. session_search：历史检索层
4. graph-memory：durable knowledge network 层
5. graph recall block：动态召回层

这会比任何“粗暴把 graph-memory 当第二个 context engine 塞进 Hermes”的路线都稳得多，也更适合长期迭代。
