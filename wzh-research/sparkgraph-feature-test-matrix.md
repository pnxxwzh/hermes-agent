# SparkGraph Feature/Test 追踪矩阵（第一稿）

## 1. 使用规则

这份矩阵用于保证后续整合开发：

1. 不漏 feature
2. 不漏测试
3. 不把未定义改动混进实现
4. 每个改动都能追溯到代码位置与测试用例

规则：

1. 每个 feature 都必须分配 `Feature ID`
2. 每个 PR 必须声明涉及哪些 `Feature ID`
3. 每个 `Feature ID` 必须至少有：
   - 1 个单测
   - 1 个集成测试或明确说明为什么不需要
4. 每个 timeout/fallback feature 必须有失败测试

---

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
| `Status` | `planned` / `in_progress` / `done` |

---

## 3. 矩阵

| Feature ID | Feature Name | Change Type | Primary Code Paths | Supporting Code Paths | Unit Tests | Integration Tests | Regression Tests | Negative/Failure Tests | Status |
|---|---|---|---|---|---|---|---|---|---|
| `GM-DB-001` | Graph DB schema and migrations | `new` | `agent/graph_memory/db.py` | `agent/graph_memory/types.py` | `tests/graph_memory/test_db.py` | `tests/graph_memory/test_manager.py` |  | `test_db_migration_idempotent` | `planned` |
| `GM-STORE-001` | Graph store CRUD | `new` | `agent/graph_memory/store.py` | `agent/graph_memory/write_policy.py` | `tests/graph_memory/test_store.py` | `tests/graph_memory/test_manager.py` |  | `test_duplicate_node_merge_policy` | `planned` |
| `GM-EXT-001` | Turn extraction pipeline | `new` | `agent/graph_memory/extractor.py` | `agent/graph_memory/manager.py` | `tests/graph_memory/test_extractor.py` | `tests/test_run_agent_graph_memory.py` |  | `test_extraction_timeout_marks_pending` | `planned` |
| `GM-EXP-001` | Explicit memory candidate extraction | `new` | `agent/graph_memory/explicit.py` | `agent/graph_memory/manager.py` | `tests/graph_memory/test_explicit.py` | `tests/test_run_agent_graph_memory.py` |  | `test_explicit_low_confidence_skipped` | `planned` |
| `GM-REF-001` | Turn-level reflection | `new` | `agent/graph_memory/reflection.py` | `agent/auxiliary_client.py` | `tests/graph_memory/test_reflection.py` | `tests/test_run_agent_graph_memory.py` |  | `test_reflection_timeout_fallback` | `planned` |
| `GM-RCL-001` | Graph recaller core | `new` | `agent/graph_memory/recaller.py` | `agent/graph_memory/store.py` | `tests/graph_memory/test_recaller.py` | `tests/test_run_agent_graph_memory.py` |  | `test_recall_without_vectors_falls_back_to_fts` | `planned` |
| `GM-POOL-001` | Recall pool | `new` | `agent/graph_memory/recall_pool.py` | `agent/graph_memory/manager.py` | `tests/graph_memory/test_recall_pool.py` | `tests/test_run_agent_graph_memory.py` |  | `test_recall_pool_evicts_lrfu` | `planned` |
| `GM-FMT-001` | Graph recall block formatting | `new` | `agent/graph_memory/formatter.py` | `agent/graph_memory/recaller.py` | `tests/graph_memory/test_formatter.py` | `tests/test_run_agent_graph_memory.py` |  | `test_graph_budget_enforced` | `planned` |
| `GM-CTX-001` | Ephemeral graph recall injection | `modified` | `run_agent.py` | `agent/graph_memory/manager.py` | `tests/graph_memory/test_formatter.py` | `tests/test_run_agent_graph_memory.py` | `tests/test_run_agent.py` | `test_cached_prompt_excludes_graph_block` | `planned` |
| `GM-TOOLS-001` | Graph tools (`graph_search`, `graph_record`, `graph_stats`, `graph_maintain`) | `new` | `tools/graph_memory_tools.py` | `tools/__init__.py` | `tests/tools/test_graph_memory_tools.py` | `tests/test_run_agent_graph_memory.py` | `tests/test_plugins.py` | `test_graph_search_no_results` | `planned` |
| `GM-MTN-001` | Graph maintenance | `new` | `agent/graph_memory/maintenance.py` | `agent/graph_memory/store.py` | `tests/graph_memory/test_maintenance.py` | `tests/test_run_agent_graph_memory.py` |  | `test_maintenance_timeout_reschedules` | `planned` |
| `GM-FLS-001` | Flush/session_end graph finalize | `modified` | `run_agent.py` | `gateway/run.py`, `agent/graph_memory/manager.py` | `tests/graph_memory/test_manager.py` | `tests/gateway/test_graph_memory_flush.py` | `tests/gateway/test_async_memory_flush.py`, `tests/gateway/test_flush_memory_stale_guard.py` | `test_graph_finalize_failure_does_not_break_flush` | `planned` |
| `GM-AUX-001` | Auxiliary routing for graph tasks | `modified` | `agent/auxiliary_client.py` | `hermes_cli/config.py` | `tests/test_auxiliary_graph_memory.py` | `tests/test_run_agent_graph_memory.py` |  | `test_graph_task_route_missing_provider_fallback` | `planned` |
| `GM-RT-001` | Graph runtime provider resolution | `modified` | `hermes_cli/runtime_provider.py` | `agent/graph_memory/config.py`, `agent/auxiliary_client.py` | `tests/hermes_cli/test_graph_memory_runtime_provider.py` | `tests/test_run_agent_graph_memory.py` | `tests/test_auxiliary_config_bridge.py` | `test_graph_embedding_runtime_rejects_text_only_model` | `planned` |
| `GM-RT-002` | Graph runtime health visibility | `modified` | `gateway/status.py` | `gateway/run.py` | `tests/gateway/test_graph_memory_runtime_health.py` | `tests/hermes_cli/test_gateway_runtime_health.py` | `tests/hermes_cli/test_gateway_runtime_health.py` | `test_graph_runtime_health_reports_degraded_embedding_runtime` | `planned` |
| `GM-ERR-001` | Structured graph runtime errors | `modified` | `tools/graph_memory_tools.py`, `agent/graph_memory/manager.py` | `run_agent.py` | `tests/tools/test_graph_memory_tools.py` | `tests/test_run_agent_graph_memory.py` |  | `test_graph_search_returns_structured_runtime_error` | `planned` |
| `GM-ERR-002` | Graph degraded-mode fallback | `modified` | `agent/graph_memory/recaller.py`, `agent/graph_memory/maintenance.py` | `run_agent.py` | `tests/graph_memory/test_recaller.py`, `tests/graph_memory/test_maintenance.py` | `tests/test_run_agent_graph_memory.py` |  | `test_graph_runtime_down_main_chat_still_works` | `planned` |
| `GM-CFG-001` | `graph_memory` config block | `modified` | `hermes_cli/config.py` | `agent/graph_memory/config.py` | `tests/hermes_cli/test_graph_memory_config.py` | `tests/test_run_agent_graph_memory.py` |  | `test_invalid_graph_memory_config_rejected` | `planned` |
| `GM-CFG-002` | Graph-memory setup wizard integration | `modified` | `hermes_cli/setup.py` | `hermes_cli/config.py`, `cli.py` | `tests/hermes_cli/test_graph_memory_setup.py` | `tests/hermes_cli/test_setup.py` | `tests/hermes_cli/test_setup.py` | `test_setup_keep_current_graph_runtime` | `planned` |
| `GM-CFG-003` | Graph-memory config migration/backfill | `modified` | `hermes_cli/config.py` | `hermes_cli/setup.py` | `tests/hermes_cli/test_graph_memory_config_migration.py` | `tests/hermes_cli/test_graph_memory_config_restore.py` | `tests/hermes_cli/test_set_config_value.py` | `test_old_config_version_migrates_graph_memory_block` | `planned` |
| `GM-CFG-004` | Graph-memory CLI/gateway config bridge | `modified` | `cli.py`, `gateway/run.py` | `hermes_cli/runtime_provider.py` | `tests/test_graph_memory_config_bridge.py` | `tests/hermes_cli/test_graph_memory_gateway_bridge.py` | `tests/test_auxiliary_config_bridge.py` | `test_graph_memory_bridge_missing_subsection_safe` | `planned` |
| `GM-CFG-005` | Graph-memory namespace/storage compatibility with Hermes profiles | `modified` | `agent/graph_memory/config.py`, `agent/graph_memory/db.py` | `hermes_cli/config.py`, `hermes_cli/setup.py`, `hermes_cli/profiles.py` | `tests/hermes_cli/test_graph_memory_profile_paths.py`, `tests/graph_memory/test_db.py` | `tests/hermes_cli/test_graph_memory_setup.py` | `tests/hermes_cli/test_profiles.py` | `test_graph_memory_default_db_path_scopes_to_current_profile`; `test_custom_external_db_path_warns_but_does_not_break` | `planned` |
| `GM-TRI-001` | Memory review triage with small model | `modified` | `run_agent.py` | `agent/auxiliary_client.py` | `tests/test_graph_review_triage.py` | `tests/test_run_agent_graph_memory.py` | `tests/test_run_agent.py` | `test_memory_review_triage_timeout_degrades_safely` | `planned` |
| `GM-TRI-002` | Skill review triage with small model | `modified` | `run_agent.py` | `agent/auxiliary_client.py` | `tests/test_graph_review_triage.py` | `tests/test_run_agent_graph_memory.py` | `tests/tools/test_skill_manager_tool.py` | `test_skill_review_triage_timeout_degrades_safely` | `planned` |
| `GM-DRM-001` | Internal dream job runner | `new` | `agent/background_jobs.py`, `agent/graph_memory/dream.py` | `gateway/run.py`, `run_agent.py` | `tests/graph_memory/test_dream.py` | `tests/gateway/test_graph_memory_dream.py` | `tests/gateway/test_gateway_shutdown.py` | `test_dream_timeout_does_not_block_runtime` | `planned` |
| `GM-EVAL-001` | Small-model quality evaluation fixtures | `new` | `tests/graph_memory/evals/fixtures/*` | `tests/graph_memory/test_small_model_eval_smoke.py` | `tests/graph_memory/test_small_model_eval_smoke.py` |  |  | `test_eval_fixture_schema_valid` | `planned` |
| `GM-EVAL-002` | Multilingual classifier qualification baseline | `new` | `tests/sparkgraph/evals/fixtures/*` | `tests/sparkgraph/evals/test_eval_metrics.py` | `tests/sparkgraph/evals/test_eval_metrics.py` |  |  | `test_classifier_fails_qualification_below_threshold` | `planned` |
| `GM-EVAL-003` | User-configured small-model qualification status | `modified` | `agent/graph_memory/config.py`, `hermes_cli/setup.py` | `gateway/status.py`, `cli.py` | `tests/hermes_cli/test_graph_memory_setup.py`, `tests/sparkgraph/test_model_qualification.py` | `tests/hermes_cli/test_gateway_runtime_health.py` |  | `test_unverified_model_restricted_to_shadow_mode` | `planned` |
| `GM-EVAL-004` | Shadow mode for candidate-only graph runs | `new` | `agent/graph_memory/manager.py` | `run_agent.py`, `gateway/run.py` | `tests/sparkgraph/test_shadow_mode.py` | `tests/test_run_agent_graph_memory.py` |  | `test_shadow_mode_never_promotes_active_nodes` | `planned` |
| `GM-EVAL-006` | Manual/explicit-only fallback mode | `new` | `agent/graph_memory/manager.py`, `run_agent.py` | `agent/graph_memory/extractor.py`, `agent/graph_memory/classifier.py` | `tests/sparkgraph/test_manual_only_mode.py` | `tests/test_run_agent_graph_memory.py` |  | `test_unverified_model_can_disable_auto_pipeline_without_breaking_recall` | `planned` |
| `GM-EVAL-007` | Preflight harness blocks unsafe classifier assumptions | `new` | `tests/sparkgraph/evals/test_preflight_smoke.py` | `tests/sparkgraph/evals/fixtures/preflight/*`, `agent/graph_memory/classifier.py` | `tests/sparkgraph/evals/test_preflight_smoke.py` |  |  | `test_preflight_marks_model_unverified_when_multilingual_smoke_fails` | `planned` |
| `GM-SCORE-001` | Confidence generation and score-component persistence | `new` | `agent/graph_memory/scoring.py` | `agent/graph_memory/store.py`, `agent/graph_memory/classifier.py` | `tests/sparkgraph/test_scoring.py` | `tests/test_run_agent_graph_memory.py` |  | `test_confidence_components_are_persisted`; `test_confidence_varies_by_source_kind` | `planned` |
| `GM-SCORE-002` | Candidate/active thresholds do not collapse all durable nodes | `new` | `agent/graph_memory/scoring.py`, `agent/graph_memory/manager.py` | `tests/sparkgraph/evals/fixtures/*` | `tests/sparkgraph/test_scoring.py`, `tests/sparkgraph/evals/test_eval_metrics.py` | `tests/test_run_agent_graph_memory.py` |  | `test_candidate_path_does_not_filter_all_durable_nodes`; `test_active_path_preserves_explicit_and_manual_nodes` | `planned` |
| `GM-EVAL-005` | End-to-end system-quality evaluation gate | `new` | `tests/sparkgraph/evals/test_system_quality_eval.py` | `agent/graph_memory/scoring.py`, `agent/graph_memory/manager.py`, `tests/sparkgraph/evals/fixtures/*` | `tests/sparkgraph/evals/test_system_quality_eval.py` | `tests/test_run_agent_graph_memory.py` |  | `test_system_quality_eval_fails_when_all_filtered_out`; `test_system_quality_eval_detects_confidence_collapse`; `test_system_quality_eval_requires_nonempty_recall_on_durable_fixture_set` | `planned` |

---

## 4. 开发约束

实现过程中必须满足：

1. 未出现在本矩阵里的 feature，不进入本轮开发。
2. 每新增或修改一个 `Feature ID`，必须同步更新本矩阵状态。
3. PR 描述必须列出：
   - 变更的 `Feature ID`
   - 新增测试
   - 必跑回归测试
4. 若某 feature 暂不做测试，必须在矩阵中明确记录原因，不能空着。
5. 所有 SparkGraph 路径与命名空间相关改动，必须引用 `GM-CFG-005`，禁止绕开 Hermes 官方 `HERMES_HOME/profile` 模型自行引入新的一等命名空间。
