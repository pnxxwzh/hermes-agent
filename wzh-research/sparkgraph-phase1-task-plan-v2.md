# SparkGraph Phase 1 任务计划（v2）

> 适用设计：`wzh-research/sparkgraph-sds-v2.md`  
> 当前主线：**flush-only knowledge extraction**

## 1. Phase 1 目标

Phase 1 只做以下能力：

1. SparkGraph 最小 schema / store
2. 最小 config/runtime bootstrap
3. flush-integrated extraction bridge
4. `sparkgraph_record` 入库路径
5. semantic dedup
6. recall block 注入
7. embedding runtime / FTS fallback
8. flush-aligned lightweight maintenance
9. 基础单测 / 集成测试

### Phase 1 明确不做

1. background review 集成
2. 完整 setup/reconfigure UI 打磨
3. doctor/status 全量展示
4. dream/background job
5. 重 maintenance
6. skill / curated memory 升级联动

## 2. 执行策略

### 2.1 核心原则

1. 先做 `core layer`
2. 再做 `flush bridge`
3. 最后做 `recall bridge`
4. 每一步都先补测试，再进入下一步

### 2.2 v2 的关键约束

1. 不再实现 `review-integrated extraction`
2. 任何自动知识提取，都必须围绕 `flush_memories()` 展开
3. 任何 SparkGraph 错误都不能影响主回复
4. recall block 必须保持动态层、非持久化、非 cached prompt

## 3. 任务批次总览

| Batch | 名称 | 目标 |
|---|---|---|
| `P2-B0` | Mainline Refresh | 把 v2 文档、矩阵、计划统一到 flush-only 主线，后续按实现持续同步 |
| `P2-B1` | Core Skeleton | 建立 `agent/sparkgraph/` 核心包 |
| `P2-B2` | Schema & Store | 完成 schema、CRUD、evidence append、profile path |
| `P2-B3` | Scoring & Dedup | 完成 dedup、scoring、candidate/active 决策 |
| `P2-B4` | Flush Bridge | 完成 flush prompt 扩展、`sparkgraph_record`、flush-only 提取接线 |
| `P2-B5` | Recall Core | 完成 retrieval、formatter、dynamic recall injection |
| `P2-B6` | Runtime & Config | 完成 embedding runtime、config 和基础 status/health；setup/probe 仍待补齐 |
| `P2-B7` | Flush Maintenance | 完成 flush 对齐的轻量整理/遗忘 |
| `P2-B9` | End-to-End Tests | 跑通 flush-only 集成链并补全首批对话级测试 |

## 4. 批次详情

## `P2-B0` Mainline Refresh

### 目标

把当前文档主线全部切到 v2 的 flush-only 设计。

### 主要文件

1. `wzh-research/sparkgraph-sds-v2.md`
2. `wzh-research/sparkgraph-execution-board.md`
3. `wzh-research/sparkgraph-feature-test-matrix-v2.md`
4. `wzh-research/sparkgraph-phase1-task-plan-v2.md`

### 必测

1. 文档引用一致
2. 不再把 `review-integrated` 当作主线

### 状态

- 已完成，但后续文档同步仍需持续维护

## `P2-B1` Core Skeleton

### 目标

建立 SparkGraph v2 的独立核心包，不接 Hermes 主线。

### 主要文件

新增：

1. `agent/sparkgraph/__init__.py`
2. `agent/sparkgraph/types.py`
3. `agent/sparkgraph/config.py`
4. `agent/sparkgraph/db.py`
5. `agent/sparkgraph/runtime.py`
6. `agent/sparkgraph/manager.py`

### 主要内容

1. 定义 `NodeType` / `EdgeType` / `NodeStatus`
2. 定义 config dataclass
3. 定义 profile-scoped db path 解析
4. 定义 runtime health result 结构

### 必测

1. `tests/sparkgraph/test_types.py`
2. `tests/sparkgraph/test_config.py`
3. `tests/hermes_cli/test_sparkgraph_profile_paths.py`

### 依赖

- `P2-B0`

### 状态

- 已完成

## `P2-B2` Schema & Store

### 目标

建立最小 DB 和 CRUD。

### 主要文件

1. `agent/sparkgraph/db.py`
2. `agent/sparkgraph/store.py`

### 主要内容

1. 创建 `_migrations`
2. 创建 `sg_nodes / sg_edges / sg_evidence / sg_vectors / sg_nodes_fts`
3. 实现基本 CRUD
4. 实现 evidence append
5. 实现 profile-scoped path bootstrap

### 必测

1. `tests/sparkgraph/test_db.py`
2. `tests/sparkgraph/test_store.py`
3. `tests/integration/test_sparkgraph_flush_flow.py`

### 依赖

- `P2-B1`

### 状态

- 已完成

## `P2-B3` Scoring & Dedup

### 目标

建立 dedup、scoring 和状态迁移基础。

### 主要文件

1. `agent/sparkgraph/dedup.py`
2. `agent/sparkgraph/scoring.py`

### 主要内容

1. canonical key 生成
2. FTS + vector 近似查重
3. confidence / stability / reuse_score 计算
4. `candidate -> active`
5. `active -> deprecated`

### 必测

1. `tests/sparkgraph/test_dedup.py`
2. `tests/sparkgraph/test_scoring.py`
3. `tests/integration/test_sparkgraph_flush_flow.py`

### 依赖

- `P2-B2`

### 状态

- 已完成

## `P2-B4` Flush Bridge

### 目标

把 SparkGraph 正式接到 Hermes 的 `flush_memories()`。

### 主要文件

1. `run_agent.py`
2. `agent/sparkgraph/prompting.py`
3. `tools/sparkgraph_tool.py`
4. `agent/sparkgraph/manager.py`
5. `tools/__init__.py`

### 主要内容

1. 扩展 flush prompt
2. 注册 `sparkgraph_record`
3. flush 返回 tool call 时写 SparkGraph
4. flush 失败/坏输出时安全降级
5. 保证 flush 痕迹不污染消息历史

### 必测

1. `tests/sparkgraph/test_flush_prompting.py`
2. `tests/tools/test_sparkgraph_record_tool.py`
3. `tests/integration/test_sparkgraph_flush_flow.py`

### 核心对话级测试

1. `fact -> flush -> graph write`
2. `preference -> flush -> graph write`
3. `greeting -> flush -> no graph write`
4. `flush extraction failure -> user response unaffected`
5. `flush artifacts removed after call`
6. `query tools can inspect flush-written nodes`

### 依赖

- `P2-B2`
- `P2-B3`

### 状态

- 已完成
- 对话级覆盖现已包括 `fact/preference/greeting/query tools` 四条主链

## `P2-B5` Recall Core

### 目标

建立 SparkGraph recall 和动态注入。

### 主要文件

1. `agent/sparkgraph/recaller.py`
2. `agent/sparkgraph/formatter.py`
3. `run_agent.py`

### 主要内容

1. retrieval
2. one-hop expansion
3. active-only filtering
4. ranking
5. budget trim
6. dynamic recall block 注入

### 必测

1. `tests/sparkgraph/test_recaller.py`
2. `tests/sparkgraph/test_formatter.py`
3. `tests/integration/test_sparkgraph_flush_flow.py`

### 核心对话级测试

1. `flush writes graph -> later turn recall hits`
2. `recall block never persists`
3. `cached prompt excludes recall block`

### 依赖

- `P2-B3`
- `P2-B4`

### 状态

- 已完成
- 质量门禁已推进到 `run_agent` 注入层：`deprecated / low-stability active` 节点不会进入真实发送给模型的 recall block

## `P2-B6` Runtime & Config

### 目标

完成 embedding runtime、config、setup/probe 和 degraded fallback。

### 主要文件

1. `agent/sparkgraph/runtime.py`
2. `hermes_cli/config.py`
4. `gateway/status.py`

### 主要内容

1. `sparkgraph` 顶层配置
2. embedding runtime probe
3. FTS fallback
4. status/health 基础展示

### 必测

1. `tests/sparkgraph/test_runtime.py`
2. `tests/hermes_cli/test_sparkgraph_runtime_status.py`
3. `tests/hermes_cli/test_sparkgraph_profile_paths.py`

### 依赖

- `P2-B1`

### 状态

- 部分完成
- 已完成：
  1. `sparkgraph` 顶层配置
  2. embedding/runtime snapshot
  3. non-blocking status/health 基础展示
  4. degraded fallback
  5. `hermes setup` 基础接入
  6. `hermes status` 基础 SparkGraph 展示
  7. `hermes doctor` 非探网 SparkGraph 诊断
  8. `hermes setup sparkgraph` 支持 Keep current / Reconfigure / Restore defaults
  9. `hermes status --deep` / `hermes doctor --probe` 已支持显式 live probe
- 未完成：
  1. live probe 结果的持久化/历史对比
  2. restore / reconfigure 的更细粒度产品面
  3. `doctor/status` 的更细粒度文案与可操作修复提示

## `P2-B7` Flush Maintenance

### 目标

把 SparkGraph 的轻量整理/遗忘与 Hermes memory flush 对齐。

### 主要文件

1. `agent/sparkgraph/maintenance.py`
2. `agent/sparkgraph/store.py`
3. `run_agent.py`

### 主要内容

1. flush 后 merge 明显重复 candidate
2. 更新 score / status
3. 长期未命中弱 candidate 的降级
4. 被替代节点标记 `deprecated`

### 必测

1. `tests/sparkgraph/test_maintenance.py`
2. `tests/integration/test_sparkgraph_flush_flow.py`

### 核心对话级测试

1. `flush 写入后同轮整理不会污染主回复`
2. `低信号 candidate 在后续 flush 中被降级`
3. `memory flush 与 sparkgraph flush 可共存`

### 依赖

- `P2-B3`
- `P2-B4`

### 状态

- 已完成

## `P2-B9` End-to-End Tests

### 目标

把 flush-only 机制真正跑通。

### 主要文件

1. `tests/integration/test_sparkgraph_flush_flow.py`

### 主要内容

1. 对话脚本驱动
2. flush 触发
3. DB 检查
4. recall 命中
5. degraded fallback

### 状态

- 部分完成
- 已完成：
  1. `fact -> flush -> graph write -> recall`
  2. `greeting -> flush -> no graph write`
  3. `flush extraction failure -> chat safe`
  4. `preference -> flush -> graph write -> recall`
  5. `deprecated node never recalled`
  6. `low-stability active node never recalled`
  7. `empty recall block stays safe when nothing is eligible`
- 未完成：
  1. 独立的更宽 recall 质量门禁
  2. 更接近真实语义改写的 recall 质量验证

### Phase 1 完成标准

1. SparkGraph 能在 flush 中写入 candidate/active 节点
2. 后续对话能命中 recall
3. flush 失败不影响 Hermes 正常回复
4. recall 不污染 cached prompt 和持久化消息
5. 所有 `SG2-*` P0 测试通过

## 5. 对话中如何测试这个新机制

这部分单独明确，因为它是 v2 最大变更点。

### 脚本 1：fact flush recall

1. 连续进行 6+ 轮对话，形成明确 fact
2. 强制触发 compression / flush
3. 检查 SparkGraph DB 写入
4. 再问相关问题
5. 检查 recall 注入

### 脚本 2：preference flush recall

1. 用户表达长期偏好
2. 触发 flush
3. 检查 graph 节点生成
4. 后续问答中检查 recall 命中

注意：

1. 当前已落地的回归用例采用词面可命中的 query
2. 在 embedding 缺失或降级到 FTS-only 时，改写问法的 preference recall 仍可能不稳
3. 这属于当前实现边界，不应被误写成“已完成语义级 preference recall”

### 脚本 3：greeting no write

1. 仅进行寒暄对话
2. 触发 flush
3. 检查 DB 无新增 durable 节点

### 脚本 4：flush degraded safe

1. 模拟 flush 解析失败或 `sparkgraph_record` 异常
2. 确认用户主对话不报错
3. 确认 SparkGraph 本轮安全跳过

## 5.1 主模型 flush extraction eval 当前形态

已落地：

1. `pytest` 基础 harness
2. `scripts/sparkgraph_flush_eval.py` 手动运行脚本
3. 默认读取当前 Hermes `model.default`
4. 默认通过 `resolve_runtime_provider()` 解析当前 provider/base_url/api_key
5. 显式 `--model/--provider/--base-url/--api-key` 可覆盖默认解析
6. 配套回归：
   - `tests/sparkgraph/evals/test_flush_extraction_eval.py`
   - `tests/sparkgraph/evals/test_flush_eval_script.py`
7. 支持把结果持久化到当前 profile 的 `sparkgraph/evals/flush-last.json`
8. 支持 `--compare-last` 与上次结果做最小摘要对比

未落地：

1. 趋势视图 / 多次评测历史
2. provider 级健康探测和自动降级
3. 将 eval 结果纳入更完整的产品化入口（基础 `status/doctor` 展示已落地）

## 6. 当前实施建议

真正开工顺序建议：

1. `P2-B6` 剩余产品面
2. `P2-B9` 剩余对话级测试
3. 更大范围全仓回归

说明：

1. 核心主链已经跑通
2. 当前优先级应转为补齐产品面与剩余测试
3. 避免再次让实现偏离 flush-only 主线
