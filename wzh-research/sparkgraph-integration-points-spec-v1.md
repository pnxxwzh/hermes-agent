# SparkGraph 接入点规格说明（Integration Points Spec v1）

> 目的：把 SparkGraph 的源码级整合，从“模块级/架构级”进一步压到“文件级/函数级/生命周期级”。  
> 约束：  
> 1. 不修改参考代码目录 `wzh-research/graph-memory/`  
> 2. 尽量解耦  
> 3. 必须走 Hermes 正式注册点  
> 4. 主线文件只放桥接逻辑，不放 SparkGraph 核心逻辑

## 1. 适用范围

本文只描述 SparkGraph 与 Hermes 的正式接入点，不重复描述 SparkGraph 核心模块内部实现。

核心模块设计见：
- `wzh-research/sparkgraph-sds-v1.md`

---

## 2. 接入原则

每个接入点都必须满足：

1. 只承担注册和桥接职责
2. 不实现 SparkGraph 核心判断逻辑
3. 不复制核心逻辑到多个 Hermes 主线文件
4. 能被单独 mock / stub / integration test

---

## 3. 接入点总览

```text
Hermes Mainline Integration Points
  ├─ A. Config bootstrap
  ├─ B. Runtime provider resolution
  ├─ C. Agent init
  ├─ D. Pre-LLM dynamic recall injection
  ├─ E. Post-turn ingestion pipeline
  ├─ F. Flush / session_end finalize
  ├─ G. Gateway startup & shutdown
  ├─ H. Setup / reconfigure / restore / migration
  ├─ I. Status / doctor / health
  └─ J. Tools surface registration
```

---

## 4. 文件级接入点

## 4.1 `hermes_cli/config.py`

### 角色

SparkGraph 顶层配置的默认值、迁移、回填入口。

### 允许改动

1. `DEFAULT_CONFIG`
   - 新增 `graph_memory` 顶层块
2. 配置加载后的 backfill / migration helper
3. config save/restore 所需的 SparkGraph 兼容字段处理

### 禁止改动

1. 不在这里做 runtime 实际调用
2. 不在这里做 SparkGraph 业务逻辑

### 需要新增的函数

建议新增：

1. `_default_graph_memory_config() -> dict`
2. `_backfill_graph_memory_config(cfg: dict) -> dict`
3. `_normalize_graph_memory_db_path(cfg: dict) -> dict`

### 必测

1. 默认配置存在完整 `graph_memory` 块
2. 旧配置能自动补齐
3. `db_path=""` 时正确按当前 profile 解析
4. restore defaults 不影响非 SparkGraph 配置

---

## 4.2 `hermes_cli/runtime_provider.py`

### 角色

为 SparkGraph 的各子 runtime 提供正式 provider resolution。

### 允许改动

1. 新增 SparkGraph runtime 解析入口
2. 与现有 model/auxiliary provider 逻辑保持风格一致

### 建议新增函数

1. `resolve_sparkgraph_reflection_runtime(...)`
2. `resolve_sparkgraph_extraction_runtime(...)`
3. `resolve_sparkgraph_maintenance_runtime(...)`
4. `resolve_sparkgraph_embedding_runtime(...)`

### 输出要求

每个 resolver 至少返回：

1. `provider`
2. `model`
3. `base_url`
4. `api_key`
5. `timeout`
6. `resolved_from`

### 必测

1. `provider=auto`
2. `provider=main`
3. custom endpoint
4. embedding runtime 拒绝明显不支持 embedding 的配置
5. 缺失 provider 时 structured fallback

---

## 4.3 `hermes_cli/setup.py`

### 角色

SparkGraph 进入正式 setup / reconfigure / restore 生命周期。

### 允许改动

1. 在 setup 流程中增加 SparkGraph section
2. 增加 rerun setup 的 SparkGraph 分支
3. 增加 restore SparkGraph defaults
4. 增加 post-setup probe 调用

### 建议新增函数

1. `_setup_sparkgraph(cfg, env)`
2. `_setup_sparkgraph_runtime(kind, current_cfg, env)`
3. `_restore_sparkgraph_defaults(cfg)`
4. `_probe_sparkgraph_after_setup(cfg, env)`

### 交互要求

1. 不问“是否启用”
2. 只问 runtime 如何配置
3. 支持：
   - Keep current
   - Use main provider/runtime
   - Use auto
   - Custom endpoint

### 必测

1. 初次 setup 写入完整配置
2. rerun setup 保留未修改字段
3. restore defaults 正确回退
4. probe 失败时给正式错误提示

---

## 4.4 `run_agent.py`

### 角色

SparkGraph 在 agent 生命周期中的主桥接点。

### 允许改动的生命周期

#### A. Agent init

目标：

1. 初始化 SparkGraph manager
2. 读取当前 config/profile/runtime
3. 在不阻断主 agent 初始化的前提下完成 SparkGraph 初始就绪

建议新增薄桥接：

1. `_init_sparkgraph(...)`

#### B. Pre-LLM dynamic injection

目标：

1. 在构造本轮最终请求前，插入 SparkGraph recall block
2. 只进动态层，不动 cached prompt

建议新增薄桥接：

1. `_build_sparkgraph_turn_context(...)`

#### C. Post-turn pipeline

目标：

1. turn 完成后，触发 SparkGraph candidate extraction/classification/dedup 流程
2. 不拖垮主回复

建议新增薄桥接：

1. `_run_sparkgraph_post_turn(...)`

#### D. Flush/session_end

目标：

1. flush / session_end 前后触发 SparkGraph finalize / maintenance
2. 失败时降级，不阻断 Hermes flush

建议新增薄桥接：

1. `_flush_sparkgraph(...)`

### 禁止改动

1. 不把 classifier/dedup/scoring 内联进 `run_agent.py`
2. 不让 `run_agent.py` 直接操作 SQLite
3. 不在这里手搓 provider 解析

### 必测

1. recall block 不污染 cached prompt
2. SparkGraph 失败不阻断主聊天
3. post-turn timeout 安全降级
4. flush finalize 失败不影响 Hermes flush

---

## 4.5 `gateway/run.py`

### 角色

SparkGraph 在 gateway 生命周期中的桥接点。

### 允许改动

1. 启动时 SparkGraph runtime probe / health init
2. 关闭时 SparkGraph finalize / shutdown hooks
3. dream / background job runner 的宿主注册

### 建议新增函数

1. `_init_sparkgraph_gateway_state(...)`
2. `_shutdown_sparkgraph_gateway_state(...)`

### 必测

1. gateway startup 健康检查展示 SparkGraph 状态
2. shutdown 不因 SparkGraph 超时而卡死
3. background dream job 失败不影响 gateway 主流程

---

## 4.6 `gateway/status.py`

### 角色

展示 SparkGraph runtime / qualification / degraded 状态。

### 允许改动

1. health data 展示
2. SparkGraph qualification 状态展示
3. runtime degraded 状态展示

### 必测

1. qualified / unverified / restricted 显示正确
2. embedding degraded 显示正确
3. SparkGraph 缺配置时状态友好而非崩溃

---

## 4.7 `tools/__init__.py` 与 `tools/graph_memory_tools.py`

### 角色

注册 SparkGraph 工具表面。

### 初版建议工具

1. `graph_search`
2. `graph_record`
3. `graph_stats`
4. `graph_maintain`

### 允许改动

1. 工具注册
2. 结构化错误桥接

### 禁止改动

1. 不在工具层重复实现核心 store/scoring 逻辑

### 必测

1. 每个工具的成功路径
2. runtime 未就绪时 structured error
3. no results / timeout / degraded fallback

---

## 4.8 `agent/auxiliary_client.py`

### 角色

如果 SparkGraph 继续沿用 Hermes 的 auxiliary runtime 模式或兼容其调用风格，这里是桥接层。

### 允许改动

1. SparkGraph side-task runtime dispatch
2. timeout/defaults/fallback 兼容

### 必测

1. SparkGraph task route 正确命中
2. provider 缺失时 fallback 正确
3. timeout 不污染其他 auxiliary 任务

---

## 5. 核心模块与接入层的边界

## 5.1 Core layer 文件

以下文件承载真实核心逻辑：

1. `agent/graph_memory/types.py`
2. `agent/graph_memory/config.py`
3. `agent/graph_memory/db.py`
4. `agent/graph_memory/store.py`
5. `agent/graph_memory/extractor.py`
6. `agent/graph_memory/classifier.py`
7. `agent/graph_memory/dedup.py`
8. `agent/graph_memory/scoring.py`
9. `agent/graph_memory/recaller.py`
10. `agent/graph_memory/formatter.py`
11. `agent/graph_memory/maintenance.py`
12. `agent/graph_memory/manager.py`
13. `agent/graph_memory/runtime.py`

## 5.2 Integration layer 文件

以下文件只允许薄桥接：

1. `run_agent.py`
2. `gateway/run.py`
3. `gateway/status.py`
4. `hermes_cli/config.py`
5. `hermes_cli/setup.py`
6. `hermes_cli/runtime_provider.py`
7. `tools/__init__.py`
8. `tools/graph_memory_tools.py`
9. 视情况 `agent/auxiliary_client.py`

---

## 6. 测试覆盖要求

## 6.1 接入点测试必须一一对应

每个接入点都必须有：

1. 单测或模块测试
2. 集成测试
3. 至少一个 failure/timeout 测试

## 6.2 覆盖清单

### `hermes_cli/config.py`

1. 默认 config
2. migration/backfill
3. profile path normalization

### `hermes_cli/runtime_provider.py`

1. reflection runtime resolve
2. extraction runtime resolve
3. maintenance runtime resolve
4. embedding runtime resolve

### `hermes_cli/setup.py`

1. setup create
2. rerun setup
3. restore defaults
4. probe after setup

### `run_agent.py`

1. init bridge
2. pre-LLM injection
3. post-turn bridge
4. flush bridge
5. cached prompt isolation

### `gateway/run.py`

1. startup health init
2. shutdown finalize
3. dream/background job safety

### `gateway/status.py`

1. qualification state rendering
2. degraded rendering

### `tools`

1. success path
2. no result
3. runtime down
4. timeout

---

## 7. 当前还未细化到函数体的部分

这里明确承认目前仍有待细化点：

1. `run_agent.py` 里的确切插入行位和函数名还需结合实现时代码上下文确认
2. `setup.py` 里的交互 UI 文案顺序还需和现有 wizard 结构贴合
3. `gateway/run.py` 里是否已有现成 background job 宿主可复用，还需实现前再核

这些不是设计缺失，而是“必须在改代码时结合真实主线版本确认的薄接入细节”。

---

## 8. 设计验收标准

本文达到可执行颗粒度的标准是：

1. 能明确说出每个 Hermes 主线文件为什么需要改
2. 能明确说出每个文件只允许承载什么职责
3. 能为每个接入点列出测试覆盖
4. 没有任何一个 SparkGraph 核心逻辑必须被迫塞进 Hermes 主线文件

如果后续实现发现某个接入点无法满足这些标准，必须先更新本文，再改代码。
