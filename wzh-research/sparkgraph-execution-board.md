# SparkGraph 执行总控板（v2）

> 这份文档是当前阶段的 **唯一实施跟踪面**。  
> 后续只要涉及 SparkGraph 的开发、测试、评测、配置、运行时、迁移边界，都必须回到这份文档登记。  
> 目标：避免遗漏、避免越做越多、避免不同文档之间出现冲突。

## 1. 单一真相源

从现在开始，SparkGraph 项目按下面的文档分工执行：

1. **执行总控板**
   - `wzh-research/sparkgraph-execution-board.md`
   - 用途：范围冻结、实施状态、优先级、依赖、验收跟踪
2. **正式软件设计说明书**
   - `wzh-research/sparkgraph-sds-v2.md`
   - 用途：正式架构、模块、schema、运行时、测试、评测、失败模式、实施原则
3. **评测设计**
   - `wzh-research/sparkgraph-evaluation-design-v1.md`
   - 用途：软件测试、模型评测与历史研究参考
4. **配置设计**
   - `wzh-research/sparkgraph-config-setup-design.md`
   - 用途：setup / reconfigure / restore / migration / probe / health
5. **feature/test 追踪矩阵**
   - `wzh-research/sparkgraph-feature-test-matrix-v2.md`
   - 用途：当前功能与测试追踪底稿
6. **Phase 1 实施计划**
   - `wzh-research/sparkgraph-phase1-task-plan-v2.md`
   - 用途：flush-only 主线下的第一阶段任务拆分
7. **实施检查清单**
   - `wzh-research/sparkgraph-implementation-checklist-v2.md`
   - 用途：编码阶段逐项核对已完成项、剩余项与禁止事项
8. **调测与验收手册**
   - `wzh-research/sparkgraph-test-manual-v1.md`
   - 用途：从安装、配置到功能调测与回归的完整执行手册

### 当前原则

1. **实施以本执行总控板为准**
2. **正式架构与实施边界以 SDS 为准**
3. **功能边界以 SparkGraph 设计为准**
4. **测试/评测边界以评测设计为准**
5. **配置生命周期以配置设计为准**
6. 旧的重型 `graph-memory` 方案只保留参考价值，不再作为实施目标
7. `sparkgraph-sds-v1.md` 及其中的 `review+flush` 主线视为历史方案，当前以 `flush-only` 的 v2 设计为准
8. `sparkgraph-feature-test-matrix.md` 与 `sparkgraph-phase1-task-plan-v1.md` 视为历史方案，不再代表当前测试覆盖面

---

## 2. 冻结范围

## 2.1 项目名称

- **SparkGraph**

## 2.2 项目定位

SparkGraph 不是第二套记忆操作系统，而是：

- **Hermes 内部的 Durable Knowledge Graph Backend**

## 2.3 只做两件事

1. 记录 **具体且有价值的 durable knowledge**
2. 在需要时 **准确召回相关知识点及其关系邻域**

## 2.4 明确不做

1. 不做 OpenClaw 式 context engine
2. 不做 visible transcript / assemble / compact 主链
3. 不做 Hermes skill 的替代系统
4. 不做“大而全”的第二套长期记忆操作系统
5. 不做依赖固定词组的多语言脆弱硬编码
6. 不自行发明单 profile 内多长期 agent namespace
7. 不重写 Hermes 的 cached system prompt 主骨架
8. 不直接改写 Hermes `MEMORY.md` / `USER.md` / skills

---

## 3. 与 Hermes 的职责边界

## 3.1 Hermes 继续负责

1. `SOUL.md`
2. `MEMORY.md`
3. `USER.md`
4. skills
5. `session_search`
6. cached system prompt
7. compression / flush / background review 主链
8. 最终制度化写入

## 3.2 SparkGraph 负责

1. flush-integrated durable knowledge extraction
2. flush-integrated extraction / finalize
3. semantic dedup / merge
4. graph persistence
5. graph recall
6. lightweight maintenance

## 3.3 关键边界规则

1. **Skill 不进入 SparkGraph**
2. **Recall block 只进入动态层**
3. **SparkGraph 不直接承担 curated memory 写入权**
4. **主自动提取路径以 flush-integrated extraction 为准**

---

## 4. Namespace / 存储硬约束

SparkGraph 必须严格跟随 Hermes 官方组织方式：

1. 以当前 `HERMES_HOME` 为根
2. 以当前 **profile** 为 durable graph 存储边界
3. 默认 graph DB 路径位于当前 profile 内
4. setup / restore / migration / health / doctor 都按当前 profile 作用域工作
5. 不新增“agent namespace”一等抽象

### 默认路径原则

1. default profile：
   - `~/.hermes/sparkgraph/default.db`
2. named profile：
   - `~/.hermes/profiles/<name>/sparkgraph/default.db`

### 允许但不推荐

1. 用户自定义 `db_path` 指向 profile 外部
2. 系统必须允许，但要提示这偏离推荐组织方式

---

## 5. 数据模型冻结

## 5.1 节点类型

第一版只保留：

1. `FACT`
2. `PREFERENCE`
3. `ISSUE`
4. `RESOURCE`
5. `DECISION`

## 5.2 边类型

第一版只保留：

1. `RELATED_TO`
2. `DEPENDS_ON`
3. `CONFLICTS_WITH`
4. `DERIVED_FROM`
5. `APPLIES_TO`

## 5.3 节点状态

第一版只保留：

1. `candidate`
2. `active`
3. `deprecated`

### 状态原则

1. 默认不允许“抽到就 active”
2. `candidate` 是默认落点
3. `active` 需要升级条件
4. `deprecated` 用于被替代、低质量、过时内容

---

## 6. 决策流水线冻结

## 6.1 写入流水线

```text
recent turns
  -> candidate extraction
  -> semantic dedup / merge search
  -> score
  -> reject / candidate / active / merge / deprecate
```

## 6.2 Recall 流水线

```text
query
  -> vector/FTS retrieval
  -> one-hop relation expansion
  -> candidate filtering
  -> rank
  -> budget trim
  -> recall block
```

## 6.3 Reflection 规则

1. v2 主线不依赖 reflection
2. SparkGraph 主自动入口只有 `flush_memories()`
3. 不在主链中引入独立小模型抽取调用

---

## 7. 实现原则冻结

## 7.0 源码级整合原则

由于 SparkGraph 是对 Hermes **源码级别** 的改写整合，而不是纯外置插件，所以必须同时满足三条原则：

1. **尽量与原本代码解耦**
   - SparkGraph 的核心逻辑应尽可能收敛在独立模块中
   - 避免把业务逻辑散落进 `run_agent.py`、`gateway/run.py`、`setup.py` 等主线文件
2. **流程与注册点必须在源代码正式注册处接入**
   - 该挂在 prompt assembly 动态层的，就在正式动态注入点接
   - 该挂在 setup、runtime provider、health/status、flush/session_end 的，就在 Hermes 原生注册点接
   - 不允许通过“隐式旁路”或难以追踪的 monkey patch 方式接入
3. **尽可能保持未来主线更新的独立性**
   - 优先做“薄接入层 + 独立实现层”
   - 尽量不改 Hermes 主链的核心假设与主流程结构
   - 后续官方升级时，理想情况应主要处理少量接线层冲突，而不是大规模重写

### 结构化落实方式

所有 SparkGraph 改动应优先分成两层：

1. **Core layer**
   - `agent/sparkgraph/*`
   - 承载 DB、store、classifier、dedup、recall、maintenance 等核心逻辑
2. **Integration layer**
   - 少量 Hermes 主线改动点
   - 只负责：
     - 注册
     - 生命周期接线
     - 配置桥接
     - runtime 桥接
     - health/status 桥接

### 禁止事项

1. 不把 SparkGraph 核心逻辑直接内联到 `run_agent.py`
2. 不在多个主线文件里复制同一套判断逻辑
3. 不引入难以追踪的全局状态旁路
4. 不为了接入方便而改写 Hermes 已稳定的主链职责边界

## 7.1 不做词组硬编码

禁止把多语言 durable judgement 建立在：

1. 某个中文短语
2. 某个英文词组
3. 某种固定句式

### 允许的方式

1. 结构化或半结构化候选抽取
2. 基于 schema、evidence、去重、score、状态迁移的后处理
3. 在 flush 阶段通过 prompt 约束模型主动调用 `sparkgraph_record`

---

## 8. 当前实现状态

截至当前代码状态，以下主线能力已经落地：

1. `agent/sparkgraph/` 核心包
2. `sparkgraph` 顶层配置与 profile 作用域路径
3. `sg_nodes / sg_edges / sg_evidence / sg_vectors / sg_nodes_fts`
4. DB 级 `CHECK` 约束，限制 `type / status / source_kind`
5. store CRUD、FTS 同步、evidence append
6. dedup / scoring / candidate-active-deprecated 状态迁移
7. `sparkgraph_record` 工具
8. flush-only 提取接线
9. recall 检索、格式化、动态注入
10. flush 对齐轻量 maintenance
11. runtime snapshot / health 基础能力
12. 对话级 `flush -> graph write -> recall` 集成测试

### 已明确修掉的真实问题

1. FTS 查询原先可被原始用户输入打崩；现在已加清洗和失败回退
2. DB 原先只靠 Python enum；现在已加 SQLite `CHECK` 约束
3. recall 原先没有完全尊重配置；现在已按 `recall.enabled/max_items/max_related/max_chars` 生效
4. runtime status 原先会同步探网阻塞；现在 status 路径默认只做本地配置汇总

### 当前仍未完成的事项

1. `setup` / `reconfigure` / `restore` 的完整产品面接入
2. `doctor` / `status` / UI 展示的完整打磨
3. 主模型 flush 抽取评测 harness 未实现完整 provider 驱动版本
4. 更大范围的全仓回归尚未完成，当前以 SparkGraph 聚焦回归为主
5. embedding 缺失或降级为 FTS-only 时，改写问法的 recall 质量仍然有限，当前不应宣称已具备稳定语义召回
6. 当前环境下不建议使用 `uv pip install -e \".[all,dev]\"` 进行 SparkGraph 调测，因为 `matrix-nio[e2e] -> python-olm` 可能因 CMake 兼容性失败；调测手册已改用不含 `matrix` 的推荐 extras
7. `scripts/sparkgraph_flush_eval.py` 目前对本地 OpenAI 兼容服务的 provider 语义仍依赖 Hermes runtime 解析规则；当前已兼容 `openai-compatible -> custom`，但 `openai` 仍不是有效 Hermes provider 名

### 已完成：embedding setup 产品面优化

已完成项：

1. 增加 embedding vendor 选项
2. 引导顺序调整为：
   - provider/vendor
   - URL
   - model
   - API key
3. 配置完成后立即验证连通性与可用性
4. 用更清晰的成功/失败状态展示
5. 失败时明确提供：
   - 重新配置
   - 恢复默认

补充说明：

1. `sparkgraph_search` / `sparkgraph_stats` 已实现最小工具本体、单测以及 flush 写入后的集成覆盖
2. `sparkgraph_search` 是显式查询工具，可返回 `candidate` 节点；这不等于动态 recall 主链只召回 `active` 节点
3. 主模型 flush 抽取评测 harness 的基础 pytest 版本已实现
4. 已新增可手跑脚本：`scripts/sparkgraph_flush_eval.py`
5. 它当前覆盖的是：fixture、真实 flush prompt 拼装、`sparkgraph_record` tool-call 解析、样本打分
6. 它当前还不是“连真实 provider 自动跑并打分并落盘报告”的完整产品态
7. 已完成两轮更大范围回归：
   - SparkGraph 聚焦回归：`83 passed`
   - Hermes 外围相关回归（config/setup/status/run_agent/tools）：`382 passed`
8. `hermes setup` 已新增 SparkGraph section，并接入：
   - section-specific 调用
   - full setup
   - returning-user 菜单分发
   - Keep current / Reconfigure / Restore defaults
9. `hermes status` 已新增 SparkGraph 基础展示：
   - mode
   - db path
   - recall limits
   - embedding runtime 配置态
   - `--deep` 时可启用显式 live probe
10. `hermes doctor` 已新增 SparkGraph 非探网诊断：
   - config parse
   - db parent / profile 目录
   - runtime degraded 状态
   - `--probe` 时可启用显式 live probe
11. 但 SparkGraph 产品面当前仍是“基础接入”：
   - live probe 目前只接到显式入口，不会自动落盘或形成历史报告
   - 还没有 restore/reconfigure 的完整产品面
   - `doctor/status` 默认仍是非探网本地诊断，不等于完整 provider 级健康检查

### 本轮新增暴露的真实边界

1. 默认 profile 下 `sparkgraph/` 目录不存在时，`doctor` 现在只警告不报待修复问题；这是刻意对齐“首次使用时自动创建”的设计，不代表 DB 已实际初始化
2. `status` / `doctor` 当前只展示 SparkGraph runtime 配置态，不会主动验证外部 provider 真可用；真正的 provider 级验证仍需后续显式 probe/doctor 产品面
3. `hermes setup sparkgraph` 现在会对 profile 外部 `db_path` 给出 warning，但这仍然只是交互提示，不是强限制或迁移保护
4. `restore defaults` 当前是 SparkGraph 整块恢复默认，不支持更细粒度的子项恢复
5. live probe 目前仅覆盖运行时可达性，不包含模型能力评测；后者仍依赖 flush eval

### 当前不计划进入主线的内容

1. `review-integrated extraction`
2. 小模型前置分类主链
3. dream / 重社区分析 / 全图重写
4. skill 图谱化
5. 单 profile 内多 agent namespace

1. 结构化语义分类
2. embedding / FTS 检索
3. 状态/分数/证据规则
4. 可解释的阈值系统

## 7.2 规则/模型比例原则

推荐目标：

1. 规则与可测试逻辑：主导
2. 主模型：负责 flush 阶段知识提取与工具调用
3. Hermes：负责最终制度化写入

---

## 8. 数据库冻结方向

第一版建议最小化主表：

1. `sg_nodes`
2. `sg_edges`
3. `sg_evidence`
4. 可选 `sg_vectors`

### 当前明确不承诺第一版实现

1. community 表
2. 重型全图分析
3. OpenClaw 旧版那种大量辅助表

---

## 9. 配置与运行时冻结

## 9.1 作为核心配置块存在

SparkGraph 要像主模型 / TTS / vision 一样，进入正式配置生命周期。

### 配置块

顶层保留：

- `graph_memory`

其中至少包括：

1. `db_path`
2. `required`
3. `probe_on_startup`
4. `recall_max_nodes`
5. `recall_max_depth`
6. `graph_budget_ratio`
7. `auto_extract_enabled`
8. `explicit_enabled`
9. `reflection_enabled`
10. `maintenance_enabled`
11. `reflection.*`
12. `extraction.*`
13. `maintenance.*`
14. `embedding.*`

## 9.2 setup 生命周期

必须覆盖：

1. 初次 setup
2. rerun setup
3. reconfigure
4. restore defaults
5. migration/backfill
6. CLI bridge
7. gateway bridge
8. probe
9. doctor / health / status

## 9.3 runtime 正式化

embedding 模型必须像主模型一样进入正式运行时体系：

1. provider resolution
2. runtime health
3. structured errors
4. timeout
5. degraded mode
6. startup probe

---

## 10. embedding 运行时要求

当前主线只要求 embedding runtime：

1. 可在 setup 中配置
2. 可在 status/doctor 中显示与 probe
3. 不可用时自动降级到 FTS5-only recall

---

## 11. 测试与评测冻结

## 11.1 必须同时有两套保障

1. **软件测试**
2. **模型评测**

缺一不可。

## 11.2 软件测试层

必须覆盖：

1. unit tests
2. integration tests
3. regression tests
4. failure / timeout tests

## 11.3 模型评测层

必须覆盖：

1. main-model flush eval
2. end-to-end system-quality eval

## 11.4 多语言 baseline eval

第一版目标：至少 100 条样本。

### 语言分布

1. 中文：25
2. 英文：25
3. 日文：10
4. 韩文：10
5. 法文：10
6. 德文：10
7. 混合语言/代码切换：10

### 样本类型

每种语言必须覆盖：

1. durable fact
2. user preference
3. reusable issue pattern
4. stable resource reference
5. lasting decision
6. ephemeral session summary
7. one-off task progress
8. noisy chatter
9. borderline ambiguous case
10. multilingual mixed-with-code case

## 11.5 初版合格线

1. durable precision >= 0.90
2. ephemeral rejection precision >= 0.92
3. type accuracy >= 0.85
4. session-bound accuracy >= 0.90
5. parse failure rate <= 1%
6. timeout rate <= 3%

### 单语言下限

每种语言：

1. durable precision >= 0.80
2. ephemeral rejection precision >= 0.85

## 11.6 系统运行质量评测

模型能力测试不能只看 classifier 本身的理解正确率，还必须反映最终系统运行质量。

因此，SparkGraph 的模型准入必须额外覆盖：

1. candidate acceptance rate
2. active promotion preservation
3. confidence spread
4. all-filtered-out rate
5. recall usefulness rate
6. session-bound suppression quality

### 关键规则

如果模型单独测起来“懂语义”，但接入 SparkGraph 后出现以下任一问题，则不能视为 `qualified`：

1. durable 样本大面积被 reject
2. confidence 分布塌缩
3. recall 长期为空
4. explicit/manual 样本被误挡
5. session-bound 抑制逻辑误伤大量正常 durable 样本

---

## 12. 影子模式冻结

Shadow mode 是必须实现的。

### 目标

1. 跑完整候选提取与分类
2. 记录结果
3. 不写 active
4. 不影响正式 recall

### 补充要求

1. embedding 不可用时必须自动落到 `FTS5-only mode`
2. 不允许出现“embedding 不可用但系统仍悄悄假装语义召回可用”的状态

---

## 13. Feature / 测试追踪约束

现阶段继续沿用 `sparkgraph-feature-test-matrix.md` 里的 `Feature ID` 体系。

### 硬规则

1. 没有 `Feature ID` 的需求，不进入开发
2. 每个 PR 必须声明涉及哪些 `Feature ID`
3. 每个 `Feature ID` 必须绑定：
   - 单测
   - 集成测试或明确豁免理由
   - 必要的失败/超时测试
4. 路径与命名空间相关改动必须引用 `GM-CFG-005`
5. 模型评测/准入相关改动必须引用：
   - `GM-EVAL-002`
   - `GM-EVAL-003`
   - `GM-EVAL-004`
6. 所有 Hermes 主线接入点改动，必须额外说明：
   - 为什么必须改主线
   - 是否存在更低耦合替代方案
   - 核心逻辑是否已留在独立模块中

---

## 14. 当前确认的功能清单

下面这份清单代表 **当前已确认要做** 的内容。

### 核心功能

1. Graph DB schema / migration
2. Graph store CRUD
3. durable candidate extraction
4. explicit memory candidate extraction
5. reflection candidate path
6. semantic recaller
7. recall block formatting
8. 动态 graph recall 注入
9. graph tools
10. finalize / lightweight maintenance

### 运行时与配置

11. graph runtime provider resolution
12. runtime health visibility
13. structured graph runtime errors
14. degraded-mode fallback
15. `graph_memory` 顶层配置块
16. setup / reconfigure / restore / migration
17. CLI / gateway config bridge
18. Hermes profile 路径兼容

### 评测

19. main-model flush eval fixtures
20. flush eval result persistence
21. FTS5-only degraded recall verification

### 后台机制

23. internal dream/background job runner

---

## 15. 当前明确搁置的内容

下面这些不是当前阶段目标：

1. `TASK/SKILL/EVENT` 旧三分法整体迁移
2. skill 图谱
3. OpenClaw context-engine 整体迁移
4. visible transcript 主链
5. compact / assemble 主导权
6. 单 profile 内新 agent namespace
7. 重型 community-first recall
8. 每轮大规模反思式图谱写入

---

## 16. 当前实施阶段划分

## Phase 0：控制面冻结

目标：

1. 冻结范围
2. 冻结设计边界
3. 冻结测试/评测要求

状态：

- **已完成**

## Phase 1：最小可运行内核

目标：

1. SparkGraph DB schema
2. config/runtime resolution
3. flush-integrated candidate extraction
4. semantic dedup
5. recall block 注入
6. 基础单测/集成测试
7. flush-aligned lightweight maintenance

状态：

- **已完成**
- 已落地：
  1. DB/schema/store
  2. flush-integrated extraction bridge
  3. dedup/scoring
  4. recall block 注入
  5. flush-aligned lightweight maintenance
  6. 对话级与集成测试主链

## Phase 2：配置与正式运行时

目标：

1. setup 接入
2. restore/migration
3. health/status/doctor
4. structured errors
5. degraded mode

状态：

- **大部分已完成**
- 已落地：
  1. setup section
  2. keep/reconfigure/restore defaults
  3. status 基础展示
  4. doctor 基础检查
  5. `status --deep` / `doctor --probe` 显式 live probe
  6. `status/doctor` 可显示最近一次 SparkGraph flush eval 本地结果
- 剩余：
  1. 更细粒度 restore/reconfigure
  2. 更完整的 provider 级产品文案与修复建议

## Phase 3：评测与准入

目标：

1. flush eval fixtures
2. main-model eval
3. degraded recall verification

状态：

-- **部分完成**
- 已落地：
  1. main-model flush eval pytest harness
  3. `scripts/sparkgraph_flush_eval.py` 手动评测脚本
  4. flush eval 默认读取当前 Hermes `model.default` 与 runtime provider
  5. flush eval 结果可持久化到当前 profile 的 `sparkgraph/evals/flush-last.json`
  6. flush eval 支持 `--compare-last` 与上次结果做最小摘要对比
- 剩余：
  1. 更宽的 recall 质量门禁

## Phase 4：维护与后台治理

目标：

1. finalize
2. lightweight maintenance
3. dream internalization

状态：

- **部分完成**
- 已落地：
  1. flush-aligned lightweight maintenance
- 剩余：
  1. finalize 强化
  2. dream internalization

---

## 17. 执行状态板

| Area | Current Status | Notes |
|---|---|---|
| 范围收缩 | `frozen` | 已从重型旧方案收缩到 SparkGraph |
| 架构定位 | `frozen` | Durable Knowledge Graph Backend |
| 数据模型 | `frozen-v1` | FACT/PREFERENCE/ISSUE/RESOURCE/DECISION |
| 节点状态流 | `frozen-v1` | candidate/active/deprecated |
| namespace/storage | `frozen` | 严格跟随 Hermes `HERMES_HOME/profile` |
| 源码级整合原则 | `frozen` | 薄接入层 + 独立核心层 |
| 配置生命周期 | `designed` | 已有专项设计文档 |
| 运行时/错误/health | `implemented-partial` | setup/status/doctor/live probe 已落地；SparkGraph 配置校验已收严，文案与更深产品化仍待补 |
| 软件测试 | `implemented-partial` | SparkGraph 主链、宽回归与产品面相关回归已落地；若要阶段性封板，仍可再做一轮更大全仓筛选 |
| 模型评测 | `implemented` | main-model flush eval 已落地，支持当前结果持久化、上次对比与失败退出码 |
| 小模型准入 | `removed` | 非主线，已删除 shadow extractor 与相关脚本 |
| shadow mode | `removed` | 非主线，已删除 |
| Phase 1 编码 | `completed-core` | 当前进入产品面与评测完善阶段 |

---

## 18. 文档完整性规则

为了避免后续遗漏，从现在开始：

1. 新增设计决策，必须同步更新本执行总控板
2. 新增 feature，必须同步补 `Feature ID`
3. 新增测试要求，必须同步更新评测设计或追踪矩阵
4. 若某项被砍掉，必须在本执行总控板中显式移到“搁置/删除”
5. 每轮推进结束后，优先更新本板，而不是散落在聊天记录里

---

## 19. 当前结论

以今天为准，SparkGraph 的当前完整共识是：

1. **做轻，不做重**
2. **做知识点，不做 skill**
3. **做准确召回，不做重型上下文引擎**
4. **做 profile 级兼容，不自创 agent namespace**
5. **主自动提取路径是 flush-integrated main-model extraction**
6. **做正式运行时，不做外挂脚本**
7. **做软件测试 + 模型评测双保险**
8. **主链不依赖任何小模型旁路**

后续实现、评测、任务拆分，都应以这 8 条为总纲。

---

## 20. 最近回归记录

### 2026-04-02 相关区域宽回归

已通过：

1. CLI / 配置 / status / doctor / setup 相关
   - `86 passed`
2. 主链 / flush / tools / SparkGraph 集成相关
   - `332 passed`

本轮结论：

1. SparkGraph 当前实现没有在已触达的 CLI、主链、工具、集成面上暴露新回归
2. 这轮属于 **相关区域宽回归**，不是全仓所有测试的最终验收
3. 当前阶段质量信号较强，但如需阶段性封板，仍可再做一轮更大全仓筛选

### 2026-04-02 SparkGraph / 产品面聚焦回归

已通过：

1. `tests/sparkgraph`
2. `tests/integration/test_sparkgraph_flush_flow.py`
3. `tests/hermes_cli/test_sparkgraph_setup.py`
4. `tests/hermes_cli/test_sparkgraph_runtime_status.py`
5. `tests/hermes_cli/test_status.py`
6. `tests/hermes_cli/test_doctor.py`
   - 合计 `102 passed`

本轮结论：

1. SparkGraph 核心包、flush 对话流、以及 `setup/status/doctor` 产品面在当前实现上是一致的
2. 本轮仍存在测试环境 warning：`tests/conftest.py` 中事件循环获取方式会触发 `DeprecationWarning`
3. 该 warning 已登记到实施清单中，但不阻塞当前 SparkGraph 主线验收
