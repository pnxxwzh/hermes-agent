# SparkGraph 小模型能力与责任范围规格（v1）

> 目的：冻结 SparkGraph 中“小模型”的能力边界、责任边界、准入条件与禁止事项。  
> 作用：避免后续实现时把小模型用得过重、过宽、不可控。  
> 适用范围：SparkGraph v1。

## 1. 核心结论

SparkGraph 中的小模型只应承担：

1. **原版风格的候选知识抽取**
2. **候选层任务**
3. **可回退、可降级、不可直接污染正式长期层的任务**

一句话：

**小模型负责“先把知识点提出来”，不负责“替系统拍板”。**

---

## 2. 小模型的正式职责

v1 中，小模型允许承担以下职责：

### 2.1 Candidate extraction

从最近窗口中提炼：

1. 候选知识点
2. 候选节点类型粗分类
3. 候选关系
4. 候选 evidence span

注意：

1. 这是候选提取，不是最终写入
2. 输出可以是宽松 JSON 或轻结构文本块
3. 第一版优先对齐原版 graph-memory 的 extraction 方法，而不是前置分类考试

### 2.2 Optional lightweight classification

如确有必要，小模型可以补充：

1. 对已抽取候选做非常轻的类型复核
2. 给出粗粒度 strength

但这不是 v1 主入口，也不应成为正式主链前提。

### 2.3 Shadow-mode evaluation workload

小模型可以用于：

1. 预跑 extractor
2. 影子模式记录
3. prompt 调优回归

### 2.4 Optional low-risk helper tasks

仅在明确不影响正式主链时，可承担：

1. very small reflection-like candidate nomination
2. 非关键辅助摘要

但这类能力在 v1 中不是主目标。

---

## 3. 小模型明确不负责的事

v1 中，小模型不得承担以下职责：

### 3.1 不负责最终制度化写入

不得直接决定：

1. 写入 Hermes `MEMORY.md`
2. 写入 Hermes `USER.md`
3. 生成 Hermes skill
4. patch Hermes skill

### 3.2 不负责最终 active promotion 拍板

小模型可以提供输入，但不能成为唯一决策源。

最终 active promotion 必须结合：

1. scoring
2. evidence
3. support
4. dedup 结果
5. source_kind

### 3.3 不负责 recall 排序主逻辑

小模型不能参与：

1. 每轮 recall 主排序
2. recall block 主过滤
3. cached prompt 相关逻辑

### 3.4 不负责复杂图治理

不得承担：

1. 全图清理主判定
2. 大规模 merge 主判定
3. community 建模
4. 梦境整理的最终治理裁决

### 3.5 不负责重型多字段 reasoning

不应要求小模型一步同时完成：

1. extraction
2. classification
3. relation generation
4. detailed scoring
5. long summary

---

## 4. v1 推荐的小模型任务形态

## 4.1 推荐形态：原版风格最小抽取器

推荐输出 schema：

```json
{
  "items": [
    {
      "summary": "SOCKS proxy issues may require socksio to be installed",
      "type": "FACT",
      "evidence": "以后遇到 SOCKS 代理问题，先检查是否安装了 socksio。",
      "related_to": ["socksio", "SOCKS proxy"]
    }
  ]
}
```

理由：

1. 字段少
2. 更接近原版 graph-memory 的提取范式
3. 更适合小模型做“提名”而不是“裁决”
4. 更容易接到后处理与打分层

## 4.2 次级形态：轻结构文本块抽取

推荐输出格式：

```text
ITEM
summary: httpx requires socksio for SOCKS proxy support
type: FACT
evidence: Using SOCKS proxy with httpx requires socksio support.
related: httpx | socksio | SOCKS proxy
END
```

理由：

1. 比严格 JSON 更稳
2. 仍然比自由文本更容易解析
3. 对小模型友好

## 4.3 不推荐形态

不推荐让小模型直接输出：

1. durable / type / session 三连判题作为主入口
2. `reusability`、`stability` 精细分数
3. 大量 `relation_hints`
4. 长 summary / detail
5. 复杂 graph update plan

这些应交给规则层或后续更强模型。

---

## 5. 小模型与规则层的分工

### 小模型负责

1. 语义理解
2. 候选知识抽取
3. 粗粒度类型提示

### 规则层负责

1. confidence 生成
2. support 计算
3. dedup 阈值
4. active / candidate / deprecated 判定
5. recall 过滤与排序

### 强模型负责

1. 高价值 review
2. 最终制度化升级
3. 复杂冲突裁决

---

## 6. 小模型准入条件

小模型要进入正式 SparkGraph candidate pipeline，至少满足：

1. 通过 preflight
2. 通过 baseline eval 的最低门槛
3. 通过 system-quality eval

否则状态只能是：

1. `unverified`
2. `restricted`

### 只有 `qualified` 才能正式上链

只有 `qualified` 模型，才能参与：

1. 正式 automatic candidate extraction
2. 可选轻量复核

---

## 7. 对当前 0.8B 模型的正式判断

当前测试对象：

1. endpoint：`http://127.0.0.1:8000`
2. model：`Qwen3.5-0.8B-MLX-bf16`

当前结论：

1. 在原版风格抽取任务上比分类题更自然
2. 作为抽取器仍明显弱于 27B
3. 容易出现 JSON 不完整、边字段缺失、关系误用

因此当前状态应定义为：

- `unverified`

当前允许用途：

1. preflight
2. prompt 试验
3. shadow mode

当前不允许用途：

1. 正式 automatic candidate extractor
2. 任何 active promotion 关键路径
3. 依赖其做严格结构裁决的主链

---

## 8. 对整体设计的影响

这个责任边界一旦定稿，会反过来约束整体设计：

1. SparkGraph v1 必须优先实现规则层，而不是把希望押在小模型上
2. extractor prompt 必须优先对齐原版 graph-memory 的真实生产方法
3. active promotion 逻辑必须继续留在 scoring / evidence / support 层
4. evaluation 必须前置到实现前

---

## 9. 设计冻结语句

从现在开始，SparkGraph v1 统一遵守以下冻结语句：

**小模型只负责候选层的知识抽取与轻量提名，不负责最终长期层决策，不负责 recall 主排序，不负责 Hermes curated memory / skill 的制度化写入。**

这是后续评审与实现的硬边界。
