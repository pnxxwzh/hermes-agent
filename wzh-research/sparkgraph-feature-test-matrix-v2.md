# SparkGraph Feature/Test 追踪矩阵（v2）

> 适用设计：`wzh-research/sparkgraph-sds-v2.md`  
> 当前主线：**flush-only knowledge extraction**

## 1. 使用规则

这份矩阵用于保证 SparkGraph v2：

1. 不漏 feature
2. 不漏测试
3. 不把旧的 `review-integrated` 需求混进实现
4. 每个改动都能追溯到代码位置与测试用例

规则：

1. 每个 feature 都必须分配 `SG2-*` 编号
2. 每个 PR 必须声明涉及哪些 `Feature ID`
3. 每个 `Feature ID` 必须至少有：
   - 1 个单测
   - 1 个集成测试，或明确说明为什么不需要
4. 每个 timeout/fallback/degraded feature 必须有失败测试
5. 每个涉及对话后处理的 feature，必须说明：
   - 它是否运行在 `flush_memories()`
   - 是否会新增模型调用
   - 出错时是否影响主回复

## 2. 字段定义

| 字段 | 说明 |
|---|---|
| `Feature ID` | 稳定编号 |
| `Feature Name` | 功能名 |
| `Change Type` | `new` / `modified` |
| `Primary Code Paths` | 主改动文件 |
| `Supporting Code Paths` | 配套改动文件 |
| `Unit Tests` | 单元测试 |
| `Integration Tests` | 集成测试 |
| `Regression Tests` | 需回归验证的既有测试 |
| `Negative/Failure Tests` | 失败、超时、fallback 覆盖 |
| `Status` | `planned` / `in_progress` / `partial` / `done` |

## 3. v2 矩阵

| Feature ID | Feature Name | Change Type | Primary Code Paths | Supporting Code Paths | Unit Tests | Integration Tests | Regression Tests | Negative/Failure Tests | Status |
|---|---|---|---|---|---|---|---|---|---|
| `SG2-DB-001` | SparkGraph DB schema and migrations | `new` | `agent/sparkgraph/db.py` | `agent/sparkgraph/types.py` | `tests/sparkgraph/test_db.py` | `tests/integration/test_sparkgraph_flush_flow.py` |  | `test_db_migration_idempotent` | `done` |
| `SG2-STORE-001` | Store CRUD and evidence append | `new` | `agent/sparkgraph/store.py` | `agent/sparkgraph/db.py` | `tests/sparkgraph/test_store.py` | `tests/integration/test_sparkgraph_flush_flow.py` |  | `test_store_append_evidence_without_duplicate_node` | `done` |
| `SG2-DEDUP-001` | Canonicalization and semantic dedup | `new` | `agent/sparkgraph/dedup.py` | `agent/sparkgraph/store.py` | `tests/sparkgraph/test_dedup.py` | `tests/integration/test_sparkgraph_flush_flow.py` |  | `test_near_duplicate_merge_is_stable` | `done` |
| `SG2-SCORE-001` | Confidence/stability/reuse scoring | `new` | `agent/sparkgraph/scoring.py` | `agent/sparkgraph/store.py` | `tests/sparkgraph/test_scoring.py` | `tests/integration/test_sparkgraph_flush_flow.py` |  | `test_candidate_path_does_not_collapse_all_nodes` | `done` |
| `SG2-MTN-001` | Flush-aligned lightweight maintenance | `new` | `agent/sparkgraph/maintenance.py` | `agent/sparkgraph/store.py`, `agent/sparkgraph/scoring.py`, `run_agent.py` | `tests/sparkgraph/test_maintenance.py` | `tests/integration/test_sparkgraph_flush_flow.py` | `tests/gateway/test_async_memory_flush.py` | `test_flush_maintenance_does_not_break_chat`; `test_low_signal_candidate_gets_downgraded_on_flush` | `done` |
| `SG2-REC-001` | Recall retrieval core | `new` | `agent/sparkgraph/recaller.py` | `agent/sparkgraph/store.py`, `agent/sparkgraph/scoring.py` | `tests/sparkgraph/test_recaller.py` | `tests/integration/test_sparkgraph_flush_flow.py` |  | `test_recall_without_vectors_falls_back_to_fts` | `done` |
| `SG2-FMT-001` | Recall block formatter | `new` | `agent/sparkgraph/formatter.py` | `agent/sparkgraph/recaller.py` | `tests/sparkgraph/test_formatter.py` | `tests/integration/test_sparkgraph_flush_flow.py` | `tests/test_run_agent.py` | `test_recall_budget_enforced`; `test_cached_prompt_excludes_recall_block` | `done` |
| `SG2-TOOL-001` | `sparkgraph_record` tool | `new` | `tools/sparkgraph_tool.py` | `tools/__init__.py`, `agent/sparkgraph/manager.py` | `tests/tools/test_sparkgraph_record_tool.py` | `tests/integration/test_sparkgraph_flush_flow.py` |  | `test_sparkgraph_record_rejects_invalid_payload` | `done` |
| `SG2-TOOL-002` | `sparkgraph_search` / `sparkgraph_stats` tools | `new` | `tools/sparkgraph_tool.py` | `tools/__init__.py` | `tests/tools/test_sparkgraph_query_tools.py` | `tests/integration/test_sparkgraph_flush_flow.py` |  | `test_sparkgraph_search_no_results` | `done` |
| `SG2-FLS-001` | Flush-integrated extraction bridge | `modified` | `run_agent.py` | `agent/sparkgraph/prompting.py`, `tools/sparkgraph_tool.py`, `agent/sparkgraph/manager.py` | `tests/sparkgraph/test_flush_prompting.py` | `tests/integration/test_sparkgraph_flush_flow.py` | `tests/test_run_agent.py`, `tests/gateway/test_async_memory_flush.py` | `test_flush_extraction_failure_does_not_break_chat`; `test_flush_does_not_add_extra_calls_beyond_flush` | `done` |
| `SG2-FLS-002` | Flush artifact cleanup safety | `modified` | `run_agent.py` | `agent/sparkgraph/prompting.py` | `tests/sparkgraph/test_flush_prompting.py` | `tests/integration/test_sparkgraph_flush_flow.py` | `tests/gateway/test_flush_memory_stale_guard.py` | `test_flush_does_not_leak_sparkgraph_prompt_artifacts` | `done` |
| `SG2-FLS-003` | SparkGraph as memory-complement lifecycle | `modified` | `run_agent.py`, `agent/sparkgraph/maintenance.py` | `agent/sparkgraph/store.py`, `agent/sparkgraph/scoring.py` | `tests/sparkgraph/test_maintenance.py` | `tests/integration/test_sparkgraph_flush_flow.py` | `tests/test_run_agent.py` | `test_memory_flush_and_sparkgraph_flush_can_coexist`; `test_greeting_flush_writes_neither_memory_nor_graph` | `done` |
| `SG2-CTX-001` | Ephemeral recall injection | `modified` | `run_agent.py` | `agent/sparkgraph/manager.py`, `agent/sparkgraph/formatter.py` | `tests/sparkgraph/test_formatter.py` | `tests/integration/test_sparkgraph_flush_flow.py` | `tests/test_run_agent.py` | `test_recall_block_never_persisted_to_session_history` | `done` |
| `SG2-EMB-001` | Embedding runtime and degraded fallback | `modified` | `agent/sparkgraph/runtime.py` | `hermes_cli/config.py`, `gateway/status.py` | `tests/sparkgraph/test_runtime.py` | `tests/integration/test_sparkgraph_flush_flow.py` | `tests/hermes_cli/test_gateway_runtime_health.py` | `test_embedding_runtime_down_uses_fts_only` | `done` |
| `SG2-CFG-001` | `sparkgraph` config block | `modified` | `hermes_cli/config.py` | `agent/sparkgraph/config.py` | `tests/hermes_cli/test_config.py`, `tests/hermes_cli/test_sparkgraph_profile_paths.py`, `tests/sparkgraph/test_config.py` |  |  | `test_invalid_sparkgraph_config_rejected`; `test_parse_sparkgraph_config_rejects_non_boolean_recall_enabled`; `test_parse_sparkgraph_config_rejects_invalid_budget_ratio`; `test_parse_sparkgraph_config_rejects_non_mapping_section` | `done` |
| `SG2-CFG-002` | Setup / probe / restore integration | `modified` | `hermes_cli/setup.py`, `hermes_cli/status.py`, `hermes_cli/doctor.py` | `hermes_cli/config.py`, `gateway/status.py`, `agent/sparkgraph/flush_eval.py` | `tests/hermes_cli/test_setup.py`, `tests/hermes_cli/test_sparkgraph_setup.py`, `tests/hermes_cli/test_status.py`, `tests/hermes_cli/test_doctor.py` |  | `tests/hermes_cli/test_setup.py`, `tests/hermes_cli/test_status.py`, `tests/hermes_cli/test_doctor.py` | `test_setup_probe_reports_embedding_degraded`; `test_check_sparkgraph_runtime_ready_when_db_parent_exists`; `test_check_sparkgraph_warns_on_last_failed_eval` | `done` |
| `SG2-CFG-003` | Profile-scoped storage compatibility | `modified` | `agent/sparkgraph/config.py`, `agent/sparkgraph/db.py` | `hermes_cli/profiles.py` | `tests/hermes_cli/test_sparkgraph_profile_paths.py` |  | `tests/hermes_cli/test_profiles.py` | `test_external_db_path_warns_but_works` | `done` |
| `SG2-EVAL-001` | Main-model flush extraction eval harness | `new` | `agent/sparkgraph/flush_eval.py`, `scripts/sparkgraph_flush_eval.py`, `tests/sparkgraph/evals/test_flush_extraction_eval.py` | `tests/sparkgraph/evals/fixtures/flush/*`, `agent/sparkgraph/prompting.py` | `tests/sparkgraph/evals/test_flush_extraction_eval.py`, `tests/sparkgraph/evals/test_flush_eval_script.py` |  |  | `test_flush_eval_fixture_schema_valid`; `test_resolve_eval_runtime_requires_model`; `test_resolve_eval_runtime_requires_base_url`; `test_main_writes_report_and_compares_last`; `test_main_returns_nonzero_when_case_fails` | `done` |
| `SG2-EVAL-003` | Flush-only dialogue flow coverage | `new` | `tests/integration/test_sparkgraph_flush_flow.py` | `run_agent.py`, `agent/sparkgraph/*` |  | `tests/integration/test_sparkgraph_flush_flow.py` | `tests/test_run_agent.py`, `tests/gateway/test_async_memory_flush.py` | `test_no_flush_no_graph_write`; `test_flush_writes_graph_then_recall_hits`; `test_preference_flush_writes_graph_and_later_turn_recalls`; `test_query_tools_can_inspect_flush_written_nodes` | `done` |
| `SG2-EVAL-004` | End-to-end recall quality gate | `new` | `tests/integration/test_sparkgraph_flush_flow.py` | `agent/sparkgraph/recaller.py`, `agent/sparkgraph/formatter.py` | `tests/sparkgraph/test_recaller.py`, `tests/sparkgraph/test_formatter.py` | `tests/integration/test_sparkgraph_flush_flow.py` | `tests/test_run_agent.py` | `test_recall_does_not_return_deprecated_nodes`; `test_recall_empty_is_safe`; `test_recall_nodes_excludes_low_stability_active_nodes`; `test_sparkgraph_recall_skips_deprecated_nodes_in_real_manager`; `test_sparkgraph_recall_skips_low_stability_active_nodes_in_real_manager` | `done` |

## 4. v2 开发约束

实现过程中必须满足：

1. 未出现在本矩阵里的 feature，不进入本轮开发。
2. 每新增或修改一个 `Feature ID`，必须同步更新本矩阵状态。
3. 所有旧的 `GM-*` 条目视为历史参考，不再代表 v2 主线。
4. 所有涉及自动知识提取的代码，都必须明确标注：
   - `flush-integrated`
   - `manual-only`
5. 所有 SparkGraph 路径与命名空间相关改动，必须继续遵守 Hermes 官方 `HERMES_HOME/profile` 组织模型。
