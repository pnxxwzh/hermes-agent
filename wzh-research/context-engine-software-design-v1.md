# Spark Context Engine 软件设计（第一稿）

> 分支：`context-research`
>
> 本文档描述 Spark 在 Hermes 底座上的上下文引擎重构第一稿。
> 目标不是立刻改变产品行为，而是在**外部无感、功能不变**的前提下，
> 把当前分散的上下文拼装逻辑收敛成一个可演进、可观测、可预算的统一模块。

## 1. 设计目标

本阶段的目标只有三个：

1. 把当前分散在 `run_agent.py`、`agent/prompt_builder.py`、`agent/sparkgraph/*`、
   plugins、Honcho 等处的上下文来源，统一纳入一个显式的 `context engine`。
2. 在**不改变任何外部功能和行为**的前提下，复现当前的 system prompt / dynamic context 组装结果。
3. 保留并升级 Hermes 现有的 context 进度条，使其成为未来上下文治理的统一观测入口。

### 明确非目标

第一阶段不做以下事：

1. 不改变 `MEMORY.md` / `USER.md` / SparkGraph recall 的注入顺序。
2. 不改变 compression 触发阈值和时机。
3. 不新增用户可见配置。
4. 不引入 OpenClaw 式完整 context engine 主导权。
5. 不在第一阶段改变任何 tool output / visible transcript / project context 策略。

---

## 2. 现状判断

当前 Spark/Hermes 已经存在两个重要“总入口”，但还没有真正独立的 context engine。

### 2.1 已有的总入口

1. **系统提示词总装配入口**
   - `run_agent.py::_build_system_prompt()`
   - 负责 stable system 层。

2. **每轮 API 调用前的最终拼装入口**
   - `run_agent.py` 主循环内的 `effective_system` 组装。
   - 负责把 ephemeral system、plugin context、SparkGraph recall、Honcho turn context 拼进去。

3. **上下文计量与压缩入口**
   - `ContextCompressor`
   - CLI status bar / context pressure warning / compression 都通过它的 token 统计工作。

### 2.2 当前的主要问题

虽然存在总入口，但上下文相关职责仍然是分散的：

1. source 分散
   - SOUL、skills、project context、memory/user、plugin context、SparkGraph recall、Honcho recall 等来源分布在不同模块。

2. 组装顺序隐式
   - 最终顺序埋在 `run_agent.py` 逻辑里，不容易抽查和测试。

3. 计量口径不统一
   - 某些真实 API 调用后的 token 统计包含 SG recall。
   - 但 preflight compression 和压缩后的本地重估未必包含 SG recall。

4. 未来演进困难
   - 如果要做 source budget、project context 分层、tool transcript budget、visible history 等增强，当前架构改动面会太大。

---

## 3. 关键结论

### 3.1 现有 context 进度条必须保留

CLI 上的 context 进度条是一个非常好的产品特征，必须保留，而且未来要增强。

当前它读取的是：

- `agent.context_compressor.last_prompt_tokens`
- `agent.context_compressor.context_length`

也就是说，它不是自己重新估算，而是依赖 `ContextCompressor` 的统一计量结果。

### 3.2 SparkGraph 现在对进度条的“汇报”是不完整的

当前事实：

1. **真实 API 调用后的统计通常包含 SG**
   - 因为 `last_prompt_tokens` 会用 provider 返回的真实 `prompt_tokens` 更新。
   - 此时请求中的 `effective_system` 已经拼入 SparkGraph recall。

2. **但预估链路并不完全包含 SG**
   - preflight compression 使用 `active_system_prompt`，此时 SG turn recall 还未拼入。
   - `_compress_context()` 压缩后的 `_compressed_est` 也没有把 turn-level SG recall 算进去。

因此：

- “调用后真实状态”大致包含 SG
- “调用前预估状态”口径不完整

这不是功能错误，但说明当前计量模型不统一。

### 3.3 第一阶段 context engine 必须把“计量模型”作为正式职责之一

新的 context engine 不能只负责“拼装文本”，还必须输出统一的上下文计量结果。

这意味着它将同时成为：

1. 上下文装配入口
2. 上下文计量入口
3. 上下文观测入口

---

## 4. 总体设计原则

第一阶段遵守以下 7 条原则：

1. **零行为变化优先**
   同一输入下，最终 `stable_system`、`dynamic_system`、`effective_system` 必须与旧逻辑一致。

2. **外部无感**
   不改变 CLI、gateway、tool 调用、session、config 的外部行为。

3. **总调度仍在 `run_agent.py`**
   不推翻 Hermes 的主循环，只把上下文拼装与计量逻辑抽到独立模块。

4. **source 与 assembler 分离**
   各模块只负责“产出自己的 chunk”，不负责全局装配。

5. **stable / dynamic 显式分层**
   未来一切预算与 caching 策略都要建立在这层边界之上。

6. **计量与拼装并行输出**
   `context engine` 的输出不只是字符串，还要有来源级 token 信息。

7. **先做兼容壳，再做策略升级**
   第一阶段只复现现状；第二阶段再调整策略。

---

## 5. 模块组织

建议新增：

```text
agent/context_engine/
  __init__.py
  models.py
  context.py
  sources.py
  registry.py
  assembler.py
  metrics.py
  compat.py
```

### 5.1 `models.py`

定义核心数据模型：

- `ContextChunk`
- `AssemblyContext`
- `AssemblyResult`
- `ContextMetrics`
- `SourceMetrics`

建议结构：

```python
@dataclass
class ContextChunk:
    source: str
    stage: Literal["stable", "dynamic"]
    slot: str
    priority: int
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class SourceMetrics:
    source: str
    stage: Literal["stable", "dynamic"]
    char_count: int
    rough_tokens: int
    included: bool = True

@dataclass
class ContextMetrics:
    stable_tokens: int
    dynamic_tokens: int
    total_estimated_tokens: int
    by_source: list[SourceMetrics]

@dataclass
class AssemblyResult:
    stable_chunks: list[ContextChunk]
    dynamic_chunks: list[ContextChunk]
    stable_system: str
    dynamic_system: str
    metrics: ContextMetrics
```

### 5.2 `context.py`

定义装配阶段的执行上下文：

```python
@dataclass
class AssemblyContext:
    agent: "AIAgent"
    system_message: str | None
    user_message: str | None
    cwd: str | None
    conversation_history: list[dict[str, Any]]
```

### 5.3 `sources.py`

定义 source 协议和内置 source。

统一协议：

```python
class ContextSource(Protocol):
    name: str
    stage: Literal["stable", "dynamic"]

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        ...
```

第一阶段内置 source：

#### stable

1. `IdentitySource`
2. `ToolGuidanceSource`
3. `SystemMessageSource`
4. `MemorySource`
5. `UserProfileSource`
6. `SkillsSource`
7. `ProjectContextSource`
8. `TimePlatformSource`

#### dynamic

1. `EphemeralSystemSource`
2. `PluginTurnContextSource`
3. `SparkGraphRecallSource`
4. `HonchoTurnSource`

### 5.4 `registry.py`

第一阶段使用显式注册表，而不是动态发现：

```python
STABLE_SOURCE_FACTORIES = [...]
DYNAMIC_SOURCE_FACTORIES = [...]
```

这样做是为了：

1. 最大程度复现旧顺序
2. 易于 snapshot test
3. 易于逐步替换 `run_agent.py`

### 5.5 `assembler.py`

负责：

1. 调用 source `collect()`
2. 丢弃空 chunk
3. 按 `priority` 和注册顺序排序
4. 拼出 `stable_system` / `dynamic_system`
5. 调用 `metrics.py` 生成统一计量结果

### 5.6 `metrics.py`

这是本设计新增的关键文件。

它负责统一计算：

1. 每个 source 的 char/tokens
2. stable/dynamic 层的估算 tokens
3. 总上下文估算 tokens
4. 可供 status bar / context pressure / compression 读取的统一结果

第一阶段允许它继续使用当前 Hermes 的 rough token 估算逻辑，但必须把 source 级明细沉淀下来。

### 5.7 `compat.py`

兼容层只做一件事：

- 复用旧逻辑，确保新 source 能包装现有实现而不是重写实现

例如：

- `ProjectContextSource` 调旧的 `build_context_files_prompt()`
- `SparkGraphRecallSource` 调旧的 `build_recall_block()`
- `MemorySource` / `UserProfileSource` 调旧的 `MemoryStore.format_for_system_prompt()`

---

## 6. 统一计量设计

这是本次设计相对上一版讨论新增的重点。

### 6.1 为什么必须统一计量

当前系统的计量至少有三条链：

1. API 调用后的 provider usage 更新
2. preflight compression 前的 rough estimate
3. 压缩后 `_compressed_est` 的本地重估

它们现在不是完全同口径，SparkGraph recall 就在这些口径之间出现了“后算进、前没算进”的情况。

如果未来继续做：

- project context 分层
- visible history
- tool transcript budget
- source budget

那这种口径分裂会越来越难维护。

### 6.2 第一阶段统一计量的目标

第一阶段不改变用户可见行为，但要引入一个统一的**装配级估算结果**：

- `stable_tokens`
- `dynamic_tokens`
- `sparkgraph_tokens`
- `project_context_tokens`
- `memory_tokens`
- `user_tokens`
- `history_tokens`（后续扩展）
- `tool_transcript_tokens`（后续扩展）

其中：

- status bar 可以继续沿用旧展示方式
- 但内部应改为优先读取 `AssemblyResult.metrics`

### 6.3 进度条的保留与升级

第一阶段：

1. 继续保留现有进度条 UI
2. 继续显示：
   - 已用 tokens
   - context length
   - 百分比

第二阶段可升级为 source-aware：

- stable system: 2.0k
- project context: 18.2k
- sparkgraph recall: 0.6k
- history: 14k
- tool transcript: 3k

也就是说，进度条未来不只是一个 bar，而是 `context inspector` 的入口。

### 6.4 SG 在新计量中的要求

第一阶段明确规定：

1. SparkGraph recall 作为 dynamic source 提供 chunk
2. SparkGraph recall 的 token 估算必须进入 `ContextMetrics.by_source`
3. preflight compression 使用的估算必须能够读取到这部分 dynamic source
4. 压缩后本地重估也要能选择是否纳入当轮 dynamic source

这样才能彻底解决“SG 已进入实际请求，但计量有时漏算”的问题。

---

## 7. 接入方式

### 7.1 稳定层接入

先让 `_build_system_prompt()` 内部调用新的 assembler，但只取：

- `AssemblyResult.stable_system`

对外行为保持不变。

### 7.2 动态层接入

每轮 API 调用前，统一通过 assembler 拿：

- `AssemblyResult.dynamic_system`

然后继续按旧逻辑拼成 `effective_system`。

### 7.3 计量接入

第一阶段需要同时保留现有 `ContextCompressor`，但让它开始接受新的统一估算输入。

建议做法：

1. 保留 `last_prompt_tokens` / `context_length` 字段
2. 新增可选字段：
   - `last_assembled_metrics`
3. status bar 继续默认显示旧信息
4. 内部调试时可读取 source 级 metrics

这样做的好处是：

- 用户无感
- `ContextCompressor` 不被推翻
- 进度条的产品价值被完整保留

---

## 8. 测试设计

第一阶段必须有两类测试。

### 8.1 行为兼容测试

目标：新旧输出完全一致。

建议新增：

- `tests/context_engine/test_stable_assembly.py`
- `tests/context_engine/test_dynamic_assembly.py`
- `tests/context_engine/test_compat_snapshots.py`

覆盖：

1. 空环境
2. 有 `SOUL.md`
3. 有 `MEMORY.md / USER.md`
4. 有 `AGENTS.md`
5. 有 SparkGraph recall
6. 有 plugin context
7. 有 Honcho turn context

断言：

1. 旧逻辑输出 == 新逻辑输出
2. chunk 顺序稳定
3. stable/dynamic 分层正确

### 8.2 计量一致性测试

目标：新的 `ContextMetrics` 至少不比现状更差。

新增建议：

- `tests/context_engine/test_metrics.py`

覆盖：

1. SG recall 存在时，`by_source` 中必须有 `sparkgraph_recall`
2. 动态层非空时，`dynamic_tokens > 0`
3. status bar 可继续从旧字段得到合法百分比
4. preflight estimate 与 assembled metrics 的差值在可接受范围

---

## 9. 代码量评估

第一阶段只做“零行为变化 + 统一计量接入”，预计：

- 新增模块：`600 ~ 900` 行
- `run_agent.py` 接入改动：`150 ~ 250` 行
- 测试：`300 ~ 500` 行

总量大约：

- `1000 ~ 1600` 行

仍然属于可控范围，不算另起炉灶。

---

## 10. 与官方分叉影响

第一阶段结论：

- **不会彻底和官方分道扬镳**
- 但会在“上下文编排层”形成明确的 Spark 架构边界

这是一个健康的分叉：

1. 外部行为不变
2. 主产品壳仍是 Hermes
3. provider / gateway / tools / sessions 主链不动
4. 但上下文装配的组织方式开始归 Spark 所有

真正与官方明显分叉，会发生在第二阶段策略调整时，比如：

- project context 降噪
- source budget
- visible history
- tool transcript budget
- more GM-like assembly policy

---

## 11. 第一阶段实施顺序

建议顺序：

1. 建 `models.py / context.py / registry.py / assembler.py / metrics.py / compat.py`
2. 先接 dynamic sources
3. 再接 stable sources
4. 补 snapshot tests
5. 最后切 `run_agent.py` 主入口
6. 再把 status bar 的统计口切到统一 metrics 结果

这样风险最低，也最容易确认“进度条保留且不失真”。

---

## 12. 当前共识摘要

本设计当前已经明确的共识有：

1. 不回 OpenClaw，不做重型 context engine 迁移。
2. 在 Hermes 上新增一层 Spark 自己的 context engine。
3. 第一阶段必须是零行为变化重构。
4. CLI 的 context 进度条必须保留。
5. SparkGraph 当前对进度条的上报不完整，必须在新架构里统一口径。
6. 新 context engine 不只负责拼装文本，还要负责上下文计量。

这 6 条可作为 `context-research` 分支的第一批硬约束。
