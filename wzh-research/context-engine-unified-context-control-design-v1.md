# ContextEngine 统一上下文控制重构设计 v1

> 仓库: `IsacHermes`
> 创建: 2026-04-09
> 状态: 设计完成，待重构
> 目标: 让 ContextEngine 取得对模型输入上下文的绝对控制权，将 `messages`、`prefill_messages`、`tool_schemas`、context sources 全部纳入同一管理模型；重构阶段不得改变任何业务行为

---

## 1. 执行摘要

当前 Hermes 已经具备：

- `ContextEngine` 对 stable/dynamic system context 的统一组装
- `request_metrics` 对 messages / prefill / tool schemas 的统一统计

但还不具备：

- 对“完整模型输入”的统一生命周期管理
- 对 messages / prefill / tool schemas 的一等公民抽象
- 对最终请求构成的单一权威装配入口

这导致今天的系统仍是“两层体系”：

1. `ContextEngine`
   负责 context sources

2. `run_agent.py`
   负责 messages、prefill、tools、transport adaptation、修复逻辑

这意味着：

- 可以统一观察，但不能统一控制
- 可以统计 messages，但 messages 不是 ContextEngine source graph 的成员
- CLI 能展示完整 breakdown，但 ContextEngine 并不真正拥有这些内容的装配权

本设计的核心目标是：

把“模型输入”整体提升为 ContextEngine 的一等公民对象，使 ContextEngine 不只管理 system chunks，而是管理整个 request assembly graph。

---

## 2. 现状问题复盘

## 2.1 当前结构

今天的输入装配大致是：

1. `ContextAssembler.assemble_stable()` 生成 stable system
2. `ContextAssembler.assemble_dynamic()` 生成 dynamic system
3. `run_agent.py` 自己构造 `api_messages`
4. `run_agent.py` 自己插入 `prefill_messages`
5. `run_agent.py` 自己附带 `self.tools`
6. `run_agent.py` 再额外调用 `build_request_metrics(...)`

结果是：

- `ContextEngine` 拥有 system prompt 的控制权
- `run_agent.py` 拥有 request payload 的控制权

这与“Engine 绝对上下文控制权”的目标不一致。

## 2.2 为什么这不是可发布终态

当前方案存在以下架构缺陷：

1. `messages` 不是 ContextEngine graph 中的节点
2. `tool_schemas` 不是 ContextEngine graph 中的节点
3. `prefill_messages` 不是 ContextEngine graph 中的节点
4. request metrics 是事后统计，不是装配产物
5. `run_agent.py` 仍然决定哪些内容进入请求以及以何种顺序进入
6. CLI 看到的是“拼接后的统计结果”，不是“Engine 权威模型”

换句话说：

今天的 `ContextEngine` 仍然是“system prompt engine”，不是“full request engine”。

---

## 3. 重构目标

## 3.1 核心目标

让 ContextEngine 成为“完整模型输入”的唯一权威装配器。

其控制范围必须包括：

- stable context
- dynamic context
- normalized conversation messages
- prefill messages
- tool schemas
- 最终 request-level metrics

## 3.2 约束

本次是架构重构，不是行为重写。

必须满足：

1. 零业务行为变化
   最终发送给模型的内容、顺序、重试行为、缓存行为、恢复行为都不能因为重构而改变。

2. 零缓存语义变化
   Anthropic/OpenRouter prompt caching 的前缀稳定性不能被破坏。

3. 零工具调用语义变化
   tool call validation、tool result repair、compression、continuation、interrupt 等逻辑行为不变。

4. 可渐进迁移
   重构可分阶段落地，每阶段都能测试并回滚。

## 3.3 非目标

本次不做：

- provider 精确 tokenizer 统计
- 重新定义 context compression 策略
- 重写 tool execution loop
- 重写 prompt caching 策略
- 改变 API mode 适配层行为

---

## 4. 核心设计原则

1. “请求输入”必须有单一权威模型
2. 统计必须从装配模型自然导出，而不是事后打补丁
3. ContextEngine 应只暴露规范化结果，不泄漏内部拼接细节到 CLI
4. `run_agent.py` 只负责业务流程，不负责上下文拼装语义
5. transport adaptation 与 semantic assembly 分离

---

## 5. 新架构总览

推荐把 ContextEngine 扩展为三层结构：

1. `InputNode`
   最小输入单元，统一描述所有进入模型的内容

2. `InputAssembly`
   一次完整模型请求的语义装配结果

3. `TransportPayload`
   不同 API mode/provider 的最终传输格式

关系如下：

```text
Context Sources / Message Sources / Prefill / Tool Schemas
                ↓
          InputNode Graph
                ↓
          InputAssembly
                ↓
     RequestMetrics / ContextMetrics
                ↓
         TransportPayload Adapter
                ↓
            Provider API Call
```

这会让：

- ContextEngine 统一拥有所有输入节点
- metrics 由 InputAssembly 自然生成
- provider 适配仅负责“怎么发”，不负责“发什么”

---

## 5.1 文件与函数级改造清单

这一节明确到具体文件/函数，避免设计停留在概念层。

### 新增文件

1. `agent/context_engine/input_models.py`
   定义：
   - `InputNode`
   - `InputAssembly`
   - 可选：`AssemblySnapshots`

2. `agent/context_engine/input_sources.py`
   定义：
   - `ConversationMessagesSource`
   - `PrefillMessagesSource`
   - `ToolSchemasSource`

3. `agent/context_engine/transport.py`
   定义：
   - `TransportAdapter`
   - `ChatCompletionsTransportAdapter`
   - `CodexResponsesTransportAdapter`
   - `AnthropicMessagesTransportAdapter`

4. `agent/context_engine/metrics_v2.py`
   定义：
   - `build_context_metrics_from_nodes(...)`
   - `build_request_metrics_from_assembly(...)`
   - `compare_legacy_and_engine_payloads(...)`

### 修改文件

1. `agent/context_engine/models.py`
   保留现有：
   - `ContextChunk`
   - `ContextMetrics`
   - `AssemblyResult`

   仅做最小兼容扩展：
   - 增加与 `InputAssembly` 的互转辅助函数
   - 不在此文件塞入过多 request 层职责

2. `agent/context_engine/context.py`
   扩展 `AssemblyContext` 字段：
   - `prefill_messages`
   - `tool_schemas`
   - `normalized_messages`
   - `api_mode`

3. `agent/context_engine/sources.py`
   现有 context sources 不改变业务语义；
   新增或适配为可输出 `InputNode(kind="context")`

4. `agent/context_engine/assembler.py`
   必须新增：
   - `assemble(...) -> InputAssembly`
   - `_collect_input_nodes(...)`
   - `_build_effective_system(...)`
   - `_build_normalized_messages(...)`

   兼容保留：
   - `assemble_stable()`
   - `assemble_dynamic()`
   但内部转调新接口

5. `agent/context_engine/__init__.py`
   导出：
   - `InputNode`
   - `InputAssembly`
   - `TransportAdapter`

6. `run_agent.py`
   需要收口的函数：
   - `_build_system_prompt()`
   - `_get_context_assembler()`
   - `_update_request_metrics()`（未来删除或退化为兼容壳）
   - `_build_api_kwargs()`
   - `_prepare_anthropic_messages_for_api()`
   - `_sanitize_api_messages()`

   新增函数建议：
   - `_assemble_input_graph(...)`
   - `_build_transport_payload(...)`
   - `_compare_engine_and_legacy_payload(...)`

7. `cli.py`
   新增或调整：
   - `_get_preferred_breakdown_metrics()`
   - `_render_context_breakdown()`
   - `_render_source_bar()`
   - `_build_context_bar()`

   最终这些函数都应消费 Engine 权威结果，而不是自行猜测 bucket 语义

### 迁移阶段删除或降级的函数

1. `agent/context_engine/request_metrics.py`
   当前作为旁路统计 helper 存在
   最终应：
   - 删除
   或
   - 只保留薄兼容壳，内部调用 `metrics_v2.py`

2. `run_agent.py::_update_request_metrics`
   当前是临时桥接函数
   最终应删除

---

## 5.2 函数职责重分配

为避免“看起来重构了，但实际上只是换个地方打补丁”，必须明确职责转移。

### 当前由 `run_agent.py` 负责，未来必须迁出

1. request semantic assembly
2. prefill insertion order
3. tool schema 作为输入桶的收集
4. request-level metrics 的一阶构建

### 仍保留在 `run_agent.py`

1. conversation loop
2. retries / fallback
3. interrupt
4. tool execution
5. compression 触发决策

### 仍保留在 transport adapter

1. provider-specific payload shaping
2. anthropic multimodal flatten
3. cache_control injection
4. codex-specific message/instruction formatting

---

## 6. 数据模型重构

## 6.1 统一节点模型：`InputNode`

建议新增：

- `agent/context_engine/input_models.py`

### 结构

```python
@dataclass
class InputNode:
    kind: Literal[
        "context",
        "message",
        "prefill",
        "tool_schema",
    ]
    name: str
    stage: Literal["stable", "dynamic", "request"]
    content: Any
    char_count: int
    rough_tokens: int
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 设计意图

- `kind` 区分语义类型
- `name` 统一承担 today 的 source/bucket label 角色
- `stage` 允许 system-stable / per-turn-dynamic / request-time-only 共存
- `content` 保留原始语义对象
- `char_count` / `rough_tokens` 直接绑定到节点，避免重复计算

### 关键意义

以后：

- `context_project`
- `messages_tool`
- `prefill_messages`
- `tool_schemas`

都不再是“额外统计桶”，而是正式 InputNode。

## 6.2 `InputAssembly`

```python
@dataclass
class InputAssembly:
    stable_nodes: list[InputNode]
    dynamic_nodes: list[InputNode]
    request_nodes: list[InputNode]
    effective_system: str
    normalized_messages: list[dict[str, Any]]
    prefill_messages: list[dict[str, Any]]
    tool_schemas: list[dict[str, Any]]
```

### 职责

它表示“语义上的完整模型输入”，是 ContextEngine 的最高层输出。

注意：

- `effective_system`
- `normalized_messages`
- `prefill_messages`
- `tool_schemas`

都应该从同一个 assembly 得到，而不是由 `run_agent.py` 各自拼出来。

## 6.3 Metrics 模型

保留两类指标，但都从 `InputAssembly` 导出：

1. `ContextMetrics`
   仅由 `kind="context"` 节点导出，兼容旧视图

2. `RequestMetrics`
   由全部节点导出

这样以后就不再需要在 `run_agent.py` 里独立调用 `build_request_metrics(...)` 做旁路统计。

---

## 7. Source 体系重构

## 7.1 从 `ContextSource` 扩展为 `InputSource`

当前 `ContextSource.collect()` 只返回 `ContextChunk`。

建议重构为：

```python
class InputSource(Protocol):
    name: str
    stage: Literal["stable", "dynamic", "request"]
    def collect(self, ctx: AssemblyContext) -> list[InputNode]: ...
```

并把现有 `ContextSource` 视为 `InputSource(kind="context")` 的特例。

## 7.2 新增 request-level sources

新增三类 source：

1. `ConversationMessagesSource`
2. `PrefillMessagesSource`
3. `ToolSchemasSource`

### `ConversationMessagesSource`

职责：

- 接受当前语义消息链
- 产生：
  - `messages_user`
  - `messages_assistant`
  - `messages_tool`
  - `messages_other`

注意：

- 这里是“规范化消息节点”
- 不是 provider transport payload

### `PrefillMessagesSource`

职责：

- 接受 prefill messages
- 产生 `prefill_messages`

### `ToolSchemasSource`

职责：

- 接受当前 tools
- 产生 `tool_schemas`

## 7.3 Source 分层

建议三个阶段：

### stable

- identity
- tool_guidance
- tool_use_enforcement
- honcho_static
- system_message
- memory
- user_profile
- skills
- project_context
- time_platform

### dynamic

- ephemeral
- plugin
- sparkgraph_recall
- honcho_turn

### request

- conversation_messages
- prefill_messages
- tool_schemas

这样 `messages` 不再漂浮在 Engine 外面，而进入正式 source graph。

---

## 8. Assembler 重构

## 8.1 新的 `ContextAssembler` 角色

重构后，`ContextAssembler` 不应只返回 stable/dynamic strings。

它应返回完整 `InputAssembly`。

### 新接口建议

```python
class ContextAssembler:
    def assemble(
        self,
        *,
        system_message: str | None = None,
        user_message: str | None = None,
        conversation_history: list | None = None,
        prefill_messages: list | None = None,
        tool_schemas: list | None = None,
    ) -> InputAssembly: ...
```

### 向后兼容

短期保留：

- `assemble_stable()`
- `assemble_dynamic()`

但内部都转调 `assemble()` 后取子结果。

## 8.2 `run_agent.py` 的新职责

重构后 `run_agent.py` 应：

1. 维护业务语义消息链
2. 调用 assembler 生成 `InputAssembly`
3. 调用 transport adapter 把 assembly 变成 provider payload
4. 调用 provider API

不再负责：

- 自己拼 `api_messages`
- 自己插 prefill
- 自己旁路统计 request metrics

---

## 9. 传输适配层分离

## 9.1 为什么必须单独分层

如果把 provider-specific 包装也塞进 ContextEngine，Engine 很快会退化成“耦合 provider 的大泥球”。

因此必须分离：

- semantic assembly
- transport adaptation

## 9.2 新增 `TransportAdapter`

建议新增：

- `agent/context_engine/transport.py`

接口：

```python
class TransportAdapter:
    def to_api_messages(self, assembly: InputAssembly, agent) -> list[dict[str, Any]]: ...
```

不同 API mode 的适配只发生在这里：

- system prepend
- prefill insert
- anthropic multimodal flatten
- cache_control injection
- codex-specific field shaping

### 关键点

这样才能让 ContextEngine 拥有语义装配权，而 transport 层拥有协议适配权。

---

## 10. 保持零业务行为变化的方法

这是本设计最重要的部分。

## 10.1 行为冻结原则

重构阶段必须满足：

- 相同输入 → 相同 `api_messages`
- 相同输入 → 相同 tools
- 相同输入 → 相同重试行为
- 相同输入 → 相同缓存断点

## 10.2 双轨运行策略

重构时建议引入双轨对照：

1. Legacy path
   当前 `run_agent.py` 手工拼装路径

2. Engine path
   新 `InputAssembly` + `TransportAdapter`

在迁移阶段：

- 两条路径同时构建
- 对比输出
- 若不一致则记录差异并 fallback legacy

只在一致性验证通过后正式切换。

## 10.3 等价性验证点

必须逐 turn 比较：

1. `effective_system`
2. `normalized_messages`
3. `prefill insertion order`
4. `tool schema payload`
5. `api_messages` 最终顺序
6. provider-specific kwargs

## 10.4 必须冻结的具体业务行为

下面这些行为必须逐项冻结，不能只说“整体不变”：

1. system prompt 的文本内容与顺序
2. dynamic context 注入时机与顺序
3. honcho turn context 的注入位置
4. prefill messages 的插入位置
5. tool schema 列表顺序与内容
6. `_sanitize_api_messages()` 的修复结果
7. compression 后 messages 的可见结果
8. continuation/retry 时附加消息的内容
9. codex `instructions` / `messages` 拆分方式
10. anthropic message flatten 后的文本结果
11. prompt caching breakpoints 的位置
12. session persistence 前后的 message 链一致性

## 10.5 建议引入的迁移开关

为了商业发布质量，建议显式加入迁移开关，而不是直接替换主路径。

建议配置：

```yaml
agent:
  context_engine:
    unified_input_engine: false
    unified_input_shadow_compare: true
    unified_input_compare_fail_fast: false
```

语义：

1. `unified_input_engine`
   真正切换到新主路径

2. `unified_input_shadow_compare`
   仍用 legacy 发请求，但同时构建 new assembly 做等价性校验

3. `unified_input_compare_fail_fast`
   开发阶段出现 diff 直接抛错；生产环境默认只打 warning + fallback

---

## 11. 迁移路线

## 11.1 Phase A: 引入统一节点模型

新增：

- `InputNode`
- `InputAssembly`

但先不切主流程。

## 11.2 Phase B: 把现有 context sources 升级为 `InputSource`

让现有 stable/dynamic source 改返回 `InputNode(kind="context")`

## 11.3 Phase C: 新增 request sources

加入：

- `ConversationMessagesSource`
- `PrefillMessagesSource`
- `ToolSchemasSource`

## 11.4 Phase D: 引入 `assemble()` 全量接口

同时输出：

- stable nodes
- dynamic nodes
- request nodes
- metrics
- semantic payload

## 11.5 Phase E: 双轨对照

在 `run_agent.py` 中：

- legacy path 继续发请求
- new path 只做 shadow compare

## 11.6 Phase F: 切主路径

验证充分后：

- `run_agent.py` 改为完全依赖 `InputAssembly`

## 11.7 Phase G: 删除临时旁路逻辑

移除：

- `request_metrics.py` 独立旁路 builder 风格的职责
- 零散 message bucketing 逻辑

---

## 12. 测试设计总览

测试必须覆盖四层：

1. 数据模型测试
2. source/assembler 测试
3. 等价性测试
4. 业务回归测试

---

## 13. 数据模型测试

建议新增：

- `tests/context_engine/test_input_models.py`

覆盖点：

1. `InputNode` 基本构造
2. `kind`/`stage` 校验
3. char/token 记录正确
4. `InputAssembly` 可持有三类节点
5. 空 assembly 合法

---

## 14. 新 Source 测试

建议新增：

- `tests/context_engine/test_request_sources.py`

覆盖点：

### `ConversationMessagesSource`

1. user 消息 -> `messages_user`
2. assistant 消息 -> `messages_assistant`
3. tool 消息 -> `messages_tool`
4. role 缺失 -> `messages_other`
5. assistant + tool_calls 仍归 `messages_assistant`
6. 结构化 content 可处理
7. 空消息列表返回空

### `PrefillMessagesSource`

1. prefill 被单独归类
2. 空 prefill 返回空

### `ToolSchemasSource`

1. tools 为空返回空
2. tools 非空生成 `tool_schemas`
3. 动态过滤后的 tools 正确反映

---

## 15. Assembler 测试

建议新增：

- `tests/context_engine/test_input_assembler.py`

覆盖点：

1. `assemble()` 同时返回 stable/dynamic/request nodes
2. node 顺序稳定
3. context metrics 仅统计 context nodes
4. request metrics 统计全部 nodes
5. `effective_system` 与旧实现一致
6. normalized messages 与旧实现一致
7. prefill 顺序正确
8. tool schemas 顺序正确

---

## 16. 等价性测试

这是最关键的一层。

建议新增：

- `tests/context_engine/test_request_equivalence.py`

覆盖点：

1. 同一输入下，legacy `effective_system` == new `effective_system`
2. 同一输入下，legacy `api_messages` == new `api_messages`
3. 同一输入下，legacy tools == new tools
4. 含 dynamic context 时完全等价
5. 含 prefill 时完全等价
6. 含 tool messages 时完全等价
7. 含 sanitized tool-result repair 时完全等价
8. 含 codex-specific fields 时完全等价
9. 含 anthropic multimodal flatten 时完全等价
10. 含 prompt caching 时核心语义输入等价

---

## 17. `run_agent` 迁移测试

建议新增：

- `tests/context_engine/test_run_agent_unified_engine_migration.py`

覆盖点：

1. shadow mode 下 new assembly 不改变请求结果
2. fallback legacy 时行为不变
3. 切主路径后行为仍不变
4. 失败结果仍带 metrics
5. interrupted/partial 结果仍带 metrics

---

## 18. 回归测试矩阵

必须回归以下大类：

1. `tests/context_engine/`
2. `tests/test_run_agent.py`
3. `tests/test_run_agent_codex_responses.py`
4. `tests/gateway/`
5. `tests/tools/`

重点关注：

- compression
- retries
- invalid tool calls
- invalid json args
- codex incomplete
- anthropic mode
- multimodal content
- prompt caching

## 18.1 测试覆盖是否“全面”的判定标准

为了避免“看起来很多测试，但关键路径没锁住”，这里定义覆盖完成标准。

### A. 结构覆盖

必须至少覆盖：

1. 新数据模型
2. 新 request sources
3. assembler 全量接口
4. transport adapter
5. legacy/new 等价对照

### B. 行为覆盖

必须至少覆盖：

1. 首轮
2. 多轮
3. tool-heavy
4. compression
5. retry
6. interrupt
7. fallback provider
8. cache control
9. multimodal

### C. 回归覆盖

必须重新跑：

1. `tests/context_engine/`
2. `tests/test_run_agent.py`
3. `tests/test_run_agent_codex_responses.py`
4. `tests/gateway/`
5. `tests/tools/`
6. 最终全量 `tests/`

### D. 等价性覆盖

以下必须逐项断言“完全一致”：

1. `effective_system`
2. `api_messages`
3. `tools`
4. `api_kwargs`
5. 最终用户可见结果

只要以上任一未锁定，就不能宣称测试覆盖完整。

---

## 19. 边界条件与异常测试

## 19.1 边界条件

1. 首轮无历史消息
2. 多轮长历史
3. 大量 tool results
4. 大量 tool schemas
5. 空 prefill
6. 有 prefill
7. 动态 source 全空
8. dynamic source 部分失败
9. sanitizer 注入 stub tool result
10. compression 后消息重写

## 19.2 异常测试

1. Source collect 抛错时 fail-soft
2. request source 解析异常时 fail-soft
3. transport adapter 异常时回退 legacy
4. metrics 构建失败不影响主请求
5. compare mismatch 时写日志并 fallback

## 19.3 之前未覆盖充分的边界条件补充

为了达到“绝对上下文控制权”的标准，以下边界必须显式进入设计与测试。

### 消息结构边界

1. `content` 为字符串
2. `content` 为 list[dict]
3. `content` 为 dict
4. 缺失 `content`
5. 缺失 `role`
6. assistant 带 `reasoning`
7. assistant 带 `tool_calls`
8. tool message 缺失 `tool_call_id`

### 流程边界

1. 首轮请求
2. 多轮普通对话
3. 多轮 tool-heavy 对话
4. continuation 分支
5. invalid tool name 分支
6. invalid tool JSON 分支
7. incomplete scratchpad 分支
8. empty content fallback 分支
9. interrupted during API wait
10. interrupted during retry sleep

### 模式边界

1. `chat_completions`
2. `codex_responses`
3. `anthropic_messages`
4. direct OpenAI fallback 到 codex path
5. OpenRouter + Claude + cache control

### 传输边界

1. anthropic multimodal flatten
2. codex instruction extraction
3. strict provider field stripping
4. cache marker insertion
5. sanitized tool result stub insertion

### 状态边界

1. parent agent
2. child agent
3. resumed session
4. compressed session
5. fallback provider activated

## 19.4 性能与并发设计

原文档此前对性能/并发考虑不够充分，这里补齐。

### 性能目标

重构后不应显著增加以下开销：

1. 每轮节点构建次数
2. 每轮字符串复制次数
3. 每轮消息深拷贝次数
4. shadow compare 阶段的常态开销

### 性能原则

1. Node 构建尽量引用现有对象，不无意义深拷贝
2. 仅在 transport adapter 层做必要的 payload copy
3. shadow compare 默认仅在 debug/dev 或显式开关下启用
4. compare 结果优先做结构级摘要，不总是 dump 全量 payload

### 并发原则

1. `ContextAssembler` 必须是 agent-scoped，不允许跨 agent 共享可变状态
2. `InputAssembly` 必须是不可变快照语义
3. child agent 不得覆写 parent 的 assembly / metrics
4. CLI 读取 metrics 时只能读最近一次原子快照

### 具体并发风险

1. 共享 assembler 导致 agent A/B 混用 `_agent`
2. shadow compare 把上轮 assembly 残留到下轮
3. child agent 复用 parent metrics 字段
4. interrupt/retry 时 metrics 被中途部分覆盖

### 建议措施

1. 移除或限制进程级全局 assembler 复用
2. 每次 `assemble()` 返回全新快照对象
3. `_last_*_metrics` 只在完整 assembly 构建完成后一次性替换
4. parent/child agent 各自维护独立 assembler 与 metrics

## 19.5 失败恢复与回滚策略

商业发布级重构必须明确失败策略。

### 当 new engine 构建失败时

行为：

1. 记录 warning
2. 回退 legacy path
3. 不影响本轮请求发送

### 当 shadow compare 不一致时

行为：

1. 记录 diff 摘要
2. 默认继续走 legacy
3. 不在生产环境中断主流程

### 当 transport adapter 失败时

行为：

1. fallback 到 legacy payload builder
2. 保留 compare 证据

### 当 metrics 构建失败时

行为：

1. 不影响请求
2. UI 降级为空或旧 context breakdown

---

## 20. 重构完成后的理想状态

完成重构后，系统应满足：

1. 任何进入模型的语义输入都对应一个 `InputNode`
2. 任何 request-level metrics 都由 `InputAssembly` 导出
3. `run_agent.py` 不再自己拼 request 语义
4. CLI 读取的 breakdown 来自 Engine 权威模型
5. `messages`、`prefill_messages`、`tool_schemas` 成为 ContextEngine 一等公民
6. provider-specific 逻辑只存在于 transport adapter

这时才能合理宣称：

- ContextEngine 取得了完整上下文控制权

---

## 21. 当前实现与目标状态的差距清单

当前实现仍存在这些差距：

1. `messages` 仅被统计，不是 Engine source
2. `prefill_messages` 仅被统计，不是 Engine source
3. `tool_schemas` 仅被统计，不是 Engine source
4. `run_agent.py` 仍掌握 request semantic assembly
5. request metrics 仍属于装配后旁路导出
6. transport 与 semantic assembly 尚未完全分层

---

## 22. 最终建议

这次不要继续在 `request_metrics.py` 或 `run_agent.py` 周围打补丁。

真正正确的方向是：

1. 扩展 ContextEngine 为 full-input engine
2. 引入统一 `InputNode` / `InputAssembly`
3. 把 `messages` / `prefill` / `tool_schemas` 纳入 source graph
4. 通过双轨等价性测试确保零业务行为变化
5. 最后再切换主装配权

只有这样，Hermes 才能真正做到：

- 绝对的上下文控制权
- 完整的上下文可解释性
- 不依赖补丁式旁路统计的长期可维护架构

---

## 23. 严格实施拆分

本节把设计压缩为可以直接执行的重构阶段。

约束：

1. 严格按照本设计执行
2. 不允许跳阶段
3. 不允许自由发挥扩展范围
4. 每阶段必须有对应测试
5. 每阶段必须有明确验收标准
6. 任一阶段未通过，不进入下一阶段

---

## 24. 实施总表

| Phase | 目标 | 允许改动 | 禁止改动 | 必测范围 |
|------|------|------|------|------|
| A | 建立统一数据模型 | 新增 `InputNode` / `InputAssembly` | 不改主路径 | 数据模型测试 |
| B | 建立 request-level InputSource | 新增 request sources | 不改主路径 | source 单测 |
| C | 扩展 assembler 全量接口 | 新增 `assemble()` | 不切换调用方 | assembler 测试 |
| D | 引入 transport adapter | 新增 transport 层 | 不替换 legacy payload | transport/等价性测试 |
| E | 接入 shadow compare | `run_agent` 双轨对照 | 不切主路径 | 迁移测试 |
| F | 切换主路径 | Engine 接管 request assembly | 不改变业务行为 | 全量回归 |
| G | 删除桥接逻辑 | 删除临时旁路统计/拼接 | 不删除兼容测试 | 全量回归 + 清理测试 |

---

## 25. Phase A — 统一数据模型

### 25.1 目标

建立 full-input engine 的基础数据模型，但不改变任何调用方。

### 25.2 必改文件

1. 新增 `agent/context_engine/input_models.py`
2. 修改 `agent/context_engine/__init__.py`

### 25.3 必须新增的对象

1. `InputNode`
2. `InputAssembly`
3. 可选兼容快照：
   - `AssemblySnapshots`

### 25.4 明确禁止

1. 不修改 `run_agent.py` 业务路径
2. 不修改 `cli.py` 展示逻辑
3. 不修改现有 `ContextChunk` 行为
4. 不修改现有 `ContextMetrics` 语义

### 25.5 验收标准

1. 新模型可导入
2. 现有测试不破坏
3. `__init__` 导出正确

### 25.6 必做测试

新增：

- `tests/context_engine/test_input_models.py`

覆盖：

1. `InputNode` 创建
2. `kind` 校验
3. `stage` 校验
4. `InputAssembly` 可持有 stable/dynamic/request nodes
5. 空 assembly 合法
6. `__init__` 导出测试

### 25.7 阶段完成条件

必须满足：

- 新测试全绿
- `tests/context_engine/test_init.py` 仍全绿

---

## 26. Phase B — request-level InputSource

### 26.1 目标

把 messages / prefill / tool schemas 提升为正式 source，但仍不切主路径。

### 26.2 必改文件

1. 新增 `agent/context_engine/input_sources.py`
2. 可选修改 `agent/context_engine/context.py`

### 26.3 必须新增的 source

1. `ConversationMessagesSource`
2. `PrefillMessagesSource`
3. `ToolSchemasSource`

### 26.4 Source 输入要求

1. `ConversationMessagesSource`
   输入：
   - `conversation_history`
   输出：
   - `InputNode(kind="message")`

2. `PrefillMessagesSource`
   输入：
   - `prefill_messages`
   输出：
   - `InputNode(kind="prefill")`

3. `ToolSchemasSource`
   输入：
   - `tool_schemas`
   输出：
   - `InputNode(kind="tool_schema")`

### 26.5 明确禁止

1. 不改 `run_agent.py` 当前组装行为
2. 不改现有 `sources.py` 语义
3. 不改 provider payload

### 26.6 验收标准

1. request sources 可独立工作
2. 结构化消息可处理
3. assistant/tool/user 分类准确

### 26.7 必做测试

新增：

- `tests/context_engine/test_request_sources.py`

覆盖：

1. user -> `messages_user`
2. assistant -> `messages_assistant`
3. assistant + tool_calls -> `messages_assistant`
4. tool -> `messages_tool`
5. 无 role -> `messages_other`
6. prefill -> `prefill_messages`
7. tools -> `tool_schemas`
8. 空输入返回空
9. 结构化 content
10. 缺失 content

### 26.8 阶段完成条件

必须满足：

- request source 测试全绿
- 旧 `tests/context_engine/test_sources_*` 不回归

---

## 27. Phase C — assembler 全量接口

### 27.1 目标

在不影响旧接口的前提下，引入 `assemble()` 统一输出 `InputAssembly`。

### 27.2 必改文件

1. 修改 `agent/context_engine/assembler.py`
2. 视需要修改 `agent/context_engine/context.py`
3. 视需要新增 `agent/context_engine/metrics_v2.py`

### 27.3 必须新增的函数

1. `ContextAssembler.assemble(...)`
2. `_collect_input_nodes(...)`
3. `_build_effective_system(...)`
4. `_build_normalized_messages(...)`

### 27.4 兼容要求

必须保留：

1. `assemble_stable()`
2. `assemble_dynamic()`

且它们必须内部依赖新 `assemble()`，而不是走两套逻辑。

### 27.5 明确禁止

1. 不切换 `run_agent.py` 到新主路径
2. 不删除旧 `AssemblyResult`
3. 不更改旧 context source 顺序

### 27.6 验收标准

1. `assemble()` 能产出完整 `InputAssembly`
2. stable/dynamic/request nodes 顺序稳定
3. 旧接口仍工作

### 27.7 必做测试

新增：

- `tests/context_engine/test_input_assembler.py`

覆盖：

1. `assemble()` 产出三类 nodes
2. `effective_system` 正确
3. normalized messages 正确
4. prefill/tool_schemas 保留
5. context metrics 仅统计 context nodes
6. request metrics 统计全部 nodes
7. `assemble_stable()` 与旧结果兼容
8. `assemble_dynamic()` 与旧结果兼容

### 27.8 阶段完成条件

必须满足：

- `tests/context_engine/test_assembler.py`
- `tests/context_engine/test_input_assembler.py`
- `tests/context_engine/test_equivalence.py`

全部通过

---

## 28. Phase D — transport adapter

### 28.1 目标

把 provider/API mode payload shaping 从 `run_agent.py` 语义装配中分离出去。

### 28.2 必改文件

1. 新增 `agent/context_engine/transport.py`
2. 修改 `run_agent.py`

### 28.3 必须实现的 adapter

1. `ChatCompletionsTransportAdapter`
2. `CodexResponsesTransportAdapter`
3. `AnthropicMessagesTransportAdapter`

### 28.4 adapter 必须承接的行为

1. system prepend
2. prefill insertion
3. anthropic multimodal flatten
4. codex instructions/messages 拆分
5. cache_control injection
6. strict field stripping 的适配挂接点

### 28.5 明确禁止

1. 不改变 transport 结果
2. 不改变 `_build_api_kwargs()` 对外业务语义
3. 不改变 `_prepare_anthropic_messages_for_api()` 行为结果

### 28.6 验收标准

1. adapter 可从 `InputAssembly` 构造 legacy 等价 payload
2. 各 API mode 输出与旧逻辑一致

### 28.7 必做测试

新增：

- `tests/context_engine/test_transport_adapter.py`

覆盖：

1. chat completions payload 等价
2. codex payload 等价
3. anthropic payload 等价
4. multimodal flatten 等价
5. cache_control 插入位置等价
6. prefill 插入顺序等价

### 28.8 阶段完成条件

必须满足：

- transport adapter 全绿
- 新旧 payload 对照测试全绿

---

## 29. Phase E — shadow compare 接入

### 29.1 目标

让新 Engine 在真实流程中跟跑，但不接管请求发送。

### 29.2 必改文件

1. 修改 `run_agent.py`
2. 视需要修改 `hermes_cli/config.py`
3. 视需要修改 `cli.py`（仅 debug 输出，不改业务）

### 29.3 必须新增的函数

1. `_assemble_input_graph(...)`
2. `_build_transport_payload(...)`
3. `_compare_engine_and_legacy_payload(...)`

### 29.4 运行方式

1. legacy path 继续构造并发送请求
2. engine path 同时构造 `InputAssembly` 和 transport payload
3. 做结构级比较
4. 若 diff：
   - warning
   - 记录摘要
   - 继续 legacy

### 29.5 明确禁止

1. 不允许新路径直接发请求
2. 不允许 diff 时影响用户请求

### 29.6 验收标准

1. shadow compare 可稳定运行
2. diff 能定位到字段/顺序差异
3. 主业务行为不变

### 29.7 必做测试

新增：

- `tests/context_engine/test_run_agent_unified_engine_migration.py`
- `tests/context_engine/test_request_equivalence.py`

覆盖：

1. shadow compare 不影响最终请求
2. diff 时 fallback 正常
3. compare 输出可定位差异
4. metrics 仍正常

### 29.8 阶段完成条件

必须满足：

- shadow compare 测试全绿
- `tests/test_run_agent.py`
- `tests/test_run_agent_codex_responses.py`

无回归

---

## 30. Phase F — 切主路径

### 30.1 目标

让 ContextEngine 正式接管完整 request assembly。

### 30.2 必改文件

1. 修改 `run_agent.py`
2. 修改 `cli.py`

### 30.3 切换要求

切主路径时：

1. `run_agent.py` 不再自己拼 request semantic payload
2. `ContextAssembler.assemble()` 成为唯一语义装配入口
3. transport adapter 成为唯一 provider payload 构造入口

### 30.4 明确禁止

1. 不允许 legacy/new 双语义并存超过必要迁移窗口
2. 不允许保留新的旁路 builder
3. 不允许在 CLI 层再自己推导 bucket 语义

### 30.5 验收标准

1. Engine 主路径可独立工作
2. 行为与 legacy 完全一致
3. CLI breakdown 完全来自 Engine 权威结果

### 30.6 必做测试

必须重跑：

1. `tests/context_engine/`
2. `tests/test_run_agent.py`
3. `tests/test_run_agent_codex_responses.py`
4. `tests/gateway/`
5. `tests/tools/`
6. 全量 `tests/`

### 30.7 阶段完成条件

必须满足：

- 全量测试通过
- shadow compare 不再报告差异

---

## 31. Phase G — 删除桥接逻辑

### 31.1 目标

收尾清理，避免系统继续背着临时桥接层。

### 31.2 必改文件

1. 删除或瘦身 `agent/context_engine/request_metrics.py`
2. 删除 `run_agent.py::_update_request_metrics`
3. 删除仅为迁移存在的 compare/fallback 临时壳（前提是已稳定）

### 31.3 明确禁止

1. 不删除仍被测试依赖的兼容接口，除非同步完成迁移
2. 不删除必要的导出符号

### 31.4 验收标准

1. 不再存在旁路 request metrics 主逻辑
2. 统一模型只剩一套

### 31.5 必做测试

1. 清理后全量回归
2. 删除代码路径的 dead-code 检查

### 31.6 阶段完成条件

必须满足：

- 全量测试仍全绿
- 统一控制权已完全转移到 ContextEngine

---

## 32. 实施纪律

执行时必须遵守：

1. 先写阶段测试，再写阶段代码
2. 阶段代码完成后立即跑对应测试
3. 未通过阶段验收，不得进入下一阶段
4. 不允许“顺手修 unrelated 行为”
5. 任何额外想法都只能进入新文档，不得混入当前实施

---

## 33. 每阶段提交建议

为保证可审查性，建议一个阶段至少一个独立提交。

推荐提交边界：

1. `Phase A: add InputNode/InputAssembly models`
2. `Phase B: add request-level input sources`
3. `Phase C: add unified assemble() interface`
4. `Phase D: add transport adapters`
5. `Phase E: wire shadow compare in run_agent`
6. `Phase F: switch primary request assembly to engine`
7. `Phase G: remove bridge logic`

这样可以做到：

- 每次 diff 可审查
- 每次测试可定位
- 每次回滚可独立进行
