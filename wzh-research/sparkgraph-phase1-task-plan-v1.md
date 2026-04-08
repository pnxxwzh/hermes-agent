# SparkGraph Phase 1 任务计划（Task Plan v1）

> 目标：把 SparkGraph 的正式设计转成第一阶段可执行实施计划。  
> 范围：只覆盖 **Phase 1：最小可运行内核**。  
> 原则：  
> 1. 小步提交  
> 2. 每步有明确文件边界  
> 3. 每步有明确测试边界  
> 4. 不提前混入 Phase 2/3/4 的能力

## 1. Phase 1 目标

Phase 1 只做以下能力：

1. SparkGraph 最小 schema / store
2. 最小 config/runtime bootstrap
3. model capability preflight harness
4. qualified-safe candidate extraction bridge
5. optional lightweight classification bridge
6. semantic dedup
7. recall block 注入
8. 基础单测 / 集成测试
9. shadow mode / manual-only 最小落地

### Phase 1 明确不做

1. 完整 setup/reconfigure UI
2. doctor/status 全量展示
3. qualification gate 完整落地
4. shadow mode 完整落地
5. dream/background job
6. 重 maintenance
7. 完整工具表面

---

## 2. 执行策略

### 2.1 核心原则

1. 先做 `core layer`
2. 后做 `integration layer`
3. 每一步都先把测试补齐，再进入下一步

### 2.2 提交粒度原则

建议以 **8 个任务批次** 进入 Phase 1。  
每个任务批次都应可独立评审、可独立回滚。

---

## 3. 任务批次总览

| Batch | 名称 | 目标 |
|---|---|---|
| `P1-B0` | Model Preflight | 固化原版抽取式能力验证、prompt smoke、准入前置检查 |
| `P1-B1` | Core Skeleton | 建立 SparkGraph 目录、类型、配置、DB skeleton |
| `P1-B2` | Schema & Store | 完成 schema、CRUD、FTS、profile path |
| `P1-B3` | Scoring Core | 完成 scoring、confidence components、状态决策基础 |
| `P1-B4` | Extract + Classify | 完成 candidate extraction 的 qualified-safe / shadow-safe 桥接，并保留可选轻量分类复核 |
| `P1-B5` | Dedup | 完成 FTS + embedding dedup / merge 路径 |
| `P1-B6` | Recall Core | 完成 recaller + formatter |
| `P1-B7` | Hermes Integration | 完成 `run_agent.py` 动态注入与 post-turn bridge |
| `P1-B8` | End-to-End Tests | 跑通最小集成链并补全 Phase 1 测试 |

---

## 4. 批次详情

## `P1-B0` Model Preflight

### 目标

在实现正式 candidate pipeline 之前，先把原版抽取式能力验证、prompt smoke、准入前置检查固定下来。

### 主要文件

新增：

1. `tests/sparkgraph/evals/test_preflight_smoke.py`
2. `tests/sparkgraph/evals/fixtures/preflight/*`

补充：

3. `wzh-research/sparkgraph-model-preflight-and-prompt-spec-v1.md`

### 主要内容

1. 固定原版抽取式 preflight fixture
2. 固定原版抽取 prompt 与宽松 JSON 解析
3. 固定 preflight 通过/失败判定
4. 记录目标模型当前状态：
   - `unverified`
   - `qualified`
   - `restricted`

### 必测

至少覆盖：

1. TASK + SKILL
2. EVENT + SKILL
3. 纯寒暄空输出
4. PATCHES
5. 讨论/对比任务
6. JSON parse stability

### 依赖

- 无

### 风险

1. 若抽取式 preflight 长期失败，`P1-B4` 必须退化为 shadow/manual-safe 实现，不能假设正式 automatic extractor 可用

---

## `P1-B1` Core Skeleton

### 目标

把 SparkGraph 作为独立核心层建起来，但不接 Hermes 主线。

### 主要文件

新增：

1. `agent/graph_memory/__init__.py`
2. `agent/graph_memory/types.py`
3. `agent/graph_memory/config.py`
4. `agent/graph_memory/db.py`
5. `agent/graph_memory/runtime.py`

### 主要内容

1. 定义枚举：
   - `NodeType`
   - `EdgeType`
   - `NodeStatus`
2. 定义 dataclass / typed structures
3. 定义 `graph_memory` 配置解析
4. 定义默认 db path 解析逻辑
5. 定义 runtime config structures

### 必测

新增：

1. `tests/sparkgraph/test_types.py`
2. `tests/sparkgraph/test_config.py`

至少覆盖：

1. 枚举与默认值
2. profile 路径解析
3. `db_path=""` 自动解析
4. 非法配置报错

### 依赖

- `P1-B0`

### 风险

1. 当前代码库已有大量 `graph_memory` 历史命名，需避免和旧路径混淆

---

## `P1-B2` Schema & Store

### 目标

建立最小 DB 与 CRUD。

### 主要文件

实现/补充：

1. `agent/graph_memory/db.py`
2. `agent/graph_memory/store.py`

新增：

1. FTS 触发器
2. migration `1`

### 主要内容

1. 创建：
   - `_migrations`
   - `sg_nodes`
   - `sg_edges`
   - `sg_evidence`
   - `sg_vectors`
   - `sg_nodes_fts`
2. 实现基本 CRUD
3. 实现 path creation 与 profile scope

### 必测

新增：

1. `tests/sparkgraph/test_db.py`
2. `tests/sparkgraph/test_store.py`

至少覆盖：

1. schema 初始化
2. migration 幂等
3. 唯一索引
4. FTS trigger sync
5. vector row update

### 依赖

- `P1-B1`

### 风险

1. canonical_key 暂未冻结最终算法，初版先以占位流程落地

---

## `P1-B3` Scoring Core

### 目标

建立 confidence / scoring / state decision 基础。

### 主要文件

新增：

1. `agent/graph_memory/scoring.py`

### 主要内容

1. 计算：
   - `confidence`
   - `stability`
   - `reuse_score`
   - `support_score`
2. 生成 `confidence_components`
3. 实现 candidate / active / deprecated 的基础判定 helper

### 必测

新增：

1. `tests/sparkgraph/test_scoring.py`

至少覆盖：

1. `confidence_components` 持久化
2. `source_kind` 差异生效
3. support 增加时 confidence 上升
4. session-bound penalty 生效
5. candidate path 不会全筛空 durable 样本

### 依赖

- `P1-B1`
- `P1-B2`

### 风险

1. support_score 公式初版仍需保守实现

---

## `P1-B4` Extract + Classify

### 目标

实现从最近窗口中提取 candidate，并做原版风格 parse / validate；结构化分类只保留为可选轻量复核。

### 主要文件

新增：

1. `agent/graph_memory/extractor.py`
2. `agent/graph_memory/classifier.py`

可能桥接：

3. `agent/auxiliary_client.py`

### 主要内容

1. extraction runtime 调用
2. 原版风格 parse / validate
3. optional classification runtime 调用
4. timeout / parse failure 处理
5. reflection 路径的 candidate-only 限制
6. `qualified` / `unverified` / `restricted` 状态分支
7. `manual/explicit-only mode` 下跳过自动 extraction/classification

### 必测

新增：

1. `tests/sparkgraph/test_extractor.py`
2. `tests/sparkgraph/test_classifier.py`

至少覆盖：

1. 结构化输出解析
2. malformed JSON
3. timeout fallback
4. 原版风格节点/边字段校验
5. 可选轻量分类结果解析
6. `unverified` 模型不会进入正式主链
7. `shadow mode` 不会驱动 active promotion
8. `manual/explicit-only mode` 跳过自动路径但不中断系统

### 依赖

- `P1-B1`
- `P1-B3`

### 风险

1. 小模型能力依赖较强，当前阶段先用 mock/fake provider 做 deterministic tests
2. 不能把当前 `Qwen3.5-0.8B-MLX-bf16` 视为默认合格前提
3. 第一版实现不应默认依赖前置分类题路线

---

## `P1-B5` Dedup

### 目标

实现最小 dedup / merge 路径。

### 主要文件

新增：

1. `agent/graph_memory/dedup.py`

### 主要内容

1. FTS top-k 初筛
2. embedding top-k 近邻
3. merge candidate ranking
4. `merge_required` / `create_candidate` 判定

### 必测

新增：

1. `tests/sparkgraph/test_dedup.py`

至少覆盖：

1. 强近似 merge
2. 弱近似保守不 merge
3. 低相似新建 candidate
4. embedding 不可用时退回 FTS

### 依赖

- `P1-B2`
- `P1-B3`
- `P1-B4`

### 风险

1. embedding 相似度阈值初期可能需要根据假数据/fixture 调整

---

## `P1-B6` Recall Core

### 目标

实现最小 recall 主链。

### 主要文件

新增：

1. `agent/graph_memory/recaller.py`
2. `agent/graph_memory/formatter.py`

### 主要内容

1. vector/FTS query
2. one-hop relation expansion
3. active-only filtering
4. rank
5. budget trim
6. short recall block formatting

### 必测

新增：

1. `tests/sparkgraph/test_recaller.py`
2. `tests/sparkgraph/test_formatter.py`

至少覆盖：

1. candidate 不进入 recall
2. deprecated 不进入 recall
3. max_nodes 生效
4. type caps 生效
5. budget 生效
6. recall timeout / embedding fallback

### 依赖

- `P1-B2`
- `P1-B3`
- `P1-B5`

### 风险

1. recall 排序最终质量仍需依赖 Phase 3 评测验证

---

## `P1-B7` Hermes Integration

### 目标

把 SparkGraph 最小内核接进 Hermes 主链。

### 主要文件

修改：

1. `run_agent.py`
2. `hermes_cli/config.py`
3. `hermes_cli/runtime_provider.py`

新增：

1. `agent/graph_memory/manager.py`

### 主要内容

1. agent init bridge
2. pre-LLM recall block injection
3. post-turn candidate pipeline bridge
4. 最小 runtime resolution

### 必测

新增：

1. `tests/test_run_agent_sparkgraph.py`

至少覆盖：

1. recall block 注入成功
2. cached prompt 不被污染
3. SparkGraph 失败不阻断主聊天
4. runtime 未就绪时 degrade 正常

### 依赖

- `P1-B1` ~ `P1-B6`

### 风险

1. `run_agent.py` 接入点要严格保持薄桥接

---

## `P1-B8` End-to-End Tests

### 目标

把 Phase 1 最小链路闭环跑通，并确保测试覆盖达到可开下一阶段的程度。

### 主要文件

新增：

1. `tests/sparkgraph/evals/test_eval_schema.py`
2. `tests/sparkgraph/evals/test_eval_smoke.py`
3. `tests/sparkgraph/evals/test_system_quality_eval.py`

补充：

4. `tests/test_run_agent_sparkgraph.py`

### 主要内容

1. end-to-end durable fixture 通过候选链
2. confidence 不塌缩
3. explicit/manual 样本保留
4. recall 对 durable fixture 集不应长期为空
5. `unverified` 模型下系统自动退到 shadow/manual-safe
6. `qualified` 与 `shadow` 模式结果差异可观测

### 依赖

- `P1-B7`

### 风险

1. 若这一步暴露出阈值问题，应回到 `decision-parameters-spec` 调整，而不是硬 patch 测试

---

## 5. 批次依赖图

```text
P1-B0
  -> P1-B1
P1-B1
  -> P1-B2
  -> P1-B3
  -> P1-B4
P1-B2 -> P1-B5
P1-B3 -> P1-B5
P1-B4 -> P1-B5
P1-B2/P1-B3/P1-B5 -> P1-B6
P1-B1..P1-B6 -> P1-B7
P1-B7 -> P1-B8
```

---

## 6. Phase 1 完成标准

Phase 1 完成时，至少要满足：

1. SparkGraph DB 能初始化
2. candidate 能在 qualified-safe 路径下被提取和分类
3. dedup 能运行
4. recall block 能注入当前轮动态层
5. cached prompt 保持不变
6. SparkGraph 失败不阻断主聊天
7. 有基础 end-to-end tests
8. confidence 生成可解释且不塌缩
9. durable fixture 集不会系统性全筛空
10. `unverified` 模型不会被错误接入正式主链
11. 系统至少支持 `shadow mode` 或 `manual/explicit-only mode`

---

## 7. Phase 1 暂不验收的内容

以下即使未完成，也不阻断 Phase 1 结束：

1. 完整 setup 向导
2. 完整 doctor/status 页面
3. full qualification gate
4. full multilingual fixture set
5. shadow mode 完整上线
6. dream/background jobs

这些属于 Phase 2/3/4。

---

## 8. 推荐执行顺序

建议按批次顺序严格推进，不建议跳步。

### `P1-B0`：Model Capability Preflight

在真正进入 `P1-B1` 之前，必须先完成这个前置验证批次。

目标：

1. 验证目标小模型是否适合承担最小结构化分类任务
2. 先发现 prompt 设计是否过重
3. 避免把 classifier 实现建立在一个实际上不稳定的模型假设上

建议最少覆盖：

1. 中文 durable fact
2. 中文 ephemeral state
3. 英文 preference
4. 日文 durable issue
5. 混合语言 / task-state

验证维度：

1. JSON 是否稳定
2. `is_durable` 是否明显跑偏
3. `needs_current_session` 是否明显跑偏
4. `knowledge_type` 是否出现系统性误判

### 当前已知结果

我们已经对 `http://127.0.0.1:8000` 上的 `Qwen3.5-0.8B-MLX-bf16` 做了两轮手工 smoke test：

1. **重 prompt 版本**
   - 中文 durable fact：基本可用
   - 中文临时会话状态：基本可用
   - 英文 preference：可用
   - 混合 task state：可用
   - 日文 durable issue：误判为非 durable
2. **轻 prompt 版本**
   - JSON 任务更简单
   - 但中文 durable fact、英文 preference 出现明显误判
   - 日文 durable issue 反而改判为 durable，但类型误成 `PREFERENCE`

### 当前结论

这说明：

1. `Qwen3.5-0.8B-MLX-bf16` 目前不应被直接视为“稳定合格 classifier”
2. classifier prompt 还需要针对小模型继续优化
3. SparkGraph 的模型能力测试必须前置，而不是实现后再补

### 对 Phase 1 的影响

若 preflight 仍明显失败，则：

1. 先继续迭代 classifier prompt
2. 先落地测试 harness
3. 暂缓把完整 classifier 绑定到正式 SparkGraph 主链

### 推荐节奏

1. 先做 `P1-B0` Model Capability Preflight
   - 验证目标小模型与最小 classifier prompt 是否可行
2. 再完成 `P1-B1 ~ P1-B3`
   - 把地基和 scoring 立起来
3. 再完成 `P1-B4 ~ P1-B6`
   - 形成 SparkGraph 自身最小闭环
4. 最后做 `P1-B7 ~ P1-B8`
   - 接 Hermes 主线并做端到端验证

---

## 9. 当前建议

如果要正式开工，建议从 `P1-B1` 开始，不要先碰 `run_agent.py`。

原因：

1. 先搭 core layer，能最大化解耦
2. 先做 schema/store/scoring，能提前暴露设计问题
3. 先把主线文件改动留到后面，能降低返工成本
