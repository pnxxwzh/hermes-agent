# SparkGraph 决策参数规格说明（Decision Parameters Spec v1）

> 目的：把 SparkGraph 的默认阈值、状态迁移参数、recall 参数、timeout、qualification 门槛定成可执行规格。  
> 作用：为 `classifier.py`、`dedup.py`、`scoring.py`、`recaller.py`、`maintenance.py`、`runtime.py` 提供统一参数依据。  
> 说明：这些参数是 **v1 默认值**，不是永远不变的真理。它们必须在实现后通过 fixture eval、shadow mode 和真实使用反馈持续验证。

## 1. 设计原则

1. **先保守**
   - 宁可少记，不可乱记
2. **先高 precision，后补 recall**
   - 第一版优先降低脏数据率
3. **参数显式化**
   - 不允许把关键阈值埋进实现细节里
4. **支持后续调参**
   - 参数应集中定义、可测试、可回归

---

## 2. 核心默认策略

### 2.1 总策略

第一版默认策略：

1. `candidate` 易进入
2. `active` 难进入
3. `deprecated` 相对积极
4. recall 只吃高质量 `active`
5. reflection 默认降权

### 2.2 目标偏向

SparkGraph v1 目标偏向：

1. 小而干净的 active 图
2. 少量但高质量的 recall block
3. 小模型误判时优先不写入，而不是写错

---

## 3. Classifier 输出字段与默认阈值

classifier 结构化输出字段至少包括：

1. `is_durable: bool`
2. `knowledge_type: enum`
3. `needs_current_session: bool`
4. `reusability: float [0,1]`
5. `stability: float [0,1]`
6. `relation_hints: list`

### 3.1 durable 最低接受阈值

默认：

- `is_durable == true`

并且：

- `reusability >= 0.55`
- `stability >= 0.55`
- `needs_current_session == false`

否则默认不能直接进入 `active` 候选链。

### 3.2 candidate 进入阈值

候选进入 `candidate` 的最低条件：

1. `is_durable == true`
2. `reusability >= 0.45`
3. `stability >= 0.45`

若低于此阈值：

- 直接 `reject`

### 3.3 reflection 附加限制

若 `source_kind == reflection`：

1. `candidate` 最低要求提高为：
   - `reusability >= 0.60`
   - `stability >= 0.60`
2. 且默认不允许直接 `promote_to_active`

---

## 4. Promotion / Deprecation 参数

## 4.1 `candidate -> active`

默认满足以下任一条件即可升级：

### 路径 A：explicit path

1. `source_kind == explicit`
2. classifier durable 合格
3. dedup 未命中强冲突

### 路径 B：multi-evidence path

1. evidence_count >= 2
2. 来自不同 turn，且建议不同 session 更佳
3. `confidence >= 0.70`
4. `stability >= 0.65`

### 路径 C：relation-supported path

1. 存在至少 `1` 条高权重边连接到 `active` 节点
2. `confidence >= 0.72`
3. `stability >= 0.65`

### 路径 D：manual/review path

1. `source_kind in ('manual', 'review')`
2. classifier durable 合格

## 4.2 promotion 的默认硬限制

以下情况默认不得进入 `active`：

1. `needs_current_session == true`
2. `source_kind == reflection` 且 `evidence_count < 2`
3. dedup 检测为高相似重复但未完成 merge
4. embedding/FTS 都显示为高度不稳定近重复

## 4.3 `active -> deprecated`

默认满足以下任一条件即可降级：

### 路径 A：superseded

1. 被新节点明确替代

### 路径 B：low-signal stale

1. 最近 `N=30` 天无 recall 命中
2. `support_score < 0.35`
3. `stability < 0.45`

### 路径 C：merge cleanup

1. 被合并入 canonical 更强节点

### 路径 D：maintenance low-value detection

1. 维护任务认定为低价值残留
2. 且无人工/explicit 标记保护

---

## 5. Scoring 参数

## 5.1 评分维度

建议统一生成：

1. `confidence`
2. `stability`
3. `reuse_score`
4. `support_score`
5. `source_score`
6. `session_bound_penalty`

### 5.2 默认 source 权重

建议默认：

1. `manual = 1.00`
2. `review = 0.92`
3. `explicit = 0.88`
4. `auto = 0.72`
5. `reflection = 0.58`

### 5.2.1 confidence 生成原则

`confidence` 不能直接等于 source 权重，也不能长期停留在某个默认常数。

第一版要求：

1. `confidence` 必须由多个可解释信号组合生成
2. 组合后必须 clamp 到 `[0,1]`
3. 计算过程中必须保留中间分量，便于测试与调试

建议组成项：

1. `source_score`
2. `classifier_durability_score`
3. `reuse_score`
4. `stability`
5. `support_score`
6. `dedup_consistency_bonus`
7. `session_bound_penalty`

### 5.2.2 confidence 调试要求

实现时，`meta` 中必须保留最少一组调试字段：

1. `confidence_components`
2. `confidence_version`
3. `classification_version`

这样才能在测试中验证：

1. confidence 是否按预期生成
2. 为什么某节点被筛掉
3. 为什么某节点被保留

### 5.3 默认 support 计算方向

support_score 主要来自：

1. evidence_count
2. active-edge support
3. repeated recall usefulness

建议初版归一到 `[0,1]`。

### 5.4 session-bound penalty

若 classifier 给出 `needs_current_session == true`：

- `session_bound_penalty = 0.35`

这意味着即使其他项不错，也很难升成 active。

### 5.5 防止“全筛空”的保护原则

SparkGraph v1 目标是“宁少勿滥”，但不能允许系统性地把所有节点都筛空。

因此必须有三层保护：

1. **candidate 层保护**
   - classifier 达到 candidate 最低门槛时，默认允许进入 `candidate`
   - 不要求一开始就达到 `active` 阈值
2. **active 层保护**
   - `active` 阈值更高，但 explicit/manual/review 路径有单独晋升通道
3. **回归评测保护**
   - baseline fixtures 中 durable recall 不能低到失去系统意义

### 5.6 防止“confidence 全趋同”的保护原则

若大量节点 confidence 长期集中在极窄区间，例如都接近 `0.5`，则说明 scoring 设计失效。

第一版必须有测试验证：

1. 不同 source_kind 的节点 confidence 有可测差异
2. support/evidence 增加会提高 confidence
3. session-bound 内容会显著压低 confidence
4. explicit/manual 路径不会被错误压到和弱 reflection 一样

---

## 6. Dedup / Merge 参数

## 6.1 FTS 初筛

默认取：

- `top_k = 8`

## 6.2 embedding 近邻

默认取：

- `top_k = 8`

## 6.3 merge 建议阈值

### 强 merge 候选

满足：

1. 同 `type`
2. embedding 相似度 >= `0.90`
3. summary/FTS 高相似

则默认：

- `merge_required`

### 弱 merge 候选

满足：

1. 同 `type`
2. embedding 相似度在 `[0.82, 0.90)`

则默认：

- `manual_or_shadow_review`

### 低相似

低于：

- `0.82`

则默认：

- 新建候选

### 已知风险

这些值需要根据 embedding 模型特性再校准。  
第一版只能作为保守起点。

---

## 7. Recall 参数

## 7.1 初筛参数

默认：

1. FTS `top_k = 8`
2. vector `top_k = 8`

## 7.2 relation expansion

默认：

1. 只扩 `1 hop`
2. relation expansion 上限 `max_related = 4`

## 7.3 recall 最终节点数

默认：

- `recall_max_nodes = 4`

### 类型分布上限

默认：

1. 同一类型节点最多 `2`
2. `PREFERENCE` 最多 `1`
3. `DECISION` 最多 `1`

## 7.4 recall 过滤阈值

默认只允许进入 recall 的节点：

1. `status == active`
2. `confidence >= 0.60`
3. `stability >= 0.60`

### 若为低支持边扩展节点

还需：

1. `support_score >= 0.45`

### 默认禁止

1. `candidate`
2. `deprecated`
3. `reflection` 来源但仍未完成有效升级的节点

## 7.5 recall block budget

默认：

1. `graph_budget_ratio = 0.12`
2. 若无法获得精确 token budget，则回退到：
   - `max_chars = 1800`

---

## 8. Timeout 参数

### 8.1 reflection timeout

- `8s`

### 8.2 extraction timeout

- `20s`

### 8.3 maintenance timeout

- `60s`

### 8.4 embedding timeout

- `15s`

### 8.5 recall timeout

- `5s`

## 8.6 超时后的默认行为

1. reflection timeout
   - skip
2. extraction timeout
   - mark pending / skip current turn
3. embedding timeout
   - FTS fallback
4. recall timeout
   - no graph block this turn
5. maintenance timeout
   - reschedule

---

## 9. Qualification 参数

## 9.1 总体门槛

模型要进入 `qualified`，至少满足：

1. durable precision >= `0.90`
2. ephemeral rejection precision >= `0.92`
3. type accuracy >= `0.85`
4. session-bound accuracy >= `0.90`
5. parse failure rate <= `0.01`
6. timeout rate <= `0.03`

## 9.2 分语言下限

每个语言分桶至少满足：

1. durable precision >= `0.80`
2. ephemeral rejection precision >= `0.85`

## 9.3 `restricted` 判定

满足以下任一即 `restricted`：

1. durable precision < `0.80`
2. ephemeral rejection precision < `0.82`
3. parse failure rate > `0.05`
4. timeout rate > `0.10`

## 9.4 `unverified`

默认新模型都是：

- `unverified`

只有跑过 baseline eval 后才能进入 `qualified` 或 `restricted`。

---

## 10. Shadow Mode 参数

### 默认

- `shadow_mode = false`

### 当模型为 `unverified` 时

建议默认：

- 强制 `shadow_mode = true`

### 当模型为 `restricted` 时

建议默认：

1. 禁止正式 active promotion
2. 只能 shadow run

---

## 11. Background / Maintenance 参数

## 11.1 maintenance 触发建议

第一版建议：

1. session_end / flush 时轻量触发
2. background idle 时可触发
3. dream job 延后到 Phase 4

## 11.2 low-signal candidate cleanup

若满足：

1. `status == candidate`
2. `age_days >= 14`
3. `evidence_count == 1`
4. `support_score < 0.30`

则建议：

- `deprecate`

## 11.3 low-signal active cleanup

若满足：

1. `status == active`
2. `age_days >= 30`
3. `recent_hits == 0`
4. `support_score < 0.35`
5. `stability < 0.45`

则建议：

- `deprecate`

---

## 12. 配置暴露策略

## 12.1 不建议一开始全暴露给用户

下面这些参数应作为内部默认值先存在，不建议首版 setup 全量暴露：

1. merge similarity thresholds
2. support_score 细节权重
3. relation expansion 内部权重
4. cleanup age thresholds

## 12.2 可以暴露给高级用户的

1. `recall_max_nodes`
2. `graph_budget_ratio`
3. runtime timeouts
4. `shadow_mode`

---

## 13. 必测用例

### `test_confidence_components_are_persisted`

验证：

1. `meta.confidence_components` 存在
2. 关键中间分量可追踪

### `test_confidence_varies_by_source_kind`

验证：

1. `manual/review/explicit/auto/reflection` 不会生成完全趋同的 confidence

### `test_confidence_increases_with_support`

验证：

1. 增加 evidence/active-edge support 后 confidence 上升

### `test_session_bound_penalty_reduces_confidence`

验证：

1. session-bound 内容会被明显压分

### `test_candidate_path_does_not_filter_all_durable_nodes`

验证：

1. 对一组正常 durable fixtures，candidate 层不会全部 reject

### `test_active_path_preserves_explicit_and_manual_nodes`

验证：

1. explicit/manual 路径不会因统一阈值被错误全部挡住

### `test_candidate_threshold_rejects_low_durability`

验证：

1. `reusability/stability` 过低直接 reject

### `test_reflection_never_promotes_directly_to_active`

验证：

1. reflection 默认不得直接 active

### `test_explicit_path_promotes_with_lower_evidence_count`

验证：

1. explicit path 可以不等到多证据再升 active

### `test_recall_filters_candidate_nodes`

验证：

1. candidate 默认不进 recall

### `test_recall_respects_max_nodes`

验证：

1. recall 节点数上限生效

### `test_embedding_timeout_falls_back_to_fts`

验证：

1. embedding timeout 时 recall 仍能工作

### `test_unverified_model_forces_shadow_mode`

验证：

1. 新模型默认只能 shadow

### `test_qualified_model_requires_language_floor`

验证：

1. 即使总分高，若单语言过低也不能 qualified

---

## 14. 已知缺陷与风险

### 14.1 这些参数仍然只是 v1 默认值

它们不是拍板后的“永久真理”，必须通过：

1. fixture eval
2. shadow mode
3. 真实使用数据

再迭代。

### 14.2 embedding 阈值高度依赖模型

不同 embedding 模型下，`0.82/0.90` 这类阈值可能需要明显调整。

### 14.3 support_score 公式还未细化成最终函数

本文定义了维度与阈值方向，但 support_score 具体公式仍需在实现时最终落定。

---

## 15. 结论

本参数规格的目标不是“一次定终身”，而是：

1. 防止实现时临时拍脑袋
2. 提供统一默认值
3. 让测试与评测有明确依据

它已经足够支撑 SparkGraph Phase 1 的实现与测试。
