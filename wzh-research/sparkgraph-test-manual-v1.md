# SparkGraph v2 调测与验收手册

> 适用范围：当前 `SparkGraph v2` 主线实现  
> 仓库：`/Users/wzh/IsacHermes`  
> 当前主线：主模型 `flush` 抽取 + SparkGraph DB / dedup / scoring / recall + embedding 不可用时降级到 FTS5  
> 不包含：`shadow extractor`、任何小模型旁路主链

---

## 1. 目的

这份手册用于覆盖 SparkGraph v2 从安装、启动、配置、功能验证到回归验收的完整流程。

目标：

1. 提供一套可重复执行的调测步骤
2. 覆盖所有当前已实现的 SparkGraph 改动点
3. 明确每一步的触发方法、预期现象和通过标准
4. 记录当前已知的安装环境问题与规避方法

---

## 1.1 当前手工验收记录

> 当前线程手工验收进度

- [x] Step 1: `hermes status` 中 SparkGraph 区块正常显示
- [x] Step 2: `hermes doctor` 中 SparkGraph 诊断正常显示
- [x] Step 3: `hermes status --deep` 显式 live probe
- [x] Step 4: `hermes doctor --probe` 显式 live probe
- [x] Step 5: `hermes setup sparkgraph` 可进入专属设置页
- [x] Step 5.1: `Reconfigure SparkGraph` 覆盖 recall / db_path / embedding，并可成功 probe
- [x] Step 5.2: `Restore SparkGraph defaults` 可恢复 recall / embedding 默认值
- [x] Step 6.1: `fact -> flush -> graph write -> recall` 集成验证通过
- [x] Step 6.2: `greeting -> flush -> no graph write` 集成验证通过
- [x] Step 7: 显式查询工具手工验收
- [ ] Step 8: 主模型 flush eval 手工验收（当前默认 openai-codex runtime 下会被权限页拦截）

---

## 2. 当前范围

### 2.1 已实现范围

SparkGraph 当前已实现：

1. profile 级 SQLite 存储
2. `sg_nodes / sg_edges / sg_evidence / sg_vectors / sg_nodes_fts`
3. DB 级 `CHECK` 约束
4. evidence append
5. dedup / scoring / `candidate -> active -> deprecated`
6. `sparkgraph_record`
7. `sparkgraph_search`
8. `sparkgraph_stats`
9. `flush_memories()` 集成知识抽取
10. flush 对齐轻量 maintenance
11. recall 检索、格式化、动态注入
12. embedding 不可用时降级到 FTS5
13. `setup / status / doctor / live probe`
14. 主模型 flush eval

### 2.2 明确不在本手册范围内

以下内容已移除或不属于当前主线：

1. `shadow extractor`
2. 小模型 qualification gate
3. 小模型前置分类主链
4. 任何小模型自动回退机制

---

## 3. 安装前提

### 3.1 基础要求

1. macOS / Linux 环境
2. Python `>= 3.11`
3. `uv`
4. 当前仓库可读写

### 3.2 重要说明：没有单独“编译产物”

Hermes / SparkGraph 是 Python 项目。

所以这里的“编译/构建验证”指的是：

1. 可创建虚拟环境
2. 可完成 editable install
3. CLI 可启动
4. 关键模块可导入

---

## 4. 已知安装问题

### 4.1 `.[all,dev]` 当前不建议作为 SparkGraph 调测安装命令

真实问题：

```text
python-olm==3.2.16 build failed
```

根因：

1. `hermes-agent[all]` 会拉入 `matrix-nio[e2e]`
2. `matrix-nio[e2e]` 依赖 `python-olm`
3. 当前环境中 `python-olm` 的 CMake 构建失败

这不是 SparkGraph 本身需要的依赖，而是 `matrix` extra 带进来的。

### 4.2 推荐安装命令

对于 SparkGraph 调测，**不要用**：

```bash
uv pip install -e ".[all,dev]"
```

推荐使用：

```bash
uv pip install -e ".[dev,cli,cron,pty,honcho,mcp]"
```

说明：

1. 这组 extra 足够覆盖当前 SparkGraph 调测所需的：
   - 测试
   - CLI
   - setup/status/doctor
   - profile/runtime 相关路径
2. 它不会拉入 `matrix-nio[e2e]`
3. 因此可以绕开 `python-olm` 构建失败

### 4.3 如果必须安装 `all`

这不属于 SparkGraph 调测必需路径。若未来必须安装 `all`，需要单独解决：

1. CMake 版本/兼容策略
2. `python-olm` 构建环境

当前本手册不把这条路径作为通过标准。

---

## 5. 安装与构建验证

### 5.1 创建虚拟环境

命令：

```bash
cd /Users/wzh/IsacHermes
uv venv .venv --python 3.11
source .venv/bin/activate
```

预期现象：

1. `.venv/` 创建成功
2. 激活后 `python --version` 为 `3.11.x`

### 5.2 安装依赖

命令：

```bash
uv pip install -e ".[dev,cli,cron,pty,honcho,mcp]"
```

预期现象：

1. 安装成功
2. 不出现 `python-olm` 构建失败

通过标准：

1. `hermes` 命令可执行
2. 无 import error

### 5.3 CLI 启动验证

命令：

```bash
hermes --help
hermes status --help
hermes doctor --help
```

预期现象：

1. 正常显示帮助
2. 不报模块导入错误

### 5.4 模块导入验证

命令：

```bash
python - <<'PY'
import run_agent
import agent.sparkgraph
import tools.sparkgraph_tool
print("ok")
PY
```

预期现象：

1. 输出 `ok`

---

## 6. 配置与 CLI 测试

### 6.1 `hermes setup sparkgraph`

触发方法：

```bash
hermes setup sparkgraph
```

预期现象：

1. 可以进入 SparkGraph section
2. 可见动作：
   - `Keep current settings`
   - `Reconfigure SparkGraph`
   - `Restore SparkGraph defaults`
3. 可以配置：
   - recall 开关与限制
   - `db_path`
   - embedding runtime
4. 若 `db_path` 指向 profile 外路径，会看到 warning

覆盖功能：

1. `SG2-CFG-002`
2. `SG2-CFG-003`

### 6.2 `hermes status`

触发方法：

```bash
hermes status
```

预期现象：

1. 显示 SparkGraph section
2. 至少显示：
   - mode
   - db path
   - recall limits
   - embedding 状态
   - 最近一次 flush eval 结果（若存在）

### 6.3 `hermes status --deep`

触发方法：

```bash
hermes status --deep
```

预期现象：

1. 在普通 `status` 基础上做显式 live probe
2. 若 embedding endpoint 不可达，会显示 degraded / failed 信息

### 6.4 `hermes doctor`

触发方法：

```bash
hermes doctor
```

预期现象：

1. 做本地诊断，不默认探网
2. 检查：
   - SparkGraph config 可解析
   - db parent
   - profile 路径
   - 最近一次 flush eval 结果

### 6.5 `hermes doctor --probe`

触发方法：

```bash
hermes doctor --probe
```

预期现象：

1. 在普通 `doctor` 基础上做显式 runtime probe
2. 不会影响 Hermes 主配置

---

## 7. 数据库与存储测试

### 7.1 自动化测试

命令：

```bash
python -m pytest -q tests/sparkgraph/test_db.py tests/sparkgraph/test_store.py
```

预期现象：

1. 用例通过
2. 覆盖：
   - schema 创建
   - `_migrations`
   - `sg_nodes`
   - `sg_edges`
   - `sg_evidence`
   - `sg_vectors`
   - `sg_nodes_fts`
   - evidence append
   - FTS 检索

### 7.2 关键通过标准

1. 非法 `type/status/source_kind` 无法写入
2. migration 幂等
3. evidence append 不会无故重复造节点

---

## 8. flush 主链测试

### 8.1 自动化测试

命令：

```bash
python -m pytest -q tests/test_flush_memories_codex.py tests/integration/test_sparkgraph_flush_flow.py
```

预期现象：

1. `fact -> flush -> graph write -> recall`
2. `preference -> flush -> graph write -> recall`
3. `greeting -> flush -> no graph write`
4. `flush extraction failure -> chat safe`
5. `memory flush and sparkgraph flush coexist`

### 8.2 人工调测建议

操作：

1. 先进行一段包含 durable knowledge 的对话
2. 再触发一条能进入 `flush_memories()` 的路径
3. 然后问一个相关追问

预期现象：

1. flush 阶段主模型可调用 `sparkgraph_record`
2. 不新增额外主模型调用
3. flush 结束后知识点可进入 SparkGraph
4. 后续 query 可命中 recall

---

## 9. recall 测试

### 9.1 自动化测试

命令：

```bash
python -m pytest -q tests/sparkgraph/test_recaller.py tests/sparkgraph/test_formatter.py tests/test_run_agent.py -k sparkgraph
```

预期现象：

1. recall 只召回符合门槛的 `active`
2. `deprecated` 不召回
3. 低 `stability` active 不召回
4. 空 recall 返回空 block，不报错
5. recall block 不进 cached prompt
6. recall block 不持久化到 session history

### 9.2 核心通过标准

1. 真实发给模型的 prompt 中，不含 `deprecated`
2. 真实发给模型的 prompt 中，不含低稳定度 `active`
3. 无节点匹配时系统安全退空

---

## 10. 降级与失败测试

### 10.1 embedding 降级到 FTS5

命令：

```bash
python -m pytest -q tests/sparkgraph/test_runtime.py tests/sparkgraph/test_recaller.py
```

预期现象：

1. embedding 不可用时，不中断主链
2. recall 自动退到 FTS5
3. 召回质量可能下降，但系统可用

### 10.2 flush 失败安全性

命令：

```bash
python -m pytest -q tests/test_flush_memories_codex.py tests/integration/test_sparkgraph_flush_flow.py -k failure
```

预期现象：

1. SparkGraph 写入失败不影响 Hermes 主回复
2. 本轮只跳过 SparkGraph

---

## 11. 显式工具测试

### 11.1 自动化测试

命令：

```bash
python -m pytest -q tests/tools/test_sparkgraph_record_tool.py tests/tools/test_sparkgraph_query_tools.py
```

预期现象：

1. `sparkgraph_record` 正常
2. `sparkgraph_search` 正常
3. `sparkgraph_stats` 正常

### 11.2 关键通过标准

1. `sparkgraph_search` 可返回节点列表
2. `sparkgraph_stats` 可返回统计信息
3. 显式查询可见 `candidate`
4. 动态 recall 仍只面向 `active`

---

## 12. 主模型 flush 抽取评测

### 12.1 脚本运行

命令：

```bash
python /Users/wzh/IsacHermes/scripts/sparkgraph_flush_eval.py --compare-last
```

触发方法：

1. 读取当前 Hermes `model.default`
2. 读取当前 active runtime
3. 跑固定 flush fixtures

预期现象：

1. 输出通过率
2. 写结果到当前 profile 的：
   - `sparkgraph/evals/flush-last.json`
3. 若已有上次结果，会显示最小对比
4. 若评测失败，退出码非零

### 12.2 自动化测试

命令：

```bash
python -m pytest -q tests/sparkgraph/evals/test_flush_extraction_eval.py tests/sparkgraph/evals/test_flush_eval_script.py
```

预期现象：

1. fixtures 结构合法
2. runtime 解析逻辑正确
3. report 写入成功
4. compare-last 工作正常
5. 失败时主脚本返回非零退出码

---

## 13. CLI / 产品面宽回归

命令：

```bash
python -m pytest -q \
  tests/hermes_cli/test_config.py \
  tests/hermes_cli/test_setup.py \
  tests/hermes_cli/test_status.py \
  tests/hermes_cli/test_doctor.py \
  tests/hermes_cli/test_sparkgraph_setup.py \
  tests/hermes_cli/test_sparkgraph_runtime_status.py \
  tests/hermes_cli/test_gateway_runtime_health.py \
  tests/hermes_cli/test_tools_config.py
```

预期现象：

1. SparkGraph 接入不破坏 Hermes 现有 CLI
2. setup/status/doctor 逻辑全绿

---

## 14. 相关主链宽回归

命令：

```bash
python -m pytest -q \
  tests/sparkgraph \
  tests/integration/test_sparkgraph_flush_flow.py \
  tests/test_run_agent.py \
  tests/test_flush_memories_codex.py \
  tests/tools/test_registry.py \
  tests/tools/test_memory_tool.py \
  tests/tools/test_session_search.py
```

预期现象：

1. SparkGraph 核心与 Hermes 主链相关区域通过

---

## 15. 最终验收标准

以下全部满足，才算 SparkGraph v2 调测通过：

1. 可完成安装，不依赖 `.[all,dev]`
2. `hermes setup sparkgraph` 可配置、重配、恢复默认
3. SparkGraph DB 可在当前 profile 下初始化
4. 主模型可在 flush 阶段写入 SparkGraph
5. `greeting` 不应写入 durable knowledge
6. 后续 query 可命中 recall
7. recall 不污染 cached prompt
8. embedding 不可用时可降级到 FTS5
9. `sparkgraph_search` / `sparkgraph_stats` 可用
10. `sparkgraph_flush_eval.py` 可跑、可落盘、可 compare-last
11. 相关自动化测试全部通过

---

## 16. 当前已知非阻塞问题

1. [/Users/wzh/IsacHermes/tests/conftest.py](/Users/wzh/IsacHermes/tests/conftest.py) 仍会触发 event loop `DeprecationWarning`
2. 该问题不影响 SparkGraph 主链调测和阶段验收
3. `scripts/sparkgraph_flush_eval.py` 在当前默认 `openai-codex` runtime（`https://chatgpt.com/backend-api/codex`）下会被权限/Cloudflare 页面拦截，当前不能作为默认 runtime 的直接验收入口
4. 对本地 OpenAI 兼容服务运行 `scripts/sparkgraph_flush_eval.py` 时，应使用：
   - `--provider custom`
   - 或 `--provider openai-compatible`（当前脚本会归一化到 `custom`）

---

## 17. 推荐执行顺序

建议按以下顺序执行：

1. 安装与 CLI 启动验证
2. setup / status / doctor
3. DB / store
4. flush 主链
5. recall
6. 降级与失败
7. 显式工具
8. flush eval
9. CLI 宽回归
10. 主链宽回归

这样能最快定位问题，并避免把环境问题误判成功能问题。
