# SparkGraph 待优化清单（2026-04-02）

> 目的：把当前“设计已明确、业务已验证、但仍未完全收口”的 SparkGraph 后续工作集中记录，避免后续遗漏。  
> 原则：只记录仍值得推进的真实业务问题，不重复写已经稳定收口的事项。

## 当前结论

已在真实业务链路验证通过：

1. 主流程 `memory -> SparkGraph` 同步写入
2. background review 写入 SparkGraph
3. recall block 动态注入
4. embedding 写入与旧节点向量回填
5. `candidate -> deprecated` 在真实业务 maintenance 链路中可触发
6. `last_recalled_at` / `recall_hits` 已接入，`active -> deprecated` 已进入 maintenance 主链
7. 新增近义 troubleshooting 写入已能优先更新旧节点，而不是继续长出新的 Redis 重复节点
8. 同一轮 `sparkgraph_record` 写入的明显相关节点，已开始自动补 `RELATED_TO` 边
9. recall hit 只在非空 recall block 真正注入时记账
10. maintenance 已移出同步主写入路径，改挂到 background review / flush

## P0：Active 治理做实

### 目标

让 active 节点生命周期治理不只是“规则存在”，而是稳定依赖真实业务信号运行。

### 当前已做

1. `sg_nodes.last_recalled_at`
2. recall 命中记账 `recall_hits`
3. maintenance 中接入 `active -> deprecated`

### 仍待补

1. `source_kind` 对 active 降级策略还未细化
   - `manual/review/explicit` 与 `auto/flush` 的治理容忍度应进一步区分
2. recall 命中的持久化指标仍较少
   - 目前只有 `last_recalled_at` 与 `recall_hits`
   - 后续可评估是否要补 recall quality / streak / hit 来源
3. edge 信号尚未进入 active support
   - 当前已开始真实写 `RELATED_TO`
   - 但 active 治理尚未利用边数量/边权重作为 support 的一部分
4. background review maintenance 还没有 dirty-flag
   - 目前已经不阻塞主回复
   - 但仍可能在“没有任何 SparkGraph 变化”的 review 中扫描全图
   - 后续可按 `memory_success / sparkgraph_success / fallback write` 做 gating

## P1：历史重复节点治理

### 目标

不只是“以后别继续长重复”，还要把历史上已经长出来的重复慢慢收回去。

### 当前状态

1. 新写入入口已增加 `ISSUE/FACT` 跨类型 dedup
2. 新的 Redis 近义改写已验证会更新旧节点，而不是新建第三条

### 仍待补

1. 对历史重复节点做 winner/loser 选择
2. loser 标记 `deprecated`
3. evidence / vector / meta 的归并策略
4. recall 默认只保留 winner

### 典型真实样本

1. Redis：`ISSUE|auto` + `FACT|review`
2. Nginx：`ISSUE|auto` + `ISSUE|review` + `FACT|review`

## P2：边关系进入真实业务

### 目标

让 one-hop expansion 和 graph support 不再只是“功能存在、但真实数据为空”。

### 当前状态

1. schema / type / store 已具备
2. recall 已有 one-hop expansion 逻辑
3. 真实业务写入口已开始为同批次明显相关节点自动补 `RELATED_TO`
4. 但目前仍是第一阶段，只覆盖非常保守的同批次规则建边

### 仍待补

1. background review / flush 如何补充 `DERIVED_FROM / APPLIES_TO`
2. edge 如何参与 support / ranking
3. relation completion 是否进入 maintenance
4. merge 后 loser 节点的边如何迁移到 winner

## P3：Dedup/merge 进一步增强

### 目标

提升“不同表达、不同摘要风格、不同类型写入”下的收敛能力。

### 当前状态

1. canonical key exact
2. 同类型 near dedup
3. `ISSUE/FACT` 跨类型 near dedup（真实写入入口已挂接）

### 仍待补

1. superseded merge 的正式落地
2. 更强的摘要归一化
3. 维护阶段的历史近重复批处理
4. 跨语言/跨风格重复的更稳健处理

## 不要遗漏的实施原则

1. 必须优先挂接真实业务入口，避免只在测试里成立
2. 新能力必须补可观测性，避免再次出现“功能似乎存在但无法确认是否触发”
3. 不允许为了 SparkGraph 破坏 Hermes 主回复链
4. 优先做“防止继续变脏”，再做“批量清理历史脏数据”
