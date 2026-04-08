# 输入模型上下文完整统计改造计划 v1

> 仓库: `IsacHermes`
> 创建: 2026-04-08
> 状态: 设计完成，待实施
> 目标: 让 CLI/调试视图能够完整分析“实际输入模型的上下文构成”，而不仅是 ContextEngine 产出的 system-context sources

---

## 1. 背景与问题定义

当前实现存在两套不同语义的统计：

1. `ContextEngine` source 统计
   仅统计 `ContextChunk`，即 `identity`、`memory`、`project_context`、`plugin`、`sparkgraph_recall` 等由 context engine 产生的 stable/dynamic system prompt 内容。

2. 请求总量粗估
   在真正发请求前，使用 `estimate_request_tokens_rough(...)` 估算整次请求 token，但没有对应的可视化 breakdown。

这带来一个明显问题：

- CLI 上看到的 “context breakdown/source breakdown” 并不等于“真正输入模型的完整上下文”
- `conversation history / messages`
- `assistant tool_calls`
- `role="tool"` 的工具结果消息
- `prefill_messages`
- `tools` schema

这些输入要么完全未出现在 breakdown 里，要么只反映为总 token 数的一部分，用户无法知道真正的大头在哪里。

这会直接影响以下场景：

- 定位为什么 prompt/context 爆炸
- 判断是 `project_context` 太大，还是历史消息太长
- 判断是 tool schema 占用过高，还是 tool result 消息占用过高
- 为 compression、tool pruning、history trimming 做产品决策

---

## 2. 改造目标

### 2.1 核心目标

提供一套“请求级输入构成统计”，覆盖实际送入模型的全部主要输入桶：

- stable context
- dynamic context
- conversation messages
- tool result messages
- prefill messages
- tool schemas

并在 CLI 中默认展示这套完整 breakdown。

### 2.2 非目标

本次不追求：

- provider 精确 tokenizer 级别统计
- 按每条 message 精确计费
- 为所有 API 模式建立完全不同的记账器
- 改变 prompt caching 行为
- 改变实际请求 payload

---

## 3. 设计原则

1. 统计不应改变请求内容
   所有改造都必须是旁路观测，不影响现有 prompt 组装和缓存。

2. 区分“上下文来源统计”和“请求输入统计”
   `ContextEngine` source metrics 与 request-level metrics 不是一个概念，不能强行混成一个抽象。

3. 复用现有 rough token 口径
   继续使用 Hermes 当前的 `chars / 4` 粗估逻辑，确保新旧统计口径一致。

4. 渐进上线
   先补 request breakdown，再决定是否保留原 context-only breakdown 作为 debug 视图。

5. 不破坏现有调试心智
   老的 `identity / memory / project_context` 视图仍然有价值，应保留为二级视图或底层指标。

---

## 4. 推荐方案总览

## 4.1 方案结论

不要把 `messages` 直接伪装成 `ContextSource`。

推荐新增一层与 `ContextMetrics` 并行的模型：

- `RequestMetrics`
- `RequestBucketMetrics`

其中：

- `ContextMetrics` 继续表达 ContextEngine 的 system/context source 构成
- `RequestMetrics` 表达“本次真实发给模型的输入构成”

CLI 默认读取 `RequestMetrics`
ContextEngine 原始 breakdown 作为详细调试视图保留

## 4.2 为什么不直接扩展 `ContextMetrics`

直接把 `messages` 塞进 `ContextMetrics.by_source` 会出现语义污染：

- `ContextMetrics` 当前名字和实现都围绕 `ContextChunk`
- `messages`、`tools`、`prefill_messages` 并不是 `collect()` 产物
- 后续代码会误以为这些桶都具有 stable/dynamic source 语义

所以更合适的做法是：

- 保留 `ContextMetrics`
- 新增 `RequestMetrics`
- 在 `RequestMetrics` 中引用 `ContextMetrics` 的结果或将其展平

---

## 5. 数据模型设计

建议新增文件：

- `agent/context_engine/request_metrics.py`

或合并到：

- `agent/context_engine/models.py`

推荐优先新文件，避免 `models.py` 同时承载两层不同语义。

### 5.1 `RequestBucketMetrics`

```python
@dataclass
class RequestBucketMetrics:
    bucket: str
    char_count: int
    rough_tokens: int
    included: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 5.2 `RequestMetrics`

```python
@dataclass
class RequestMetrics:
    total_estimated_tokens: int
    total_char_count: int
    by_bucket: list[RequestBucketMetrics]
    context_metrics: ContextMetrics | None = None

    def get_bucket(self, name: str) -> RequestBucketMetrics | None: ...
```

### 5.3 推荐 bucket 集合

第一阶段建议支持以下 bucket：

- `context_identity`
- `context_tool_guidance`
- `context_tool_use_enforcement`
- `context_honcho_static`
- `context_system_message`
- `context_memory`
- `context_user_profile`
- `context_skills`
- `context_project`
- `context_time_platform`
- `context_ephemeral`
- `context_plugin`
- `context_sparkgraph_recall`
- `context_honcho_turn`
- `messages_user`
- `messages_assistant`
- `messages_tool`
- `messages_other`
- `prefill_messages`
- `tool_schemas`

### 5.4 CLI 标签映射

建议 UI 友好标签如下：

- `context_project` -> `project`
- `messages_user` -> `msg:user`
- `messages_assistant` -> `msg:asst`
- `messages_tool` -> `msg:tool`
- `prefill_messages` -> `prefill`
- `tool_schemas` -> `tools`

如果状态栏空间不足，可先聚合为：

- `context`
- `msg:user`
- `msg:asst`
- `msg:tool`
- `tools`

详细 breakdown 再展开所有细项。

---

## 6. 统计口径设计

## 6.1 口径原则

每个输入只算一次，不允许重复计入。

### 6.2 推荐记账方式

1. Context buckets
   直接从 `stable_result` / `dynamic_result` 的 chunk 内容统计，逐 source 进入 `context_*` bucket。

2. Conversation messages
   统计最终发送给模型的 message 列表中的非-system 消息。

3. Prefill messages
   单独统计 `self.prefill_messages`，不要混进普通 `messages_*`。

4. Tool schemas
   单独统计 `self.tools`。

5. System prompt
   不再额外创建一个笼统的 `system` bucket，避免和 `context_*` 重复；system 中各部分由 context buckets 承担。

### 6.3 `messages` 分类规则

对实际发送给模型的消息按 role 分类：

- `role == "user"` -> `messages_user`
- `role == "assistant"` 且不为 prefill -> `messages_assistant`
- `role == "tool"` -> `messages_tool`
- 其他 role -> `messages_other`

### 6.4 assistant 中 tool_calls 的归属

assistant 消息里的 `tool_calls` 字段属于 assistant message payload 的一部分，应计入：

- `messages_assistant`

理由：

- 它实际跟随 assistant message 一起发送
- 避免和 `messages_tool` 重叠
- `messages_tool` 应专指 `role="tool"` 的工具结果消息

### 6.5 prefill messages 的归属

尽管 prefill 最终也插入 `api_messages`，仍建议单独桶：

- `prefill_messages`

原因：

- 它们不是会话历史的一部分
- 对 few-shot 成本分析很重要
- 和用户/助手真实历史分开更有调试价值

---

## 7. 实现方案

## 7.1 Phase 1: 纯数据层改造

目标：

- 增加 request-level metrics 结构
- 在 `run_agent.py` 真正发请求前生成 `_last_request_metrics`

涉及文件：

- `agent/context_engine/models.py` 或新增 `agent/context_engine/request_metrics.py`
- `run_agent.py`

实施步骤：

1. 定义 `RequestBucketMetrics` / `RequestMetrics`
2. 新增帮助函数：
   - `rough_tokens_from_text(text: str) -> int`
   - `rough_tokens_from_message(msg: dict) -> int`
   - `build_request_metrics(...) -> RequestMetrics`
3. 在 `run_agent.py` 每次构造完最终 `effective_system`、`api_messages`、`prefill_messages`、`tools` 后，生成并缓存：
   - `self._last_request_metrics`
4. 保留：
   - `self._last_context_metrics`
   - `self._stable_context_metrics`

## 7.2 Phase 2: CLI 展示切换

目标：

- CLI source bar 默认展示 request-level breakdown
- 明细视图可展示完整 request buckets

涉及文件：

- `cli.py`

实施步骤：

1. 新增 `_get_current_request_metrics()`
2. 将状态栏优先使用 `request_metrics`
3. 保留 fallback：
   - 若 `request_metrics` 不存在，则回退 `context_metrics`
4. 扩展颜色与 label 映射
5. 更新详细 breakdown 的渲染逻辑，使其支持 `by_bucket`

## 7.3 Phase 3: 兼容与调试增强

可选增强：

- 保留一个“Context Sources”调试视图
- CLI 配置允许切换：
  - `display.context_breakdown_mode: request | context`
- 将 request breakdown 写入日志或 debug 输出

---

## 8. 关键实现细节

## 8.1 统计时机

必须在以下步骤之后统计：

- dynamic context assembled
- `effective_system` finalized
- system message prepend 完成
- prefill messages inserted
- tools resolved

这样 request metrics 才与真实请求尽可能一致。

## 8.2 不应直接拿最终 `api_messages` 总量减法反推

不推荐：

- 先算 `estimate_request_tokens_rough(api_messages, tools=...)`
- 再减 context 或 messages 的值

原因：

- 粗估中 `str(dict)` 的边界和拼接会引入噪音
- 减法法容易出现误差积累和重复扣减
- 可读性差

推荐直接按桶正向求和。

## 8.3 provider 差异

当前 rough token 估算不是 provider-accurate tokenizer 统计，因此：

- 新设计只保证“桶间相对占比”合理
- 不保证与真实计费完全相等

但这与 Hermes 当前统计口径一致，可接受。

## 8.4 prompt caching 兼容性

该改造不改写消息，不插入新消息，不调整 context source 顺序，因此：

- 不应破坏 prompt caching

这属于本方案的硬约束。

---

## 9. CLI 展示设计

## 9.1 状态栏简版

推荐显示 top-4：

- `msg:tool`
- `project`
- `tools`
- `memory`

具体取决于占比排序。

示例：

```text
msg:tool:42% tools:27% project:18% msg:user:9% other:4%
```

## 9.2 详细 breakdown

推荐格式：

```text
Request Breakdown
  project: 2,300 tok (9,200 char)
  msg:user: 1,200 tok (4,800 char)
  msg:asst: 900 tok (3,600 char)
  msg:tool: 4,800 tok (19,200 char)
  tools: 3,100 tok (12,400 char)
```

可选附加：

```text
Context Sources
  identity: ...
  memory: ...
  project: ...
```

---

## 10. 兼容性与迁移策略

## 10.1 向后兼容

保留现有字段：

- `_stable_context_metrics`
- `_last_context_metrics`

新增字段：

- `_last_request_metrics`

这样现有依赖 `ContextMetrics` 的测试和代码可以渐进迁移。

## 10.2 迁移顺序

1. 先写 request metrics 生成逻辑
2. 再切 CLI 读取源
3. 最后补调试模式和配置项

这样能降低一次性改动风险。

---

## 11. 风险分析

## 11.1 语义混乱风险

风险：
开发者仍把 `ContextMetrics` 和 `RequestMetrics` 混用。

缓解：

- 类型和命名明确区分
- CLI getter 分开命名
- 注释明确“context-only” vs “full request”

## 11.2 重复计数风险

风险：

- context source 既从 chunk 记一次
- 又从 system string 记一次

缓解：

- request breakdown 中不引入笼统 `system` bucket
- system 内容仅来自 `context_*`

## 11.3 UI 可读性风险

风险：
bucket 过多，状态栏过载。

缓解：

- 状态栏只显示 top-N
- 详细 breakdown 显示全量

## 11.4 测试脆弱性风险

风险：
使用精确 token 数字容易因字符串序列化差异导致测试脆弱。

缓解：

- 断言 bucket 存在、分类正确、总和一致
- 只在必要处断言精确小样本数字

---

## 12. 完整测试覆盖设计

测试目标：

- 保证 request breakdown 覆盖完整
- 保证无重复计数
- 保证 CLI 展示正确
- 保证旧 context metrics 仍可用
- 保证缓存与组装行为不变

---

## 13. 单元测试矩阵

### 13.1 数据模型测试

建议文件：

- `tests/context_engine/test_request_metrics.py`

覆盖点：

1. `RequestBucketMetrics` 基本构造
2. `RequestMetrics` 汇总字段可用
3. `get_bucket()` 正确返回 bucket
4. 空 bucket 列表时 total 为 0
5. bucket 名重复时聚合逻辑正确

### 13.2 请求记账器测试

建议文件：

- `tests/context_engine/test_request_metrics_builder.py`

覆盖点：

1. stable context chunk 正确映射到 `context_*`
2. dynamic context chunk 正确映射到 `context_*`
3. `role=user` 归入 `messages_user`
4. `role=assistant` 归入 `messages_assistant`
5. assistant 中带 `tool_calls` 仍归入 `messages_assistant`
6. `role=tool` 归入 `messages_tool`
7. 未知 role 归入 `messages_other`
8. prefill 单独归入 `prefill_messages`
9. tool schemas 单独归入 `tool_schemas`
10. 空 messages 时只统计 context 和 tools
11. 空 tools 时 `tool_schemas` 为 0 或不存在
12. 总 token 数等于各 bucket 求和
13. 总 char 数等于各 bucket 求和
14. 不对 context 内容重复计数

### 13.3 label/color 映射测试

建议文件：

- `tests/context_engine/test_cli_request_source_bar.py`

覆盖点：

1. 所有新 bucket 都有 label
2. 所有新 bucket 都有 color
3. 未知 bucket fallback 到原名
4. top-N 排序正确
5. 第 4 名以后正确聚合到 `other`

---

## 14. 集成测试矩阵

### 14.1 run_agent 集成

建议文件：

- `tests/context_engine/test_run_agent_request_metrics.py`

覆盖点：

1. 仅 stable context 时生成 `_last_request_metrics`
2. dynamic context 存在时合并进入 request metrics
3. prefill messages 存在时被单独统计
4. tools 存在时 `tool_schemas` 被统计
5. 多轮对话后 user/assistant/tool 消息分类正确
6. tool 调用回合中 assistant tool_calls 进入 `messages_assistant`
7. tool result 消息进入 `messages_tool`
8. `_last_context_metrics` 仍保留原语义
9. `_last_request_metrics.total_estimated_tokens` 大于等于 `_last_context_metrics.total_estimated_tokens`
10. request metrics 不影响最终 `api_messages` 内容

### 14.2 CLI 渲染集成

建议文件：

- `tests/context_engine/test_cli_request_breakdown_rendering.py`

覆盖点：

1. `_get_current_request_metrics()` 读取 agent 缓存
2. 状态栏优先 request metrics
3. request metrics 不存在时 fallback 到 context metrics
4. 明细视图使用 request buckets
5. breakdown 为空时返回空字符串
6. 聚合后显示 token 和 char
7. CLI 不因未知 bucket 崩溃

---

## 15. 回归测试矩阵

### 15.1 旧 context engine 行为不变

建议复用/补充：

- `tests/context_engine/test_assembler.py`
- `tests/context_engine/test_sources_*.py`
- `tests/context_engine/test_cli_source_bar.py`

覆盖点：

1. stable/dynamic chunk 组装顺序不变
2. `ContextMetrics` 生成逻辑不变
3. `project_context` 等旧 source 标签仍可渲染
4. 旧 breakdown 在 fallback 路径仍工作

### 15.2 prompt caching 不破坏

建议文件：

- `tests/context_engine/test_request_metrics_prompt_cache_safety.py`

覆盖点：

1. 开启 request metrics 后，不增加 system prompt 内容
2. 开启 request metrics 后，不增加消息数量
3. 开启 request metrics 后，不改变 prefill 插入顺序
4. 开启 request metrics 后，不改变 tool schema 列表

### 15.3 compression / overflow 路径

建议文件：

- `tests/test_context_pressure.py`
- `tests/test_1630_context_overflow_loop.py`
- 新增 `tests/context_engine/test_request_metrics_under_compression.py`

覆盖点：

1. history 被压缩后 request metrics 反映压缩后的 message 集
2. payload-too-large 重试前后 request metrics 更新正确
3. 重启重试后 `_last_request_metrics` 不残留旧值

---

## 16. 手工验证清单

实施后建议手工跑以下场景：

1. 单轮无工具对话
   预期：`messages_user` 和 `messages_assistant` 很小，context buckets 正常

2. 大型 `AGENTS.md` 项目
   预期：`context_project` 明显升高

3. 大工具集启用
   预期：`tool_schemas` 明显升高

4. 多轮 tool-heavy 对话
   预期：`messages_tool` 成为主要占比

5. 启用 prefill messages
   预期：出现 `prefill_messages`

6. SparkGraph / plugin / Honcho 打开
   预期：动态 context buckets 正常出现

---

## 17. 建议实施顺序

### Step 1

新增 request metrics 数据模型与 builder

### Step 2

在 `run_agent.py` 中构造 `_last_request_metrics`

### Step 3

为 CLI 增加 request metrics 渲染和 fallback

### Step 4

补全单元测试

### Step 5

补全 run_agent/CLI 集成测试

### Step 6

补充 compression / retry / caching 回归测试

---

## 18. 最终验收标准

以下条件全部满足，视为改造完成：

1. CLI 默认 breakdown 能覆盖完整请求输入，而不只是 context chunks
2. `messages_user` / `messages_assistant` / `messages_tool` 可分辨
3. `tool_schemas` 可单独显示
4. `prefill_messages` 可单独显示
5. `ContextMetrics` 原有能力仍保留
6. 不修改实际请求 payload
7. 不破坏 prompt caching
8. 所有新增与回归测试通过

---

## 19. 附：推荐新增测试文件清单

- `tests/context_engine/test_request_metrics.py`
- `tests/context_engine/test_request_metrics_builder.py`
- `tests/context_engine/test_run_agent_request_metrics.py`
- `tests/context_engine/test_cli_request_source_bar.py`
- `tests/context_engine/test_cli_request_breakdown_rendering.py`
- `tests/context_engine/test_request_metrics_prompt_cache_safety.py`
- `tests/context_engine/test_request_metrics_under_compression.py`

---

## 20. 附：实施时应修改的主要文件

- `agent/context_engine/models.py` 或新增 `agent/context_engine/request_metrics.py`
- `run_agent.py`
- `cli.py`
- `tests/context_engine/test_cli_source_bar.py`
- 新增若干 request metrics 测试文件

---

## 21. 一句话决策

这次改造的本质不是“给 ContextEngine 再加几个 source”，而是“补上一层 request-level 输入构成统计”，让 Hermes 第一次真正能解释：模型这一轮到底吃进去了什么，以及是谁把上下文撑爆了。

---

## 22. 对业务的影响评估

本改造是“可观测性增强”项目，不直接改变模型输出质量，但会显著改善以下业务能力。

### 22.1 正向业务价值

1. 降低上下文爆炸问题的定位成本
   当前只能看到总 token，无法快速判断是项目上下文、历史消息还是工具 schema 导致超限。引入 request breakdown 后，用户与开发者能直接定位主要成本桶。

2. 提升压缩策略和产品决策质量
   当 `messages_tool` 长期成为最大桶时，团队可以更有依据地优化 tool result 截断、摘要、持久化与重放策略。

3. 提升高工具密度场景的可解释性
   Hermes 的核心价值之一是工具编排。若工具调用很多，用户自然会追问“上下文为什么这么大”。该能力能把复杂代理行为可视化。

4. 为后续成本控制功能提供基础设施
   request-level metrics 可直接支持未来能力：
   - 自动提示“当前主要成本来自 tool schemas”
   - 建议关闭某些 toolset
   - 建议执行 `/compress`
   - 建议切换到短上下文模式

5. 改善企业/重度用户对系统可信度的感知
   在复杂对话、多轮工具调用、长项目上下文下，用户更关心系统“为何如此表现”。透明的输入构成会提升产品可信度。

### 22.2 潜在负面影响

1. UI 更复杂
   如果 bucket 过多，普通用户可能感到噪音变大。

2. 指标可能被误读为真实计费
   当前仍是 rough token 估算，不应被宣传为 provider 精确账单。

3. 开发复杂度上升
   会引入第二套 metrics 抽象，后续若命名不清晰，维护成本会上升。

### 22.3 业务影响结论

总体上这是一个低风险、高解释价值、高运营价值的改造。

建议产品定位为：

- “请求输入构成分析”
- “上下文构成分析”

而不是：

- “精确 token 计费”

---

## 23. 边界条件全量分析

本节定义 request metrics 必须覆盖或明确降级处理的边界。

### 23.1 API 模式边界

Hermes 当前存在多种 API 模式：

- `chat_completions`
- `codex_responses`
- `anthropic_messages`

设计要求：

1. request metrics 应独立于 provider 计费实现
2. request metrics 应基于“发出前的统一消息视图”生成
3. 若某模式会在最后一跳做 payload 适配，指标应尽量基于适配前的规范消息结构，而不是 provider 私有传输格式

原因：

- 不同 provider 对同一消息可能有不同 JSON 包装
- 如果直接按 provider 传输格式统计，跨 provider 对比会失真

### 23.2 Prompt caching 边界

Anthropic prompt caching 会改写消息结构，给 system 与最近消息加 `cache_control`，甚至把字符串 `content` 包装成结构化列表。

设计要求：

1. request metrics 默认统计“语义输入内容”，不把 `cache_control` 元数据算作核心内容桶
2. 如需观测实际传输膨胀，可新增 debug-only 桶：
   - `transport_overhead`
3. 第一阶段不统计 cache metadata 开销

理由：

- 用户更关心“哪类内容撑爆了上下文”
- 不关心 Anthropic 为缓存插入的协议性包装

### 23.3 多模态消息边界

消息内容可能不是纯字符串，可能是：

- list content
- text/image mixed blocks
- dict content
- provider-specific structured content

设计要求：

1. builder 必须支持非字符串 `content`
2. 统计时统一使用 `len(str(msg)) // 4` 或等价的稳定序列化口径
3. 测试必须覆盖图片/结构化 content message

### 23.4 Prefill 边界

`prefill_messages` 最终被插入请求，但不属于真实会话历史。

设计要求：

1. 永远单独记账
2. 不混入 `messages_user/messages_assistant`
3. request summary 中可单独显隐

### 23.5 Tool schema 边界

不同场景下 `tools` 可能为：

- `None`
- 空列表
- 大量 schema
- 动态裁剪后的 schema

设计要求：

1. `tool_schemas` 桶支持空值
2. 必须与当前 turn 实际启用的 tools 对齐
3. 不应误统计被过滤掉的 schema

### 23.6 Context compression 边界

压缩会替换 `messages` 集，并可能切换为压缩后的 system/history 结构。

设计要求：

1. request metrics 必须反映“压缩后最终送出的消息”
2. 压缩前 metrics 不得残留到下一次重试
3. 压缩引发的新 session/history 结构变化必须被重新统计

### 23.7 Retry / fallback 边界

重试、fallback provider、认证恢复可能导致同一轮内多次重建请求。

设计要求：

1. `_last_request_metrics` 始终代表“最后一次实际尝试的请求结构”
2. 若 provider fallback 改变 api_mode，不应导致 bucket 丢失
3. 如重试前后请求内容变化，应更新指标而不是复用旧值

### 23.8 Interrupt 边界

用户中断可能发生在：

- tool loop 中
- API 请求等待中
- retry sleep 中

设计要求：

1. 若本轮尚未形成最终请求，不应写入误导性 request metrics
2. 若中断前已发出请求，则允许保留最后一次已发送请求的 metrics
3. 中断返回结果中不应带着下一轮请求的脏指标

### 23.9 Partial / malformed response 边界

模型可能返回：

- 空响应
- 非法 tool_calls
- 非法 JSON
- incomplete scratchpad

设计要求：

1. request metrics 仍应可用，因为它描述的是输入，不依赖输出是否成功
2. 发生恢复重试后，应以最后一次尝试为准

### 23.10 Parent / child agent 边界

Hermes 支持子代理委托。

设计要求：

1. 每个 agent 实例独立维护自己的 `_last_request_metrics`
2. 不共享父子 request metrics 状态
3. 不让子代理覆盖父代理的 CLI 观测结果

---

## 24. 异常情况覆盖与处理策略

本节定义实现时应显式处理的异常分支。

### 24.1 builder 自身异常

风险：

- message 结构异常
- bucket 名未知
- content 不可序列化

策略：

1. builder 必须 fail-soft
2. 单个 message 无法分类时，降级进 `messages_other`
3. 整体 builder 出错时：
   - 记录 warning
   - 保留 `_last_context_metrics`
   - 不影响主请求发送

### 24.2 序列化异常

风险：
某些对象的 `str(...)` 行为不稳定或异常。

策略：

1. 统一在 helper 中包装 `try/except`
2. 出错时退化为：
   - `repr(type(obj))`
   - 或固定占位文本长度

### 24.3 未知 role

策略：

- 全部归入 `messages_other`
- 不抛异常

### 24.4 缺失字段

如 message 缺失：

- `role`
- `content`

策略：

1. role 缺失 -> `messages_other`
2. content 缺失但存在其他字段 -> 仍统计整个 message 序列化长度

### 24.5 动态 context source 异常

当前 dynamic source 可能独立失败并写 warning。

策略：

1. request metrics 不应因为某个 source 失败而整体失效
2. 失败的 source 直接不出现在 `context_*` bucket 中
3. 必要时可在 metadata 标记：
   - `source_error=True`

### 24.6 CLI 渲染异常

策略：

1. request metrics 渲染失败时 fallback 到 context metrics
2. context metrics 也失败时返回空串
3. 不允许状态栏渲染异常影响主 CLI 交互

---

## 25. 软件设计优化建议

在原计划基础上，建议进一步优化设计，避免未来抽象债务。

### 25.1 拆分为三层模型

推荐明确分三层：

1. `ContextMetrics`
   仅描述 ContextEngine chunk

2. `RequestMetrics`
   描述最终请求输入构成

3. `DisplayMetrics`
   用于 UI 聚合显示，例如：
   - top-N
   - `other`
   - label/color 映射

不要让 CLI 直接承担聚合和语义翻译职责过多。

### 25.2 引入专用 builder 模块

建议新增：

- `agent/context_engine/request_metrics_builder.py`

职责：

- 规范化 bucket 名
- 统计 request buckets
- 聚合 totals
- 构建 `RequestMetrics`

不要把这些逻辑散落在 `run_agent.py`。

### 25.3 引入“规范化消息视图”

建议增加内部帮助函数，例如：

```python
def normalize_request_messages(
    messages: list[dict],
    *,
    prefill_messages: list[dict] | None = None,
) -> NormalizedRequestMessages:
    ...
```

作用：

- 明确区分真实历史消息与 prefill
- 避免 builder 直接依赖 `run_agent.py` 某段插入逻辑
- 让测试更容易写

### 25.4 不要把 request metrics 绑定到 ContextEngine 包名过深

虽然本项目从“context breakdown”切入，但从语义上看：

- `RequestMetrics` 更接近 agent 请求层

可接受两种放置方式：

1. `agent/context_engine/request_metrics.py`
2. `agent/request_metrics.py`

如果预计未来还会统计：

- output/completion breakdown
- cache hit breakdown
- transport overhead

建议优先第二种，避免 context_engine 包职责膨胀。

### 25.5 将 metrics 视为快照，而不是全局状态源

建议在 `run_conversation()` 返回结果时附带：

- `context_metrics`
- `request_metrics`

而不是只写到实例字段：

- `_last_context_metrics`
- `_last_request_metrics`

原因：

- 更利于测试
- 更利于 future telemetry/logging
- 减少状态滞留问题

实例字段可以保留给 CLI 读取，但结果对象也应带快照。

### 25.6 引入 metrics version

建议在 `RequestMetrics` 上加入：

```python
version: int = 1
```

便于后续升级 bucket 定义时保持兼容。

### 25.7 为 bucket 增加 category

建议 `RequestBucketMetrics` 附加：

- `category`: `context` | `messages` | `prefill` | `tools` | `other`

这样 UI 层和导出层可以更轻松做二次聚合。

### 25.8 增加观测一致性断言

builder 完成后可做轻量自检：

1. `total_estimated_tokens == sum(by_bucket)`
2. `total_char_count == sum(by_bucket)`
3. bucket 名集合无重复

若失败：

- 记录 warning
- 不阻塞主流程

---

## 26. 建议新增的高优先级测试

除原测试矩阵外，建议新增以下高优先级边界测试。

### 26.1 API 模式一致性

建议文件：

- `tests/context_engine/test_request_metrics_api_modes.py`

覆盖点：

1. `chat_completions` 模式下 bucket 完整
2. `codex_responses` 模式下 bucket 完整
3. `anthropic_messages` 模式下 bucket 完整
4. fallback 切换后 bucket 语义不丢失

### 26.2 Prompt caching 口径稳定性

建议文件：

- `tests/context_engine/test_request_metrics_cache_control_semantics.py`

覆盖点：

1. 应用 cache_control 前后，核心 bucket 统计一致
2. request metrics 不把 cache metadata 误当成主要内容桶

### 26.3 多模态与结构化消息

建议文件：

- `tests/context_engine/test_request_metrics_multimodal.py`

覆盖点：

1. list content message 可统计
2. image/text mixed content 可统计
3. dict content 可统计
4. 缺失 content 但有其他字段的 message 可统计

### 26.4 异常鲁棒性

建议文件：

- `tests/context_engine/test_request_metrics_fail_soft.py`

覆盖点：

1. builder 单条 message 解析失败时不崩溃
2. builder 整体异常时主请求流程不受影响
3. CLI 渲染 request metrics 失败时 fallback 正常

---

## 27. 优化后的最终建议

如果从软件设计质量出发，推荐最终落地版本是：

1. 保留现有 `ContextMetrics`
2. 新增 `RequestMetrics` 与独立 builder 模块
3. 让 `run_conversation()` 返回 metrics 快照
4. CLI 默认展示 request breakdown，必要时可切换回 context breakdown
5. 将异常策略设计成 fail-soft，不影响主代理工作流

这会比“简单把 messages 塞进现有 source breakdown”更稳、更清晰，也更适合作为后续成本分析与上下文治理能力的基础设施。
