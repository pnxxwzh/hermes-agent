# SparkGraph 实施检查清单（v2）

> 目的：在正式编码阶段提供逐项核对面，避免偏离 `sparkgraph-sds-v2.md`。  
> 主设计来源：`wzh-research/sparkgraph-sds-v2.md`  
> 测试来源：`wzh-research/sparkgraph-feature-test-matrix-v2.md`  
> 任务来源：`wzh-research/sparkgraph-phase1-task-plan-v2.md`

## 1. 使用规则

1. 只有本清单中已列出的事项可以进入当前实现。
2. 未在清单中登记的新想法，不直接编码，先回写到设计或矩阵。
3. 每完成一项实现，必须同时检查：
   - 对应代码路径
   - 对应测试
   - 是否影响 `flush-only` 主线
4. 任何实现如果重新引入：
   - `review-integrated`
   - 小模型前置主链
   - 非 profile 作用域存储
   视为偏离设计。

## 2. 全局硬约束

- [x] 只按 `sparkgraph-sds-v2.md` 实现，不参考旧 v1 主线作决策
- [x] 不修改 `wzh-research/graph-memory/` 参考代码目录
- [x] 不把 SparkGraph 核心逻辑直接内联到 `run_agent.py`
- [x] 核心包统一放在 `agent/sparkgraph/`
- [x] 不新增单 profile 内 agent namespace
- [x] SparkGraph 只作为 Hermes memory stack 的知识点补充层
- [x] 主自动提取入口只允许 `flush_memories()`
- [x] 不引入任何小模型旁路主链
- [x] recall block 只进动态层，不进 cached system prompt
- [x] SparkGraph 任意失败不得阻断主回复

## 3. 目录与命名

- [x] 新包目录使用 `agent/sparkgraph/`
- [x] 不继续扩大 `agent/graph_memory/` 历史命名
- [x] 配置顶层统一用 `sparkgraph`
- [x] 默认 DB 路径使用 `~/.hermes/.../sparkgraph/default.db`

## 4. Core Skeleton

- [x] 新建 `agent/sparkgraph/__init__.py`
- [x] 新建 `agent/sparkgraph/types.py`
- [x] 新建 `agent/sparkgraph/config.py`
- [x] 新建 `agent/sparkgraph/db.py`
- [x] 新建 `agent/sparkgraph/runtime.py`
- [x] 新建 `agent/sparkgraph/manager.py`
- [x] `NodeType` 仅包含 `FACT/PREFERENCE/ISSUE/RESOURCE/DECISION`
- [x] `EdgeType` 仅包含 `RELATED_TO/DEPENDS_ON/CONFLICTS_WITH/DERIVED_FROM/APPLIES_TO`
- [x] `NodeStatus` 仅包含 `candidate/active/deprecated`
- [x] profile 作用域路径解析已实现
- [x] 非法配置会显式报错

## 5. Schema & Store

- [x] 创建 `_migrations`
- [x] 创建 `sg_nodes`
- [x] 创建 `sg_edges`
- [x] 创建 `sg_evidence`
- [x] 创建 `sg_vectors`
- [x] 创建 `sg_nodes_fts`
- [x] migration 幂等
- [x] CRUD 可用
- [x] evidence append 可用
- [x] FTS 同步可用

## 6. Dedup & Scoring

- [x] canonical key 生成已实现
- [x] FTS 近似查重已实现
- [x] embedding 近邻查重已实现或预留接口
- [x] confidence 计算已实现
- [x] stability 计算已实现
- [x] reuse_score 计算已实现
- [x] `candidate -> active` 判定已实现
- [x] `active -> deprecated` 判定已实现
- [x] 不允许“抽到即 active”

## 7. Flush-Integrated Extraction

- [x] 只在 `flush_memories()` 流程接 SparkGraph 自动提取
- [x] flush prompt 已扩展 SparkGraph 规则
- [x] 不新增独立 SparkGraph 模型调用
- [x] `sparkgraph_record` 已注册
- [x] flush 调用允许模型主动调用 `sparkgraph_record`
- [x] `sparkgraph_record` 负责校验/去重/evidence append/初始打分
- [x] flush 后会清理 SparkGraph 相关痕迹
- [x] flush 失败时 Hermes 主回复不受影响

## 8. Flush-Aligned Maintenance

- [x] flush 后轻量整理已实现
- [x] 明显重复 candidate 可 merge
- [x] 新节点初始状态可设定
- [x] 低信号 candidate 可降级
- [x] 被替代节点可标记 `deprecated`
- [x] 不实现 dream / 重社区分析 / 全图重写

## 9. Recall

- [x] retrieval 已实现
- [x] one-hop expansion 已实现
- [x] active-only filtering 已实现
- [x] ranking 已实现
- [x] budget trim 已实现
- [x] formatter 已实现
- [x] recall block 不持久化到 session history
- [x] recall block 不进入 cached system prompt
- [x] embedding down 时可回退到 FTS-only

## 10. Runtime & Config

- [x] `sparkgraph` 顶层配置已接入
- [x] embedding runtime 已接入
- [x] setup section 已接入
- [x] status/health 至少基础展示已接入
- [x] degraded fallback 已接入
- [x] 自定义 `db_path` 支持且 setup 已有外部路径 warning
- [x] `status --deep` / `doctor --probe` 显式 live probe 已接入
- [x] `scripts/sparkgraph_flush_eval.py` 默认读取当前 Hermes `model.default` 与 active runtime
- [x] flush eval 结果可持久化到当前 profile 的 `sparkgraph/evals/flush-last.json`
- [x] flush eval 支持与上次结果做最小摘要对比
- [x] `status/doctor` 已能显示最近一次 flush eval 的本地结果
- [x] 不实现 flush eval 多次历史 / 趋势视图

## 11. 测试必须同步

- [x] `SG2-DB-001` 对应测试已补
- [x] `SG2-STORE-001` 对应测试已补
- [x] `SG2-DEDUP-001` 对应测试已补
- [x] `SG2-SCORE-001` 对应测试已补
- [x] `SG2-MTN-001` 对应测试已补
- [x] `SG2-REC-001` 对应测试已补
- [x] `SG2-FMT-001` 对应测试已补
- [x] `SG2-TOOL-001` 对应测试已补
- [x] `SG2-TOOL-002` 对应单测与集成覆盖已补
- [x] `SG2-FLS-001` 对应测试已补
- [x] `SG2-FLS-002` 对应测试已补
- [x] `SG2-FLS-003` 对应测试已补
- [x] `SG2-CTX-001` 对应测试已补
- [x] `SG2-EMB-001` 对应测试已补
- [x] `SG2-CFG-001` 对应测试已补
- [x] `SG2-CFG-002` 对应基础 setup 测试已补
- [x] `SG2-CFG-003` 对应测试已补
- [x] `SG2-EVAL-001` 对应基础 pytest harness 已补
- [x] `SG2-EVAL-001` 的 provider 自动解析与显式参数覆盖测试已补
- [x] `SG2-EVAL-001` 的脚本主执行路径、结果持久化、上次结果对比与失败退出码已补测
- [x] `SG2-EVAL-003` 对应测试已补
- [x] `SG2-EVAL-004` 对应基础质量门禁测试已补
- [x] `SG2-EVAL-004` 已推进到 `run_agent` 注入层门禁测试
- [ ] 记录并后续处理 `tests/conftest.py` 里的 event-loop deprecation warning（不阻塞当前 SparkGraph 主线）

## 12. 对话级验证

- [x] `fact -> flush -> graph write -> recall`
- [x] `preference -> flush -> graph write -> recall`
- [x] `greeting -> flush -> no graph write`
- [x] `flush extraction failure -> chat safe`
- [x] `memory flush and sparkgraph flush coexist`
- [x] `deprecated node never recalled`

## 13. 开工门槛

只有在以下条件同时满足时，才允许进入正式编码：

- [x] v2 SDS 已存在
- [x] v2 测试矩阵已存在
- [x] v2 任务单已存在
- [x] 本清单已存在
- [x] 当前实现人承诺只按 v2 主线推进
