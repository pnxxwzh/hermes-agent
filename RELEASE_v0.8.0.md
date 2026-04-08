# Hermes Agent v0.8.0 (v2026.4.3)

**Release Date:** April 3, 2026

> SparkGraph 核心升级：PPR 个性化排序 + LLM 边提取 + 中文 FTS 修复

---

## ✨ Highlights

- **PPR Personalized Ranking** — 在 `agent/sparkgraph/pagerank.py` 新增 Personalized PageRank 算法，对 direct/vector 命中的 seed 节点做 1 跳邻居 PPR 传播，解决 eligibility 门槛过高导致的完全不 recall 问题。对齐 gm `nodePriority` 评分体系（ppr_score × 1000 + source_kind bonus + evidence_count bonus + superseded penalty）。107/107 测试通过。

- **LLM 边提取（Edge Extraction）** — `tools/sparkgraph_tool.py` 新增完整边提取 pipeline：flush 时 LLM 输出 `edges` 数组（SOLVES / DEPENDS_ON / RELATED_TO 等关系），`_resolve_node_ref` 评分制解析引用，`_insert_llm_extracted_edges` 写入 DB。解决了手动边定义覆盖缺失的问题。35 个新测试全部通过，真实 DB 验证 `llm_edges_created: 2` ✅。

- **SOLVES 边类型** — `EdgeType.SOLVES` 新增枚举，`agent/sparkgraph/types.py` + `agent/sparkgraph/db.py` v3 migration 自动升级 CHECK 约束。SOLVES 表示 ISSUE/FACT 被 SKILL 解决，用于 issue→skill 链路 recall。

- **中文 FTS 修复** — `agent/sparkgraph/store.py` 的 `_sanitize_fts_query` 检测 CJK 字符（Unicode range `\u4e00-\u9fff`）后追加 `*` 前缀搜索，解决标准 FTS5 tokenizer 不识别中文字符边界的问题。中文查询 `飞书` 从 0 结果提升至 2 nodes，实测 8/8 recall 场景通过。

- **默认 Embedding 激活** — `agent/sparkgraph/config.py` 默认 embedding 配置从空改为 `bge-m3-mlx-8bit`（本地多语言嵌入服务器，1024-dim，含中文），`http://127.0.0.1:8000/v1`，向量召回路径默认启用。

- **Edge-aware Recall 返回值** — `agent/sparkgraph/recaller.py` 的 `recall_nodes` 返回类型从 `list` 改为 `tuple[list, list]`（nodes + edges），`agent/sparkgraph/formatter.py` 支持 edges 并入输出块（`NodeName --[edge_type]--> NodeName` 格式）。

---

## 🔧 Core Changes

### SparkGraph PPR 个性化排序

| 文件 | 变更 |
|---|---|
| `agent/sparkgraph/pagerank.py` | 新建：PPR 算法 + 图缓存（`personalized_pagerank` + `invalidate_graph_cache`） |
| `agent/sparkgraph/recaller.py` | 新增 `_node_priority_ppr`（对齐 gm `nodePriority`），`_rank_with_ppr` 批量 PPR 传播 |
| `agent/sparkgraph/maintenance.py` | `run_ppr_maintenance` 新增 PPR 重算入口 |
| `tests/sparkgraph/test_pagerank.py` | 新建：PPR 算法单元测试 |
| `tests/sparkgraph/test_maintenance_ppr.py` | 新建：PPR maintenance 测试 |

**PPR 评分维度**（primary → secondary）：
1. `ppr_score × 1000`（主导信号）
2. `source_kind` bonus（explicit +80, manual +40, isolated reflection +800）
3. `confidence × 100`
4. `evidence_count × 5`（上限 20 evidence = +100）
5. superseded penalty（-500）

### SparkGraph LLM 边提取

| 文件 | 变更 |
|---|---|
| `tools/sparkgraph_tool.py` | `_resolve_node_ref`（评分制引用解析）+ `_insert_llm_extracted_edges`（含 self-loop 替代查找）+ schema 新增 `edges` 字段 |
| `agent/sparkgraph/prompting.py` | `build_flush_prompt` 新增边提取引导文字 |
| `run_agent.py` | `_sparkgraph_record_tool` 调用加 `edges=` 参数 |
| `tests/sparkgraph/test_edge_extraction.py` | 新建：35 个测试（SOLVES/DEPENDS_ON/DERIVED_FROM/CONFLICTS_WITH 边提取 + instruction 截断 + 约束异常处理） |

**边类型**：SOLVES / RELATED_TO / DEPENDS_ON / DERIVED_FROM / CONFLICTS_WITH / APPLIES_TO

### SparkGraph 链路修复

| 文件 | 变更 |
|---|---|
| `agent/sparkgraph/store.py` | FTS CJK 前缀搜索 `*` 追加；`get_related_nodes` SQL 双向 seed 排除 bug 修复 |
| `agent/sparkgraph/config.py` | 默认 embedding 改为 `bge-m3-mlx-8bit` |
| `agent/sparkgraph/types.py` | `EdgeType.SOLVES` 新增枚举 |
| `agent/sparkgraph/db.py` | SCHEMA_VERSION 2→3，v3 migration 重建 `sg_edges` 表添加 SOLVES CHECK |
| `agent/sparkgraph/formatter.py` | `recall_nodes` 返回 `(nodes, edges)`，edges 并入输出块 |
| `agent/sparkgraph/manager.py` | 适配 `recall_nodes` 新返回类型 |
| `tests/hermes_cli/test_sparkgraph_setup.py` | 断言适配新的默认 embedding 配置 |

---

## 📋 SG vs GM 功能对比（当前差距）

| 能力维度 | GM | SG (v0.8.0) | 状态 |
|---|---|---|---|
| 多跳图遍历（2-3跳） | ✅ 递归CTE | ❌ 待做 | Phase 2 |
| 返回完整三元组（节点+边） | ✅ | ✅ | ✅ |
| 双路径召回（精确+泛化） | ✅ | ❌ 待做 | Phase 3 |
| 社区检测（Label Propagation） | ✅ | ❌ 无 | 低优 |
| 社区向量搜索 | ✅ | ❌ 无 | 低优 |
| PPR 个性化排序 | ✅ | ✅ | ✅ |
| 向量语义召回 | ✅ | ✅ | ✅ |
| FTS5 中文召回 | ✅ | ✅ | ✅ |
| LLM 边提取 | ✅ extract.ts | ✅ | ✅ |
| 向量 Dedup | ✅ cosine sim | ⚠️ dedup.py 存在但待验证 | 中优 |

---

## 🐛 Bug Fixes

- **`get_related_nodes` 双向 seed 排除 SQL bug**：旧版 `CASE WHEN from_id IN (seeds) THEN to_id ELSE from_id END` 会把 seed↔seed 双向边的 to_id 错误地包含进来。新版改用 UNION 两个单向子查询精确排除。
- **FTS5 中文分词失败**：标准 tokenizer 不处理中文字符边界，中文查询永远返回 0。修复后追加 `*` 前缀搜索。
- **SOLVES 边类型 CHECK 约束缺失**：v2 schema 的 EDGE_TYPES CHECK 不含 SOLVES，导致写入 SOLVES 边时触发 constraint 异常。v3 migration 自动重建表。
- **`_resolve_node_ref` 误匹配**：长 ref 片段可能匹配到短 summary，导致错误节点被选中。评分制改为 exact > prefix > substring > bigram Jaccard 优先级。

---

## ✅ Test Results

- **SparkGraph 测试**：143/143 全部通过（107 原有 + 35 新增 + 1 修复）
- **真实 DB 验证**：`llm_edges_created: 2`，`meta.origin="llm_extracted"` ✅
- **8/8 recall 场景验证**：中文/英文 FTS、中文 CJK 向量召回、英文向量召回、无关词过滤 ✅

---

## 📦 New Files

- `agent/sparkgraph/pagerank.py` — PPR 个性化 PageRank 实现
- `tests/sparkgraph/test_pagerank.py` — PPR 算法单元测试
- `tests/sparkgraph/test_maintenance_ppr.py` — PPR maintenance 测试
- `tests/sparkgraph/test_edge_extraction.py` — 35 个边提取集成测试
- `tests/sparkgraph/conftest.py` — 共享测试 fixtures
- `scripts/sparkgraph_recall_verify.py` — recall 链路验证脚本
- `docs/sparkgraph-phase1-ppr-plan.md` — Phase 1 实施计划文档

---

## 🚀 待做（Phase 2 / Phase 3）

- Phase 2：多跳图遍历（2-3跳递归CTE）
- Phase 3：双路径 recall 架构（精确 + 泛化）
- 向量 Dedup 验证
- 社区检测 + 社区向量搜索

