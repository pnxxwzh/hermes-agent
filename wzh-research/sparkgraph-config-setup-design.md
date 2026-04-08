# SparkGraph 核心配置与 Setup 流程设计

## 1. 设计目标

本设计专门回答一个问题：

**SparkGraph 既然是 Hermes 系统机制的一部分，它应当怎样像主模型、TTS、vision 一样进入正式配置生命周期？**

目标：

1. SparkGraph 作为核心配置块进入 `DEFAULT_CONFIG`
2. 初次 setup、重新配置、配置恢复、配置迁移都覆盖 SparkGraph
3. 交互风格参考现有：
   - 主模型 provider 选择
   - `auxiliary.vision`
   - `tts`
4. 不通过“是否启用”来组织，而通过“runtime 如何配置”来组织

额外硬约束：

5. SparkGraph 的配置作用域和存储作用域必须跟随 Hermes 官方 `HERMES_HOME/profile` 模型
6. 第一版不自行引入单 profile 内多长期 agent namespace

---

## 2. 顶层配置结构

建议新增顶层块：

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

说明：

1. `db_path` 默认留空，由运行时按当前 `HERMES_HOME` 自动解析
2. 默认推荐路径应为：
   - default profile: `~/.hermes/graph-memory/default.db`
   - named profile: `~/.hermes/profiles/<name>/graph-memory/default.db`
3. 不推荐把默认路径设计成 profile 外的全局共享路径

## 2.1 为什么不用 `auxiliary.*`

不放到 `auxiliary.*` 下，是因为 SparkGraph 不只是 side-task：

1. 它有自己的 DB
2. 有 recall / maintenance / dream
3. 有完整的 subsystem lifecycle

但它的子 runtime 表达方式必须参考 `auxiliary`：

- `provider`
- `model`
- `base_url`
- `api_key`
- `timeout`

## 2.2 Namespace / Storage 兼容性规则

SparkGraph 必须遵守以下兼容性规则：

1. 以当前 `HERMES_HOME` 为根组织 durable graph 存储
2. setup / reconfigure / restore / migration 只处理当前 profile 的 graph-memory 配置与路径
3. doctor / health / status 只报告当前 profile 的 graph-memory 状态
4. 不增加“选择 agent namespace”之类偏离 Hermes 官方模型的 setup 问题
5. 如果用户手动自定义 `db_path` 指到 profile 外部，系统应允许但要明确提示这会偏离官方推荐组织方式

---

## 3. `DEFAULT_CONFIG` 设计

`hermes_cli/config.py` 中应新增默认结构，且永远存在。

设计原则：

1. 新装用户即使从未配置 SparkGraph，也有完整结构
2. setup 不需要先问“开不开”
3. 代码读取时不需要到处判空整个 `graph_memory`

建议默认值：

- `required: false`
  - 默认降级，不阻塞主 Hermes
- `probe_on_startup: true`
  - 默认探测运行时健康
- `provider: auto`
  - 默认像 auxiliary 一样自动解析
- `model/base_url/api_key: ""`
  - 空值表示走 provider 默认/主配置/显式解析
- `db_path: ""`
  - 空值表示按当前 `HERMES_HOME/profile` 自动解析默认 graph DB 路径

---

## 4. Setup 流程设计

## 4.1 总体原则

SparkGraph setup 应遵循以下原则：

1. 不问“要不要启用 SparkGraph”
2. 直接问每个 runtime 怎么配置
3. 每一步都提供：
   - `Keep current (...)`
   - `Use main provider`
   - `Use auto`
   - `Custom endpoint`
4. embedding 单独一步
5. 每一步结束后写回 config 内存态，最后统一保存

## 4.2 入口设计

建议接入这些入口：

1. `hermes setup`
   - 全量向导的一部分
2. `hermes setup sparkgraph`
   - 只配置 SparkGraph
3. `hermes config edit`
   - 手工编辑
4. `hermes config set graph_memory....`
   - 精确改单项

如果暂时不想新增 `hermes setup sparkgraph` 子命令，至少要把 SparkGraph 纳入：

- `hermes setup tools`
  或
- 新增一个 setup section

我更推荐：

- **单独 section**

因为它已经不是“一个工具提供商”，而是系统机制。

## 4.3 初次 setup 顺序

建议放在：

1. 主模型 provider 配置之后
2. terminal/backend 之前或 tools 之前都可以

推荐顺序：

1. Model & Provider
2. Graph Memory
3. Terminal Backend
4. Agent Settings
5. Messaging
6. Tools

原因：

- graph-memory 和主模型/runtime 关系很强
- 又比终端/网关更基础

---

## 5. graph-memory Setup 子步骤

每个子步骤都应以当前配置为基线。

## 5.1 Graph Reflection Runtime

显示文案建议：

```text
Graph Reflection Runtime
Used for turn-level reflection and candidate rule/preference extraction.
```

选项建议：

1. `Keep current (<provider/model/base_url summary>)`
2. `Use main provider/runtime`
3. `Use auto`
4. `Custom OpenAI-compatible endpoint`

行为：

- `Keep current`
  - 不改当前子块
- `Use main provider/runtime`
  - `provider: main`
  - `base_url/api_key/model` 清空或跟随主模型
- `Use auto`
  - `provider: auto`
  - 其余清空
- `Custom endpoint`
  - 继续问：
    - `base_url`
    - `api_key`
    - `model`
    - `timeout`

## 5.2 Graph Extraction Runtime

与 reflection 相同，但文案说明：

```text
Graph Extraction Runtime
Used to extract TASK/SKILL/EVENT nodes and graph edges from recent conversation turns.
```

默认建议：

- 若用户选择本地小模型路线，可默认和 reflection 共用

## 5.3 Graph Maintenance Runtime

文案：

```text
Graph Maintenance Runtime
Used for finalize, community summaries, dream-time maintenance, and candidate promotion prep.
```

与前两项类似，但超时默认更长。

## 5.4 Graph Embedding Runtime

这是最关键的特殊步骤。

文案必须明确：

```text
Graph Embedding Runtime
Used for vector recall, dedup, and community vector search.
This must be an embedding-capable model/endpoint, not a normal chat model.
```

选项建议：

1. `Keep current (...)`
2. `Use main endpoint (only if it supports embeddings)`
3. `Use auto`
4. `Custom embedding endpoint`

如果选 `Custom embedding endpoint`，继续问：

1. `base_url`
2. `api_key`
3. `embedding model`
4. `dimensions`（可空，0 表示自动）
5. `timeout`

## 5.5 Shared graph-memory behavior settings

在 runtime 步骤之后，再问系统行为项：

1. `required`
   - `Degraded mode allowed` / `Require graph runtime`
2. `probe_on_startup`
   - `yes/no`
3. `recall_max_nodes`
4. `recall_max_depth`
5. `graph_budget_ratio`

如果不想初次 setup 太重，可以把 3-5 放高级模式，默认直接落默认值。

---

## 6. `Keep current` 设计

这部分必须明确，否则重配体验会很差。

## 6.1 显示摘要格式

每个子 runtime 的 `Keep current (...)` 建议显示：

- provider
- model
- base_url 简写

例如：

```text
Keep current (custom / qwen3-4b / http://127.0.0.1:8000/v1)
```

embedding 额外显示：

```text
Keep current (custom / bge-m3 / http://127.0.0.1:8000/v1 / dim=1024)
```

## 6.2 保持当前的语义

`Keep current` 必须做到：

1. 不覆盖已有子块
2. 不因为 rerun setup 把未知字段抹掉
3. 不强行重写默认值

---

## 7. Reconfigure 设计

需要两类重配路径。

## 7.1 全量 rerun setup

入口：

- `hermes setup`

要求：

1. 每个 graph 子步骤都提供 `Keep current`
2. 用户可以只改其中一个 runtime
3. 未修改的子块不被覆盖

## 7.2 定向重配

建议后续支持：

- `hermes setup graph-memory`

进入后可选：

1. `Reflection runtime`
2. `Extraction runtime`
3. `Maintenance runtime`
4. `Embedding runtime`
5. `Behavior settings`
6. `Run probes now`
7. `Restore defaults`

---

## 8. Restore / Migration 设计

## 8.1 Config migration

当旧版本配置中不存在 `graph_memory` 时：

1. 自动回填默认结构
2. bump `_config_version`
3. 不要求用户马上重跑 setup

## 8.2 Partial restore/backfill

如果用户有半残配置，例如：

- 只有 `graph_memory.embedding`
- 没有 `graph_memory.reflection`

系统应：

1. 自动 backfill 缺失字段
2. 保留已有值
3. 不覆盖用户手改内容

## 8.3 Restore defaults

建议 setup 子菜单里提供：

1. `Restore defaults for this runtime`
2. `Restore all graph-memory defaults`

语义：

- 只重置 graph-memory，不碰主模型、tts、vision

---

## 9. CLI / Gateway / Background Job Bridge

既然 graph-memory 是核心配置，它必须被所有运行入口统一加载。

## 9.1 CLI

CLI 启动时必须：

1. 读取 `graph_memory` 配置
2. 初始化 graph runtime resolver
3. 写入 agent/runtime 可访问的统一配置对象

## 9.2 Gateway

gateway 启动时也必须：

1. 读取同一份 `graph_memory` 配置
2. 初始化同样的 runtime bridge
3. health/status 中显示 graph runtime 状态

## 9.3 Background jobs / dream

内部 dream、maintenance、review triage 等后台任务不能偷偷绕过配置，必须走同一解析链。

---

## 10. Setup 后的 Probe 流程

每次 graph-memory setup 完成后，建议立即做 probe。

## 10.1 Probe 顺序

1. reflection runtime probe
2. extraction runtime probe
3. maintenance runtime probe
4. embedding runtime probe

## 10.2 Probe 输出

建议像原生 setup 一样给正式结果：

```text
✓ Graph reflection runtime reachable
✓ Graph extraction runtime reachable
⚠ Graph maintenance runtime timed out (degraded mode will be used)
✗ Graph embedding runtime invalid: selected model does not expose embeddings
```

## 10.3 保存策略

probe 失败时：

1. **不要自动丢弃用户配置**
2. 保存配置
3. 记录 degraded / invalid 状态
4. 提醒用户稍后可用 `doctor` / `setup graph-memory` 修复

---

## 11. 文档与状态显示

## 11.1 `hermes config`

必须能显示 graph-memory 当前配置摘要：

1. reflection runtime
2. extraction runtime
3. maintenance runtime
4. embedding runtime
5. required/degraded

## 11.2 `hermes doctor`

建议新增 graph-memory 检查项：

1. graph DB writable?
2. reflection runtime OK?
3. extraction runtime OK?
4. maintenance runtime OK?
5. embedding runtime OK?

## 11.3 gateway/runtime health

gateway health 中必须可见：

1. graph runtimes 的 degraded/fatal 状态
2. 最近错误消息

---

## 12. 测试设计

建议新增测试：

### Setup

- `tests/hermes_cli/test_graph_memory_setup.py`
  - `test_setup_persists_graph_memory_runtime_config`
  - `test_setup_keep_current_graph_runtime`
  - `test_setup_reconfigure_single_graph_runtime`
  - `test_setup_embedding_prompts_for_dimensions`
  - `test_setup_restore_graph_memory_defaults`

### Config migration / restore

- `tests/hermes_cli/test_graph_memory_config_migration.py`
  - `test_old_config_version_migrates_graph_memory_block`
  - `test_partial_graph_memory_block_backfilled`
- `tests/hermes_cli/test_graph_memory_config_restore.py`
  - `test_restore_preserves_existing_graph_memory_values`

### Bridge

- `tests/test_graph_memory_config_bridge.py`
- `tests/hermes_cli/test_graph_memory_gateway_bridge.py`

### Probe / health

- `tests/gateway/test_graph_memory_runtime_health.py`
- `tests/hermes_cli/test_graph_memory_doctor.py`

---

## 13. 结论

graph-memory 既然是 Hermes 系统机制的一部分，就必须在配置层被当作：

- 不是插件
- 不是 setup 的可选附加项
- 不是研究阶段手填字段

而是：

- **核心配置块**
- **正式 setup 项**
- **正式 migration / restore / bridge / doctor / health 项**

这才和你要求的“像主模型、TTS、vision 一样参与一切处理流程”一致。
