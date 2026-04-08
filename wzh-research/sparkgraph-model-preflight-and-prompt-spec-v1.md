# SparkGraph 模型前置验证与 Prompt 规格（v1）

> 目的：把 SparkGraph 在正式实现前，对小模型的可行性验证与 prompt 设计约束写清楚。  
> 背景：我们已经同时验证过两条路线：前置分类题路线，以及对齐原版 graph-memory 的抽取路线。当前结论是：**原版抽取路线更接近 SparkGraph 的真实入口，也更适合后续继续优化。**

## 1. 当前测试对象

本次前置验证模型：

1. endpoint：`http://127.0.0.1:8000/v1/chat/completions`
2. model：`Qwen3.5-0.8B-MLX-bf16`

---

## 2. 已完成的前置验证

## 2.1 早期两轮：前置分类题 smoke test

字段要求：

1. `is_durable`
2. `knowledge_type`
3. `needs_current_session`
4. `reusability`
5. `stability`
6. `relation_hints`
7. `summary`

结果：

1. 中文 durable fact：基本正确
2. 中文 ephemeral：正确
3. 英文 preference：正确
4. 混合 task-state：正确
5. 日文 durable issue：误判为非 durable

### 结论

1. 0.8B 在简单样本上有一定能力
2. 多语言稳定性不足
3. 一次承担 7 个字段的任务偏重

---

## 2.2 第二轮：轻 prompt

字段要求：

1. `is_durable`
2. `knowledge_type`
3. `needs_current_session`
4. `strength`
5. `summary`

结果：

1. 中文 durable fact：误判为非 durable
2. 中文 ephemeral：正确
3. 英文 preference：误判为非 durable
4. 日文 durable issue：改判为 durable，但类型误为 `PREFERENCE`
5. 混合 task-state：正确

### 结论

1. 单纯减少字段，不一定会更稳定
2. 0.8B 对 durable / preference / issue 边界仍不稳
3. 不能直接把它当正式 SparkGraph classifier

---

## 2.2 原版 graph-memory 抽取法对照测试

测试方式：

1. 直接复刻原版 `extract.ts` / `model-prod.mjs` 的生产 prompt
2. 走 `chat/completions`
3. 采用原版风格的宽松 JSON 解析
4. 测试 5 个真实风格 case：
   - TASK + SKILL
   - EVENT + SKILL
   - 纯寒暄
   - PATCHES
   - 讨论/对比任务

结果：

### `Qwen3.5-0.8B-MLX-bf16`

1. `parsed = 3/5`
2. `passed = 0/5`
3. 典型问题：
   - JSON 不稳定
   - edge 缺 `instruction`
   - 关系误用，如把实体间关系写成 `CONFLICTS_WITH`

### `Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-mlx-4bit`

1. `parsed = 5/5`
2. `passed = 4/5`
3. 唯一失败是一个命名规范细节：
   - `httpx-curl_cffi-scrape` 不符合原版 slug 规则

### 结论

1. 原版抽取法明显比前置分类题更贴近真实能力边界
2. 27B 在原版抽取任务上已经接近可用
3. 0.8B 在原版抽取任务上“会抽”，但还不够稳
4. SparkGraph v1 的小模型主评测路线应切到“抽取式 preflight”，不再把分类题当唯一主入口

---

## 3. 总体结论

### 3.1 我们现在能确定的事

1. 模型能力测试必须前置
2. prompt 不能直接按“大模型友好”的方式写给 0.8B
3. SparkGraph 不应默认建立在“0.8B 能稳定完成前置分类题”这个假设上
4. SparkGraph v1 的主评测路线应优先采用“原版抽取法 + 后处理”

### 3.2 我们现在不能确定的事

1. 0.8B 是否在更轻的原版抽取 prompt 下可达到最低可用线
2. 0.8B 是否适合只做抽取、不做任何前置分类裁决
3. 是否需要 4B 级推理蒸馏模型才能进入正式 automatic extraction path

---

## 4. Prompt 设计原则

SparkGraph 面向小模型的 prompt 必须满足：

1. 字段少
2. 任务单一
3. 优先保证“知识提取质量”，其次才是严格结构
4. 不要求长摘要
5. 不要求复杂 relation reasoning
6. 不要求同时做过多抽象判断

### 不推荐的一次性任务

1. 同时输出 durable/type/session-bound/reuse/stability/relation_hints/long summary
2. 同时做 extraction + classification + relation generation
3. 同时做细粒度打分和文本总结

---

## 5. SparkGraph v1 推荐 prompt 策略

## 5.1 主路线：原版抽取 prompt

推荐直接采用与原版 graph-memory 接近的抽取 prompt：

1. 提取候选知识节点
2. 提取候选关系
3. 使用宽松 JSON 解析
4. 把结构治理放在 SparkGraph 后处理层

理由：

1. 更贴近原版成功路径
2. 更符合 SparkGraph “抽取知识点 -> 存图 -> 召回”的职责
3. 比前置分类题更符合小模型直觉任务

## 5.2 备选路线：前置分类题

前置分类题不再作为主入口，只保留为：

1. 诊断性实验
2. 辅助能力切片测试
3. 某些极简 gating 场景，例如只测 `is_durable`

---

## 6. 推荐 preflight 测试集

在真正跑 100 条 baseline 之前，先跑一组小的 preflight。第一优先级是抽取式 preflight，分类题只做补充。

### 样本数

建议至少：

1. 中文：2
2. 英文：2
3. 日文：2
4. 混合/代码：2

总计：

- `8` 条最小 preflight 样本

### 类型覆盖

至少覆盖：

1. durable fact
2. preference
3. issue
4. ephemeral session-state

---

## 7. Preflight 通过标准

在进入正式 automatic extraction 实现前，至少要满足：

### 7.1 抽取式 preflight

1. JSON parse success >= `0.80`
2. 原版风格 5-case smoke 至少 `4/5` 通过，或出现的失败只属于轻微命名规范问题
3. 不能系统性缺 `instruction`
4. 不能系统性把纯寒暄提成知识

### 7.2 分类题 preflight

分类题不再作为正式准入门槛，只作为诊断项观察：

1. durable judgement 是否严重跑偏
2. type 是否完全塌缩到单一标签

如果达不到：

1. 继续改 prompt
2. 或调整两阶段设计
3. 或更换模型

---

## 8. 对当前 0.8B 模型的临时结论

### 当前状态

- `unverified`

### 当前建议用途

1. prompt 试验
2. preflight
3. shadow mode

### 当前不建议用途

1. 直接作为正式 SparkGraph automatic extractor
2. 直接负责 active promotion 关键判断
3. 作为前置分类题主模型

---

## 9. 后续建议

### 短期

1. 固化一个原版抽取式 preflight harness
2. 继续拿同一批 5-case/多语言样本回归
3. 分类题只保留作辅助诊断

### 中期

1. 形成 100 条 baseline fixture
2. 比较多个本地模型
3. 决定哪个模型能进 `qualified`

---

## 10. 结论

SparkGraph 的小模型能力验证必须前置。  
当前验证结果表明：

1. 前置分类题并不是最适合 SparkGraph 的主路线
2. 原版 graph-memory 的抽取法更接近 SparkGraph 的真实用途
3. 27B 在原版抽取法上已经接近可用
4. 0.8B 在原版抽取法上仍不稳，但方向比分类题更对
5. Phase 1 不应默认把 0.8B 当作“已经通过验证的正式 automatic extractor”
