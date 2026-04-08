# SparkGraph 测试与评测设计（第一稿）

> 本文专门回答两个问题：  
> 1. 我们如何保证 SparkGraph 的实现不漏、不多、不坏？  
> 2. 我们如何判断用户配置的小模型到底能不能胜任 SparkGraph 的知识提取任务？

## 1. 先说结论

仅靠“代码测试”不够。  
仅靠“主观体验”也不够。

SparkGraph 必须同时具备两套保障：

1. **软件测试**
   - 保证代码行为、边界条件、超时/fallback、配置和路径不出错
2. **模型评测**
   - 保证候选提取和知识分类的小模型质量达标

如果缺少第二套，用户即使把系统装起来了，也可能只是稳定地产生垃圾知识图。

---

## 2. 两层保证分别解决什么问题

## 2.1 软件测试解决

1. schema 是否正确
2. 配置是否能加载
3. timeout 是否不会拖垮主链
4. recall block 是否不会污染 cached prompt
5. degrade/fallback 是否按设计发生
6. profile/path/setup/restore 是否正确

## 2.2 模型评测解决

1. 小模型是否能识别 durable knowledge
2. 是否会把 turn summary 误写成知识点
3. 是否能在多语言输入下稳定分类
4. 是否能给出合适的 node type
5. 是否能给出有用的 relation hints
6. 是否在不同语言、不同风格对话里都维持可接受准确率

---

## 3. 测试体系总图

```text
SparkGraph Quality System
  ├─ A. Unit tests
  ├─ B. Integration tests
  ├─ C. Regression tests
  ├─ D. Failure / timeout tests
  ├─ E. Offline model eval set
  ├─ F. Small-model qualification gate
  └─ G. Shadow-mode online validation
```

---

## 4. 软件测试设计

## 4.1 单元测试

每个核心模块必须有单测：

1. `db.py`
2. `store.py`
3. `types.py`
4. `extractor.py`
5. `classifier.py`
6. `dedup.py`
7. `recaller.py`
8. `formatter.py`
9. `maintenance.py`
10. `config.py`

### 单测重点

1. 状态流：
   - `candidate -> active`
   - `candidate -> deprecated`
   - `merge into existing`
2. recall 过滤：
   - 低分 `candidate` 不进入 recall
   - `deprecated` 不进入 recall
3. semantic dedup：
   - 相似知识合并
   - 不同知识不误合并
4. budget：
   - recall block token/char 上限
5. runtime：
   - timeout
   - missing provider
   - embedding fallback

---

## 4.2 集成测试

需要验证 SparkGraph 与 Hermes 主链的整合行为。

### 必测集成场景

1. 新 session 启动，不带 graph data
2. 最近对话产生 durable knowledge candidate
3. recall block 注入当前轮，但 cached prompt 不变
4. 小模型超时，主对话仍正常
5. embedding runtime 掉线，回退到 FTS recall
6. `flush/session_end` 时 finalize 不阻塞主链
7. profile 切换后 graph DB 隔离

---

## 4.3 回归测试

SparkGraph 不能破坏 Hermes 原有能力。

### 必跑回归类

1. `test_run_agent.py`
2. compression 相关测试
3. flush / gateway async memory 测试
4. config/setup/restore 测试
5. runtime provider resolution 测试
6. profile 测试

原则：

**每个 SparkGraph PR 都必须明确列出必跑回归集合。**

---

## 4.4 失败与超时测试

这部分是硬要求。

### 每类 runtime 都要有独立失败测试

1. classifier timeout
2. extractor timeout
3. dedup lookup failure
4. embedding timeout
5. recall timeout
6. finalize timeout

### 失败时必须验证

1. 是否返回正式结构化错误
2. 是否记录到 health/status
3. 是否正确降级
4. 是否没有阻塞主聊天链路

---

## 5. 模型评测设计

这是最关键的补充。

## 5.1 为什么要单独做模型基线

因为 SparkGraph 不只是传统软件模块，它的核心判断有一部分依赖小模型：

1. durable vs non-durable
2. knowledge type classification
3. session-bound vs reusable
4. relation hint generation

如果没有基线评测，你无法回答：

1. 某个本地模型到底行不行
2. 中文好但英文差怎么办
3. 日语、韩语、法语、德语是否崩
4. 它是在“偶尔能用”，还是“稳定可上线”

所以必须有 **qualification eval**。

## 5.1.1 重要补充：评测必须反映“最终系统运行质量”

模型评测不能只测：

1. classifier 单独的语义理解能力
2. type 分类准确率
3. JSON 输出稳定性

还必须测：

**当模型输出进入 SparkGraph 的 scoring / threshold / dedup / recall 流水线后，最终系统质量是否仍然成立。**

也就是说，模型能力测试至少分两层：

1. **Model-only eval**
   - 只看模型是否能正确输出结构化判断
2. **System-quality eval**
   - 把模型输出送进 SparkGraph 全流程
   - 看最终：
     - 是否被错误全筛空
     - 是否 confidence 全趋同
     - 是否 recall 仍有意义
     - 是否 explicit/manual 被错误挡掉

如果只做第一层，不做第二层，就无法保证最终系统真的可用。

---

## 5.2 评测集目标

建议至少维护一个 **100 条样本** 的 v1 基线评测集。

### 语言分布建议

1. 中文：25
2. 英文：25
3. 日文：10
4. 韩文：10
5. 法文：10
6. 德文：10
7. 混合语言/代码切换：10

总计：100

这是一个起步集，不是终局集。

---

## 5.3 样本类型分布

每种语言都不能只放一种内容。

建议覆盖：

1. durable fact
2. user preference
3. reusable issue pattern
4. stable resource reference
5. lasting decision
6. ephemeral session summary
7. one-off task progress
8. noisy chatter
9. ambiguous borderline case
10. multilingual mixed-with-code case

目标是让模型面对“该记”和“不该记”都能被测到。

---

## 5.4 每条样本的标注字段

每条 fixture 必须标注：

1. `id`
2. `language`
3. `input_messages`
4. `expected_is_durable`
5. `expected_type`
6. `expected_session_bound`
7. `expected_should_recall`
8. `expected_relation_count_range`
9. `difficulty`
10. `notes`

可选：

1. `expected_summary_keywords`
2. `expected_negative_constraints`

---

## 5.5 评测指标

## 5.5.1 核心指标

1. **Durable precision**
   - 被判成 durable 的样本里，有多少真的是 durable
2. **Durable recall**
   - 真正 durable 的样本里，有多少被抓到
3. **Ephemeral rejection precision**
   - 不该记的内容，有多少被正确拒绝
4. **Type accuracy**
   - `FACT/PREFERENCE/ISSUE/RESOURCE/DECISION` 类型准确率
5. **Session-bound accuracy**
   - 是否正确识别“依赖当前会话”

## 5.5.2 辅助指标

1. relation hint usefulness
2. summary compactness
3. multilingual consistency
4. timeout rate
5. malformed JSON / parse failure rate

## 5.5.3 系统运行质量指标

这些指标专门用于回答：

**“模型接进系统后，SparkGraph 最终是否还能正常工作？”**

必须至少统计：

1. **Candidate acceptance rate**
   - 真正 durable 的样本中，有多少最终进入 `candidate`
2. **Active promotion preservation**
   - `explicit/manual/review` 样本中，有多少最终没有被错误挡掉
3. **Confidence spread**
   - confidence 分布是否健康，而不是大量挤在同一窄区间
4. **All-filtered-out rate**
   - 一组正常 durable 样本中，被系统性全部 reject 的比例
5. **Recall usefulness rate**
   - 在 end-to-end 流程后，最终 recall block 中有用项的比例
6. **Session-bound suppression quality**
   - session-bound 内容被压制得是否正确，而不是误伤 durable knowledge

### 建议最低要求

1. `all_filtered_out_rate <= 0.05`
2. `candidate_acceptance_rate >= 0.75`（对人工标注 durable 样本）
3. `active_promotion_preservation >= 0.90`（对 explicit/manual/review 样本）
4. confidence 分布不能退化为：
   - 全体集中在一个极窄默认区间
   - 或绝大多数都低到 recall 永远为空

---

## 5.6 合格线建议

第一版不需要追求学术极致，但要设最低门槛。

建议最低门槛：

1. durable precision >= 0.90
2. ephemeral rejection precision >= 0.92
3. type accuracy >= 0.85
4. session-bound accuracy >= 0.90
5. parse failure rate <= 1%
6. timeout rate <= 3%

### 多语言最低门槛

每种语言单独看：

1. durable precision 不低于 0.80
2. ephemeral rejection precision 不低于 0.85

如果某模型总分合格，但某一语言明显失真，就不能宣称“通用合格”。

---

## 5.7 不同用途的模型门槛不同

不要把所有模型要求设成一个标准。

### Classifier 小模型

职责：

1. durable 判断
2. type 分类
3. session-bound 判断

要求：

1. 精度高
2. 延迟低
3. JSON 稳定

### Extractor 小模型

职责：

1. 从最近窗口提炼知识点候选
2. 生成 summary 和 relation hints

要求：

1. 漏召回不能太高
2. 但更重要的是误写率低

### End-to-end system gate

无论 classifier/extractor 单项分数多高，只要接入系统后出现下面任一问题，就不能视为真正合格：

1. durable 样本大面积被 reject
2. confidence 分布塌缩
3. recall block 长期为空
4. explicit/manual 节点无法保留到 active 流
5. session-bound 抑制逻辑误伤大量正常 durable 样本

### Embedding 模型

评测不看“会不会回答”，而看：

1. duplicate merge 邻近质量
2. multilingual retrieval 一致性
3. recall hit usefulness

---

## 6. 用户自定义小模型的准入策略

这是你真正担心的点。

我建议：

**用户配置的小模型，不应该默认被信任为“可上线模型”。**

应分 3 个级别：

1. `unverified`
   - 用户刚配置
   - 只能用于手动测试或 shadow mode
2. `qualified`
   - 跑过本地 baseline eval，达到门槛
   - 可以进入正式 candidate pipeline
3. `restricted`
   - 评测不达标
   - 只能用于非关键辅助任务，不能负责 durable classification

### 准入判定必须同时参考两类结果

1. **模型能力结果**
2. **系统运行质量结果**

也就是说：

- 不是 classifier accuracy 高就一定能 `qualified`
- 必须同时通过 end-to-end system-quality eval

---

## 6.1 推荐运行流程

当用户配置新的 classifier/extractor 小模型时：

1. 保存配置
2. 执行 probe
3. 执行 smoke eval
4. 如果用户同意，执行 full baseline eval
5. 执行 system-quality eval
5. 输出结果：
   - `Qualified`
   - `Qualified with language caveats`
   - `Not qualified`

---

## 6.2 UI / CLI 提示建议

例如：

```text
SparkGraph classifier model check: Qualified
- Durable precision: 0.93
- Type accuracy: 0.88
- Weakest language: German (0.81)
This model is safe for candidate classification.
```

如果不合格：

```text
SparkGraph classifier model check: Not qualified
- Ephemeral rejection precision below threshold
- High session-summary false positive rate
This model will be restricted to shadow mode only.
```

---

## 7. 影子模式（Shadow Mode）

这是上线前必须有的机制。

### Shadow mode 的作用

1. 跑完整候选提取和分类
2. 记录结果
3. 不写入 `active`
4. 不影响正式 recall

用它来回答：

1. 这个模型在真实使用中会产生多少候选？
2. 脏数据率多高？
3. 哪些语言最差？
4. 平均延迟是否可接受？

建议用户新接一个本地模型时，默认先跑 shadow mode。

Shadow mode 还必须记录以下系统质量指标：

1. candidate 生成率
2. candidate reject 率
3. active promotion 率
4. confidence 分布
5. 空 recall 率
6. explicit/manual 样本保留率

---

## 8. 测试集文件组织建议

建议新增：

```text
tests/sparkgraph/
  test_db.py
  test_store.py
  test_classifier.py
  test_recaller.py
  test_formatter.py
  test_timeout_fallback.py
  evals/
    fixtures/
      zh/
      en/
      ja/
      ko/
      fr/
      de/
      mixed/
    test_eval_smoke.py
    test_eval_schema.py
    test_eval_metrics.py
```

每条 fixture 用 JSON/YAML 都可以，但必须模式固定。

---

## 9. 我们如何避免“写了很多模糊点”

这是本文最重要的一条。

以后凡是设计里出现：

1. “应该”
2. “尽量”
3. “大概”
4. “合理地”

都必须落到下面四类之一：

1. **规则**
   - 可直接编码、可测试
2. **阈值**
   - 可调参、可回归
3. **评测指标**
   - 可量化验收
4. **shadow 观察项**
   - 上线前观察真实表现

否则就不算完成设计。

---

## 10. 当前建议

我建议下一步直接做两件事：

1. 建立 SparkGraph baseline eval fixture schema
2. 把现有 feature/test matrix 增补为 SparkGraph 版本，并加入：
   - multilingual qualification gate
   - shadow mode
   - model qualification status
   - end-to-end system-quality eval

这是把“模糊设计”真正变成“可落地系统”的关键一步。
