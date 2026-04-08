# Hermes 记忆系统与上下文组装引擎研究

> 补充专项研究：`wzh-research/hermes-graph-memory-integration-analysis.md`  
> 该文档专门对照本地导入的 graph-memory 插件与 Hermes，包含源码级流程、决策树、冲突/重复/价值判断，以及 Hermes 架构下的整合建议。

## 1. 研究目标

本文档面向后续深入研究 Hermes Agent 的记忆体系与上下文装配机制，回答四个核心问题：

1. Hermes 到底有哪些“记忆层”。
2. 这些记忆在什么时机被加载、写回、检索、压缩。
3. Hermes 每一轮调用大模型之前，系统提示词和用户上下文是如何组装的。
4. 为了控制 token、保持缓存稳定、避免记忆污染，Hermes 做了哪些工程设计。

本文基于仓库内的实现与官方开发文档，重点参考：

- `run_agent.py`
- `tools/memory_tool.py`
- `tools/session_search_tool.py`
- `agent/prompt_builder.py`
- `agent/context_compressor.py`
- `agent/context_references.py`
- `gateway/run.py`
- `honcho_integration/session.py`
- `website/docs/developer-guide/prompt-assembly.md`
- `website/docs/developer-guide/context-compression-and-caching.md`

## 2. 总体结论

Hermes 不是单一“记忆模块”，而是一个分层上下文系统：

1. **本地持久记忆**：`MEMORY.md` 与 `USER.md`，容量小、强约束、每个 session 开始时注入系统提示词。
2. **会话级长期召回**：`session_search` 基于 SQLite FTS5 搜索历史会话，再用辅助模型总结相关 session。
3. **可选 Honcho 记忆层**：用于跨 session、跨平台的用户建模与语义召回，可和本地记忆并存。
4. **过程性记忆**：skills 索引与 skill 文件，本质上是“可执行经验”的沉淀。
5. **项目上下文层**：`SOUL.md`、`.hermes.md`、`AGENTS.md`、`CLAUDE.md`、`.cursorrules` 等，不是记忆，但会参与稳定上下文装配。
6. **动态上下文层**：当前轮的 `@file:` / `@diff` 引用展开、ephemeral system prompt、插件注入、Honcho turn context。
7. **上下文压缩层**：当消息历史过长时，Hermes 不直接丢弃历史，而是先裁剪旧 tool 输出、再生成结构化摘要。

Hermes 的设计主线非常清晰：**把“稳定、可缓存”的内容和“当前轮临时附加”的内容严格分离**。这既是它的记忆设计原则，也是它的 prompt assembly 原则。

---

## 3. 记忆系统全景

### 3.1 本地记忆：`MEMORY.md` + `USER.md`

Hermes 的内建持久记忆由两个文件组成，默认位于 `~/.hermes/memories/`：

- `MEMORY.md`：代理自己的长期笔记，例如环境事实、工程约定、工具坑点、项目稳定信息。
- `USER.md`：用户画像，例如偏好、沟通风格、角色、习惯。

在 `tools/memory_tool.py` 里，这两类记忆由 `MemoryStore` 统一管理。关键设计有：

1. **双状态模型**
   - `_system_prompt_snapshot`：session 启动时从磁盘加载的一份冻结快照，只用于系统提示词注入。
   - `memory_entries` / `user_entries`：实时内存状态，响应本轮工具调用，并立即落盘。

2. **容量强约束**
   - `memory_char_limit` 默认 2200 字符。
   - `user_char_limit` 默认 1375 字符。
   - Hermes 按字符而不是 token 限制，原因是字符限制更模型无关。

3. **条目组织方式**
   - 使用 `\n§\n` 作为条目分隔符。
   - 支持多行条目。
   - 启动时会去重，保留第一次出现的顺序。

4. **读写策略**
   - 读取：直接读完整文件并按分隔符切条。
   - 写入：通过临时文件 + `os.replace()` 原子替换，避免并发写导致读到空文件。
   - 修改时使用单独 `.lock` 文件进行排他锁，保证 read-modify-write 安全。

5. **变更接口**
   - `add`
   - `replace`
   - `remove`
   - `replace/remove` 使用“唯一子串匹配”而不是 ID。

6. **安全扫描**
   - 写入前会扫描 prompt injection / secrets exfiltration / 隐形 Unicode。
   - 这点非常关键，因为这些内容最终会进入系统提示词。

### 3.2 为什么本地记忆是“冻结快照”

Hermes 最重要的一个设计点是：**session 中途记忆写入不会修改本 session 的 system prompt**。

这在 `MemoryStore.format_for_system_prompt()` 和 `run_agent.py::_build_system_prompt()` 的配合里体现得很明确：

- session 初始化时，`MemoryStore.load_from_disk()` 读取磁盘并构建 `_system_prompt_snapshot`
- `_build_system_prompt()` 只读取这个 snapshot
- 后续 `memory` 工具调用会更新 live state 并落盘，但不会回写已经缓存的系统提示词

这样做的根本原因是：

1. 保持系统提示词前缀稳定，最大化 prompt cache 命中率。
2. 避免会话中途“记忆层突变”导致模型行为不连续。
3. 将“持久化成功”与“当前 session 可见”分离，降低复杂度。

代价是：**记忆写入对未来 session 生效，不保证当前 session 立即作为系统层生效**。当前 session 若想看到新状态，依赖的是工具返回值，而不是系统 prompt 重建。

### 3.3 `memory` 工具的真实定位

`memory` 工具不是检索工具，而是**小容量、强筛选、强约束的结构化写入工具**。

它适合保存：

- 用户偏好与纠正
- 工程环境事实
- 项目约定
- 稳定工作流经验

它明确不适合保存：

- 临时任务过程
- 大段日志
- 一次性调试状态
- 大量对话历史

这说明 Hermes 把“持久化记忆”理解为高价值、低容量、人工精选的事实层，而不是完整记忆数据库。

---

## 4. 更广义的“记忆层”：不仅是 `memory_tool`

### 4.1 `session_search`：长时历史检索层

`tools/session_search_tool.py` 提供了另一种记忆能力：**从过去所有 session 的 transcript 中搜索并总结**。

其工作流是：

1. 在 SQLite 中使用 FTS5 搜索匹配消息。
2. 将命中按 session 聚合，取 top-N session。
3. 读取对应 session 的完整对话。
4. 以 query 为中心截断到约 100k chars。
5. 用辅助模型生成针对 query 的 session 摘要。

这和本地记忆的差异很明显：

- 本地记忆：小、快、固定注入、需要 agent 主动维护。
- `session_search`：大、慢、按需检索、自动保留历史。

因此，Hermes 的长期记忆实际上分为两种：

1. **Always-on memory**：始终带进 prompt 的小容量事实。
2. **Recall-on-demand memory**：需要时再搜索的历史会话语义回忆。

### 4.2 Skills：过程性记忆

虽然 skills 不属于“memory”工具，但在架构上它们明显承担了过程性记忆角色。

`agent/prompt_builder.py` 会把 skills 的索引压缩进系统提示词，指导模型：

- 做任务前先看有没有匹配 skill
- 复杂任务完成后可沉淀 skill
- 发现 skill 过时要更新

这意味着 Hermes 将“事实记忆”和“方法记忆”拆开了：

- 事实记忆进入 `MEMORY.md` / `USER.md`
- 方法记忆进入 skills

这是很合理的工程分层，因为过程知识通常比事实知识更长、更结构化，也更需要复用。

### 4.3 Honcho：AI-native 记忆扩展层

当 Honcho 启用时，Hermes 会接入另一套跨 session、跨 peer 的记忆系统。

从 `run_agent.py` 与 `honcho_integration/session.py` 可以看出，Honcho 的定位不是替换 SQLite 或本地记忆，而是补充：

- 用户表示层（representation）
- peer card
- AI peer 自我表示
- 语义上下文召回
- dialectic query

Hermes 支持 `memoryMode` / per-peer memory mode：

- `hybrid`：本地记忆 + Honcho 并行
- `honcho`：某些 peer 仅使用 Honcho，关闭本地对应写入
- `local`：仅本地

这意味着 Hermes 允许不同“记忆背板”并存，并且允许按 AI peer / user peer 精细控制写入去向。

---

## 5. Agent 初始化时记忆是如何接入的

### 5.1 本地记忆初始化

在 `run_agent.py` 的 `AIAgent.__init__()` 中：

1. 读取 `config.yaml` 中的 memory 配置。
2. 决定是否启用：
   - `memory_enabled`
   - `user_profile_enabled`
3. 构造 `MemoryStore`
4. 立即执行 `load_from_disk()`

这表示：**本地记忆是在 agent 初始化时一次性装载的**，而不是每次 API 调用前动态读取磁盘。

### 5.2 Honcho 初始化

同样在 `AIAgent.__init__()` 中，如果未 `skip_memory`：

1. 读取 Honcho 配置
2. 满足条件时创建 `HonchoSessionManager`
3. 调用 `_activate_honcho()`
4. 根据 per-peer memory mode，可能关闭本地 `MEMORY.md` 或 `USER.md` 写入

这一步非常重要，因为它说明 Hermes 并不是“无条件叠加”本地记忆和 Honcho，而是允许互斥。

---

## 6. 上下文组装引擎：系统提示词的稳定层

### 6.1 `_build_system_prompt()` 是核心装配入口

Hermes 系统提示词的主要装配函数是 `run_agent.py::_build_system_prompt()`。

它组装的是**稳定层**，即会被缓存、会写入 session DB、尽量跨本 session 保持不变的内容。实际顺序如下：

1. Agent identity
   - 优先 `SOUL.md`
   - 否则 `DEFAULT_AGENT_IDENTITY`
2. Tool-aware behavioral guidance
   - memory guidance
   - session_search guidance
   - skills guidance
3. Tool-use enforcement
   - 对 GPT/Codex 等模型注入“说要做就必须调用工具”的约束
4. Honcho static block
   - 告知当前 memory mode、recall mode、可用 honcho tools
5. 外部 `system_message`
6. Frozen local memory
   - `MEMORY.md`
   - `USER.md`
7. Skills index
8. Context files
   - `.hermes.md` / `HERMES.md`
   - `AGENTS.md`
   - `CLAUDE.md`
   - `.cursorrules` / `.cursor/rules/*.mdc`
9. 时间戳 / session / model / provider
10. 平台 hint

这正是 Hermes 的“稳定上下文骨架”。

### 6.2 为什么它强调“cached system prompt”

Hermes 的一个核心工程目标是提升 provider 侧 prompt cache 命中率。

因此它在设计上要求：

- system prompt 尽可能 session 内不变
- continuation turn 尽可能复用同一份 system prompt
- 会变化的内容延迟到 API-call-time 再拼接

在 `run_agent.py` 的主循环里，如果 `_cached_system_prompt` 为空：

- 对于继续中的 session，优先从 session DB 取出之前保存过的 system prompt
- 否则才重新 `_build_system_prompt()`

这个细节很关键。它说明 Hermes 不是简单“每轮重新拼 prompt”，而是会**复用上一轮落库的系统提示词快照**，从而避免因为磁盘记忆更新或上下文文件变化导致缓存前缀漂移。

### 6.3 Context files 的发现与优先级

`agent/prompt_builder.py` 里，项目上下文文件并不是全都加载，而是采用“优先级命中一次”的策略：

1. `.hermes.md` / `HERMES.md`
   - 从当前目录向上找到 git root
2. `AGENTS.md`
   - 当前目录
3. `CLAUDE.md`
   - 当前目录
4. `.cursorrules` / `.cursor/rules/*.mdc`
   - 当前目录

特点有三点：

1. **不是全部累加，而是项目上下文只选一个主来源**
2. `SOUL.md` 单独处理，不和这些项目上下文混在一起
3. 所有上下文文件注入前都会做安全扫描与截断

### 6.4 上下文文件的安全与截断

`prompt_builder.py` 对上下文文件做了专门扫描：

- prompt injection pattern
- HTML 注释隐藏注入
- 隐形 Unicode
- 凭据读取 / exfiltration 模式

文件过长时会按 head/tail 截断：

- 默认最大 20,000 chars
- 保留头部约 70%
- 保留尾部约 20%
- 中间插入 truncation marker

这说明 Hermes 把项目上下文文件视作“高风险输入”，不是无脑注入。

### 6.5 Skills 索引的缓存策略

skills 不直接把完整 skill 全塞进 prompt，而是只注入一个 compact index。

`build_skills_system_prompt()` 有两层缓存：

1. 进程内 LRU cache
2. 磁盘快照 `.skills_prompt_snapshot.json`

只有真正需要时模型才通过 `skill_view` 去加载某个 skill 的正文。

这个设计对上下文组装非常重要，因为它避免了“技能库越大，system prompt 越大”的线性膨胀。

---

## 7. API 调用时的动态上下文装配

Hermes 并不是只靠 `_build_system_prompt()`。真正发请求前，还有一层**动态装配**。

### 7.1 不进入 cached prompt 的内容

以下内容明确不应进入 cached/stored system prompt：

- `ephemeral_system_prompt`
- 本轮插件注入的 context
- gateway 派生的临时上下文
- Honcho 后续轮次的 turn context
- `@file:` / `@diff` 展开结果

换句话说，Hermes 将上下文分为：

- **稳定层**：可缓存、可落库
- **瞬时层**：只在本次 API 调用可见

### 7.2 `ephemeral_system_prompt`

`gateway/run.py` 和 `run_agent.py` 都表明，ephemeral system prompt 会在 API-call-time 拼接到 effective system prompt 上，但不会写入缓存的 system prompt，也不会污染 session DB 中的稳定快照。

这适合放：

- 某次运行的临时策略
- 插件返回的每轮约束
- gateway 平台派生信息

### 7.3 Honcho 的两段式注入策略

Hermes 对 Honcho 的注入非常讲究缓存稳定性：

1. **第一轮**
   - 如果拿到 Honcho prefetch 结果，会把它 baked into `_cached_system_prompt`
   - 这样整场 session 都稳定可复用

2. **后续轮次**
   - 新取到的 Honcho recall 不再改 cached system prompt
   - 只在当前轮用户消息发往 API 前，作为 turn context 动态附着

这是一个非常典型的“首轮固定化、后续瞬时化”设计，兼顾连续性与 cache stability。

### 7.4 `@` 上下文引用展开

`agent/context_references.py` 实现了消息级上下文引用扩展：

- `@file:path`
- `@file:path:10-20`
- `@folder:path`
- `@diff`
- `@staged`
- `@git:N`
- `@url:https://...`

其处理位置在 gateway 收到消息后、正式进入 agent 前。

工作机制：

1. 解析引用语法
2. 读取文件/目录/diff/url 内容
3. 估算注入 token
4. 施加 25% soft limit / 50% hard limit
5. 将展开内容拼到用户消息后部

这意味着 Hermes 的“上下文组装引擎”不仅处理系统提示词，还处理**用户消息增强**。

### 7.5 插件 pre-llm hook

在 agent 主循环进入 API 调用前，Hermes 还会触发 `pre_llm_call` hook：

- 插件可返回 `context`
- Hermes 把这些 context 视为当前轮临时 system augmentation

这使得 Hermes 的上下文装配具备很强的可扩展性：很多运行态上下文都不需要修改主 prompt builder，而是通过 hook 注入。

---

## 8. 长上下文控制：压缩引擎如何工作

### 8.1 双层压缩体系

Hermes 有两层独立的压缩/卫生机制：

1. **Gateway session hygiene**
   - 入口前安全网
   - 默认在上下文接近 85% 时触发
2. **Agent ContextCompressor**
   - 主压缩器
   - 默认在 50% 阈值触发

这两层的目的不同：

- Gateway 层负责防止 session 在 agent 还没出手前就已经爆窗。
- Agent 层负责日常、渐进式上下文管理。

### 8.2 `ContextCompressor` 的算法

`agent/context_compressor.py` 的流程可以概括为：

1. **先裁剪旧 tool 输出**
   - 超过 200 chars 的旧 tool result 直接替换成短占位符
2. **保护头部**
   - 系统提示词 + 最早几条关键消息
3. **保护尾部**
   - 按 token budget 保护最近一段消息
   - 如果预算过低，则至少保留 `protect_last_n`
4. **总结中间段**
   - 用辅助模型生成结构化摘要
5. **多次压缩时迭代更新摘要**
   - 不是每次重新总结，而是带着旧摘要继续演化
6. **修正 tool call / tool result 对**
   - 避免压缩后出现孤儿 tool 消息

### 8.3 压缩后的摘要长什么样

摘要不是自由文本，而是结构化模板，包含：

- Goal
- Constraints & Preferences
- Progress
- Key Decisions
- Relevant Files
- Next Steps
- Critical Context

这说明 Hermes 不是为了“缩短对话”而总结，而是为了保留**可执行继续工作的状态机信息**。

### 8.4 预检压缩

在主循环正式跑之前，`run_agent.py` 还会先做一次 preflight compression：

- 估算 messages + system prompt + tools schema 的 token
- 超过阈值则先压缩，再进入主推理循环

这样可以避免切换到小 context model 时直接 API 400。

---

## 9. Session 切换、重置与记忆刷新

### 9.1 为什么需要 memory flush

由于 Hermes 的本地记忆是 agent 自己主动维护的，而不是每轮自动抽取，所以一旦 session 即将被重置或过期，必须给 agent 一个“最后整理记忆”的机会。

`gateway/run.py::_flush_memories_for_session()` 就是这个兜底机制。

### 9.2 Flush agent 的工作方式

当 session 因 inactivity、scheduled reset、resume/switch 等事件即将丢失上下文时，gateway 会：

1. 从 transcript 中载入 user/assistant 历史
2. 创建一个临时 `AIAgent`
3. 只启用 `memory` 与 `skills` 工具集
4. 构造一条 synthetic prompt，要求 agent：
   - 回顾对话
   - 提炼值得保存的记忆
   - 如有必要保存 skill
   - 不要回复用户

同时 gateway 还会把当前磁盘上的 `MEMORY.md` / `USER.md` 内容附加给 flush agent，提醒它不要覆盖并发 session 已经写入的新信息。

这个设计体现了 Hermes 的一个重要哲学：**记忆保存不是副作用，而是一个独立、显式、可审视的 agent 行为**。

### 9.3 和 Honcho flush 的关系

flush 完成本地记忆后，如果临时 agent 持有 Honcho manager，还会显式 shutdown，确保排队中的 Honcho 写入被冲刷出去。

因此 Hermes 在 session 生命周期末端同时照顾：

- 本地持久记忆
- skills
- Honcho 异步写入

---

## 10. 研究视角下的关键设计原则

### 10.1 稳定前缀优先

Hermes 的 prompt assembly 几乎所有关键决策都服务于一个目标：**让系统提示词和早期消息前缀尽可能稳定**。

体现为：

- memory snapshot 冻结
- continuing session 复用落库 system prompt
- ephemeral prompt 不入库
- 后续 Honcho recall 不改 cached prompt
- skills 只注入索引

### 10.2 小记忆 + 大检索 的组合

Hermes 没有试图让 `MEMORY.md` 承担全部长期记忆，而是把长期记忆拆成：

- 小而高价值的 curated memory
- 大而按需的 session search
- 更强语义层的 Honcho

这比“把所有历史都压成一份大记忆文件”更合理。

### 10.3 记忆写入与可见性解耦

很多 agent 系统喜欢“写入后马上回注 prompt”。Hermes 没这么做。

它选择：

- 立即落盘，保证 durability
- 当前 session 不重建 system prompt，保证 cache stability
- 下一 session 再以稳定形式注入

这是偏工程化而不是偏“即时一致性”的设计。

### 10.4 所有高权重上下文都要过安全扫描

Hermes 对以下内容都做了 prompt injection 防护：

- memory entries
- context files
- `@` 引用路径范围

说明它默认认为“凡是可能进入上层 prompt 的内容，都具有攻击面”。

### 10.5 摘要是工作状态摘要，不是聊天摘要

ContextCompressor 的结构化模板表明：Hermes 更在乎“下一轮能否继续工作”，而不是“上一轮聊了什么”。

这对研究很重要，因为它说明 Hermes 的上下文压缩目标是 **task continuity**，不是 conversation archival。

---

## 11. 一个完整的端到端链路

下面用一条典型路径说明 Hermes 的真实运行机制。

### 11.1 新 session 开始

1. `AIAgent` 初始化。
2. 加载本地 `MEMORY.md` / `USER.md`，形成 frozen snapshot。
3. 若启用 Honcho，则初始化 Honcho session manager。
4. 构建 `_cached_system_prompt`：
   - identity
   - behavior guidance
   - Honcho static block
   - local memory snapshot
   - skills index
   - context files
   - timestamp/platform
5. 若第一轮已有 Honcho context，则直接 bake 到 cached prompt。

### 11.2 用户发来一条消息

1. gateway 先处理 `@file:` / `@diff` 等上下文引用。
2. agent 将该消息加入 message history。
3. 如果是 continuing session，优先复用 session DB 中已有的 system prompt。
4. preflight 检查 context 是否过大，必要时先压缩。
5. 收集插件 `pre_llm_call` 上下文。
6. 若有 Honcho turn context，则只对当前轮 user message 动态附加。
7. 组装 API 请求并调用模型。

### 11.3 模型调用 `memory` 工具

1. `memory_tool()` 修改 live entries。
2. 立即持久化到 `MEMORY.md` / `USER.md`。
3. 返回更新后的 live state 给模型。
4. 当前 session 的 cached system prompt 不变。
5. 未来 session 启动时，新记忆才以 snapshot 形式注入。

### 11.4 对话太长

1. Gateway hygiene 或 agent compressor 触发。
2. 老 tool 输出先被清理。
3. 中间历史被总结成结构化摘要。
4. 头尾关键上下文保留。
5. 新压缩后的历史继续工作。

### 11.5 session 即将结束

1. gateway 触发 memory flush。
2. 临时 agent 读取 transcript。
3. 再次尝试把值得保留的信息写入 memory / skills。
4. 如启用 Honcho，冲刷异步写入。

---

## 12. 对后续研究最值得深入的点

如果后续要继续研究 Hermes，我建议优先沿这几个方向深入：

### 12.1 Memory flush 的质量与策略

值得研究：

- flush prompt 是否足够稳定
- flush 何时漏记关键事实
- flush 与正常回合中的主动记忆写入是否存在冲突

### 12.2 Session search 与 local memory 的协同

值得研究：

- 模型何时倾向于用 `memory`
- 何时倾向于用 `session_search`
- 是否存在应该进入本地记忆却只存在于 session transcript 的高价值信息

### 12.3 Honcho recall mode 的实际行为差异

值得研究：

- `context`
- `tools`
- `hybrid`

尤其要看：

- 哪种模式对 cache 稳定性影响最小
- 哪种模式对 continuity 最好
- 哪种模式最容易造成重复或冲突记忆

### 12.4 Context assembly 的可观测性

当前 Hermes 的设计非常工程化，但研究时最好补一套可视化手段：

- 每轮实际 system prompt 的层级 diff
- ephemeral vs cached 的清晰分界
- memory snapshot 与 live disk state 的对照

这会极大提升后续做机制实验时的可解释性。

---

## 13. 最终判断

从架构上看，Hermes 的记忆与上下文设计已经相当成熟，其关键特点可以概括为：

1. **把记忆视为多层系统，而不是一个文件或一个向量库。**
2. **把上下文装配视为缓存工程问题，而不仅是提示词拼接问题。**
3. **把长期事实、过程知识、会话历史、语义用户建模明确拆层。**
4. **把安全、并发一致性、prefix cache 命中率纳入一等设计目标。**

如果把 Hermes 当作研究对象，可以把它理解为：

**一个以稳定 system prompt 为核心、以多种记忆后端协同驱动的 agent context operating system。**

这个判断对后续继续研究非常重要，因为它意味着分析 Hermes 时，不应只看 `memory_tool.py`，而应同时观察：

- prompt builder
- session store
- context compressor
- gateway lifecycle
- Honcho integration
- skills system

否则很容易低估 Hermes 对“连续性”的工程投入。

---

## 14. 本机 OpenClaw + Graph Memory 对比研究

本节基于当前机器上的实际安装进行对比：

- OpenClaw 根目录：`/Users/wzh/.openclaw`
- Graph Memory 插件：`/Users/wzh/.openclaw/extensions/graph-memory`
- OpenClaw 主工作区：`/Users/wzh/.openclaw/workspace`
- OpenClaw 当前配置：`/Users/wzh/.openclaw/openclaw.json`

当前可以确认：

1. OpenClaw 已启用 `graph-memory` 作为 `contextEngine`
2. OpenClaw 同时保留文件型记忆：
   - `SOUL.md`
   - `USER.md`
   - `MEMORY.md`
   - `memory/YYYY-MM-DD.md`
3. OpenClaw 的 graph-memory 不是辅助工具，而是**接管上下文装配的插件**

### 14.1 两套系统的核心差异先给结论

如果高度压缩地总结：

- **Hermes** 更像“稳定系统提示词 + 小容量长期记忆 + 按需搜索历史”的架构
- **OpenClaw + graph-memory** 更像“工作区文件体系 + 每轮自动提取知识图谱 + 动态 recall pool 注入”的架构

换句话说：

- Hermes 优先解决的是 **prompt 稳定性、cache 命中率、上下文可控性**
- OpenClaw 优先解决的是 **跨轮自动沉淀知识、图谱化召回、会话内持续演化 recall**

---

## 15. 记忆系统差异

### 15.1 Hermes：双文件 curated memory + session_search + 可选 Honcho

Hermes 的本地记忆核心是：

- `MEMORY.md`
- `USER.md`

特点：

1. 小容量、强约束
2. agent 主动调用 `memory` 工具维护
3. session 启动时冻结成 snapshot 注入 system prompt
4. 中途写盘，但不刷新已缓存 prompt

配套的长期召回层是：

- `session_search`：搜索 SQLite transcript，再用辅助模型做 focused summary
- `Honcho`：跨 session 的语义用户建模层

本质上，Hermes 的“记忆”是：

- 小而强的事实缓存
- 大而慢的历史检索
- 可选的语义外脑

### 15.2 OpenClaw：工作区文件记忆 + graph-memory 图谱记忆

OpenClaw 这里存在两套并行记忆：

#### A. 工作区文件记忆

从 `workspace/MEMORY.md`、`workspace/USER.md`、`workspace/SOUL.md` 可以看到：

- `SOUL.md`：身份与人格
- `USER.md`：用户信息
- `MEMORY.md`：长期摘要，但当前规则明确要求“保持短、小、稳定”
- `memory/YYYY-MM-DD.md`：默认写入目标，承接日常日志与过程沉淀

这套设计强调：

1. `MEMORY.md` 不是默认写入口
2. 默认记录写入 daily logs
3. 显式长期记忆优先写 graph memory

这和 Hermes 有一个很大的区别：

- Hermes 把 `MEMORY.md` / `USER.md` 作为正式的持久事实层
- OpenClaw 则把 `MEMORY.md` 明确降级成“永久摘要页”，把大多数新增信息流向 daily notes 或 graph memory

#### B. Graph Memory 图谱记忆

OpenClaw 的 `graph-memory` 维护一个 SQLite typed knowledge graph：

- Nodes:
  - `TASK`
  - `SKILL`
  - `EVENT`
- Edges:
  - `USED_SKILL`
  - `SOLVED_BY`
  - `REQUIRES`
  - `PATCHES`
  - `CONFLICTS_WITH`

而且它不是只靠用户显式“记住这个”触发，而是有三条并发写入路径：

1. `runTurnExtract()`
   - 每轮调用 Extractor LLM，从对话中自动结构化抽取节点
2. `selfReflect()`
   - 每轮用较小模型做自反思，写 reflection 节点
3. `runExplicitMemory()`
   - 检测“记住这个”“以后都按这个来”等显式记忆信号

这意味着 OpenClaw 的 graph-memory 更像一个**自动知识抽取引擎**，而不是一个需要 agent 自己决定何时调用的写入工具。

### 15.3 写入哲学差异

两者最本质的记忆写入哲学区别如下：

#### Hermes：显式、保守、强筛选

- 让 agent 主动决定是否写 memory
- 容量非常小
- 不追求自动覆盖所有有价值内容
- 更像“高价值事实剪辑”

#### OpenClaw：自动、激进、图谱化

- 每轮都尝试抽取 durable knowledge
- 允许 auto/reflection/explicit 三路并发入库
- 不把“用户没说记住”当成不写的理由
- 更像“持续构建第二大脑图谱”

所以：

- Hermes 的风险更偏向“漏记”
- OpenClaw 的风险更偏向“误提取、漂移、重复、图谱污染”

OpenClaw graph-memory 自己也承认这个问题，所以才有：

- 向量去重
- PageRank
- 社区检测
- nightly maintenance

---

## 16. 上下文组装系统差异

### 16.1 Hermes：稳定 prompt 骨架优先

Hermes 的上下文装配主入口是 `_build_system_prompt()`，它强调：

1. 先构造稳定 system prompt
2. session 内尽量不变
3. continuing session 尽量复用已落库 prompt
4. 动态内容只在 API 调用时追加

也就是说，Hermes 的上下文组装中心思想是：

**先确定稳定骨架，再叠加当前轮临时上下文。**

### 16.2 OpenClaw + graph-memory：assemble 阶段动态拼接优先

OpenClaw 的 graph-memory 作为 `contextEngine`，在 `assemble()` 阶段执行：

1. 从消息中构建 session context layers
   - `recentMessages`
   - `visibleTranscript`
2. 读取本 session `activeNodes`
3. 从 recall pool 取当前 session 已累计召回节点
4. 用 `assembleContext()` 将 graph 转成：
   - `systemPrompt`
   - `xml`
5. 把三部分拼成 `systemPromptAddition`
   - graph system prompt
   - `<kg>` XML
   - 可见 transcript

然后返回：

- 修过的大尾部消息
- `systemPromptAddition`
- estimated token usage

这说明 OpenClaw 的图谱上下文是**每次 assemble 动态生成的**，而不是像 Hermes 那样先固定一个 cached system prompt。

### 16.3 Hermes 的 context files vs OpenClaw 的 workspace files

Hermes 的文件型上下文注入策略：

- `SOUL.md`
- `.hermes.md`
- `AGENTS.md`
- `CLAUDE.md`
- `.cursorrules`

重点是：

1. 有优先级选择
2. 有安全扫描
3. 有截断
4. 被视为“稳定层”

OpenClaw 的工作区文件更像 agent 的人工启动材料：

- `SOUL.md`
- `USER.md`
- `MEMORY.md`
- `AGENTS.md`
- `IDENTITY.md`
- daily memory files

从实际 session transcript 看，OpenClaw 常在新会话一开始主动读取这些文件和最近 memory logs，而不是统一经过 Hermes 那样的 stateless prompt builder 一次性装配。

因此二者差异是：

- Hermes：更像编译 system prompt
- OpenClaw：更像启动时按 SOP 主动读工作区文件，再由 contextEngine 动态补图谱层

### 16.4 Hermes 的动态层 vs OpenClaw 的 recall pool

Hermes 的动态上下文主要来自：

- `ephemeral_system_prompt`
- 插件 `pre_llm_call`
- `@file/@diff/@url` 引用展开
- Honcho turn context

OpenClaw graph-memory 的动态上下文主要来自：

- 每轮 `afterTurn` 后更新 recall pool
- `assemble()` 时把 recall pool 节点取出来
- visible transcript 压缩层

尤其 recall pool 是 OpenClaw 的一个关键差异点：

- 它不是 session start recall 一次就结束
- 而是每轮都基于新消息召回、更新池子、做 LRFU 保留

这和 Hermes 完全不同：

- Hermes 的 local memory snapshot 在 session 内基本不动
- OpenClaw 的 recall pool 在 session 内持续演化

---

## 17. 两套系统的流程原理对比

### 17.1 Hermes 流程

#### 启动阶段

1. 初始化 agent
2. 加载 `MEMORY.md` / `USER.md`
3. 可选初始化 Honcho
4. 构建 cached system prompt
5. 必要时加上首轮 Honcho context

#### 每轮阶段

1. 处理 `@` 引用
2. 把用户消息加入历史
3. 复用或创建 system prompt
4. preflight 压缩
5. 加入动态层
6. 调模型
7. 必要时工具写 memory / 搜索 session / 调 Honcho

#### 会话末端

1. 如果要 reset / expire
2. 触发 flush agent
3. 从 transcript 中提炼 memory / skills

Hermes 流程的关键词是：

- snapshot
- cache stability
- explicit writes
- delayed refresh

### 17.2 OpenClaw + graph-memory 流程

#### 启动阶段

1. `before_agent_start`
2. 根据 `agentId + workspaceDir` 解析该 agent 专属 graph DB
3. 用首条 prompt 做 recall seed
4. recall 结果进入 recall pool

#### 每轮阶段

1. `ingestMessage()` 写入原始消息
2. `afterTurn` 并发执行：
   - auto extract
   - self reflection
   - explicit memory
   - recallPool.recallTurn()
3. `assemble()` 时：
   - 取 recentMessages
   - 生成 visibleTranscript
   - 取 activeNodes
   - 取 recallPool nodes
   - 转成 graph XML + graph instruction
   - 拼成 `systemPromptAddition`

#### session 结束

1. `session_end`
2. 运行 maintenance
   - dedup
   - PageRank
   - 社区发现
   - 社区摘要

OpenClaw 流程的关键词是：

- every-turn extraction
- evolving recall pool
- graph assembly
- post-session consolidation

---

## 18. 哪些地方是“相同问题，不同解法”

### 18.1 对长期连续性的处理

Hermes 解法：

- 小记忆固定进 prompt
- 需要时 session_search
- 可选 Honcho 提供更深层 recall

OpenClaw 解法：

- 每轮持续抽取 durable knowledge
- 图谱召回 + recall pool
- contextEngine 动态注入

### 18.2 对上下文膨胀的处理

Hermes：

- 旧 tool output pruning
- 中间历史结构化摘要
- 稳定 system prompt + 动态增量层

OpenClaw：

- recentMessages + visibleTranscript 分层
- graphBudgetRatio 控制图谱 token 预算
- oversized tool results summarize
- 图谱只放选中的节点，不放全量 transcript

### 18.3 对“记忆污染”的处理

Hermes：

- 小容量上限天然防膨胀
- 人工/agent 主动筛选写入
- 安全扫描

OpenClaw：

- 依赖去重、PageRank、community、maintenance 对图谱做后治理
- 更偏“写得多，后面再清”

---

## 19. 研究判断：两者的优势与风险

### 19.1 Hermes 的优势与风险

优势：

1. prompt 结构稳定，便于缓存和调试
2. 记忆语义清晰，知道什么会稳定存在
3. 本地记忆实现简单、可靠、容易推断
4. 更不容易因为自动抽取失控而污染上下文

风险：

1. 高价值信息可能因为没触发 `memory` 而漏掉
2. 需要 agent 自己有较强的“记忆意识”
3. session_search 是按需检索，不是 always-on continuity

### 19.2 OpenClaw + graph-memory 的优势与风险

优势：

1. 自动提取强，用户不必总是说“记住这个”
2. 图谱结构适合表示任务、技能、事件及其关系
3. recall pool 让 session 内召回持续演化，不依赖首轮 query
4. 对“以前怎么解决过类似问题”的复用能力理论上更强

风险：

1. 系统复杂度更高，调试难度更大
2. 自动抽取和 self-reflection 可能引入噪声
3. 图谱治理要依赖维护任务，否则会漂移
4. 每轮动态 assemble 的可预测性弱于 Hermes 的 frozen snapshot

---

## 20. 最终对比结论

如果把两者放在同一张设计坐标系里：

### Hermes 偏向

- 工程可控性
- prompt 可预测性
- 缓存友好
- 明确分层
- 小而精的 durable memory

### OpenClaw + graph-memory 偏向

- 自动知识沉淀
- 图谱关系建模
- 每轮持续 recall
- 更强的“第二大脑”倾向
- 用动态 context engine 驱动连续性

因此可以给出一个研究上的判断：

**Hermes 更像“稳定上下文操作系统”；OpenClaw + graph-memory 更像“持续生长的知识图谱记忆系统”。**

两者都在解决 agent continuity，但路线不同：

- Hermes：先稳住 prompt，再补 recall
- OpenClaw：先让记忆持续生长，再动态装配上下文

这也解释了为什么两者在“记忆”和“上下文组装”上看起来都很强，但气质完全不一样。
