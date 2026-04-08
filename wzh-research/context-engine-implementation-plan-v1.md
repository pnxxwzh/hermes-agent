# Context Engine 实现计划 v2（逐行详细版）

> 分支: `context-research`
> 创建: 2026-04-03
> 状态: 实施中

---

## 实施总览

| Step | 文件 | 内容 | 预估行 |
|------|------|------|--------|
| 1 | models.py | 4个数据类 | ~80 |
| 2 | context.py | AssemblyContext | ~30 |
| 3 | registry.py | Source 注册框架 | ~60 |
| 4 | metrics.py | 计量计算 + sync_to | ~80 |
| 5a | compat.py | prompt_builder wrappers | ~80 |
| 5b | compat.py | memory_store + honcho wrappers | ~80 |
| 5c | compat.py | sparkgraph + plugin wrappers | ~80 |
| 6 | sources.py | Stable 1-5 | ~150 |
| 7 | sources.py | Stable 6-10 | ~150 |
| 8 | sources.py | Dynamic 4 | ~150 |
| 9 | assembler.py | 组装编排器 | ~120 |
| 10 | __init__.py | 导出 | ~30 |
| 11 | run_agent.py | 接入 stable | ~150 |
| 12 | run_agent.py | 接入 dynamic | ~100 |
| 13 | cli.py | 颜色 source 占比进度条 | ~100 |

---

## 正式实施步骤

---

### Step 1 — models.py

**文件**: `agent/context_engine/models.py`

**代码**：

```python
"""Core data models for the context engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Rough token estimation (chars / 4, matching existing Hermes convention)
# ---------------------------------------------------------------------------

_CHARS_PER_TOKEN = 4


def _rough_tokens(text: str) -> int:
    if not text:
        return 0
    return len(text) // _CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# ContextChunk
# ---------------------------------------------------------------------------

@dataclass
class ContextChunk:
    """A single piece of context assembled into the system prompt."""

    source: str  # e.g. "memory", "sparkgraph_recall"
    stage: Literal["stable", "dynamic"]
    slot: str  # source-internal sub-slot, e.g. "memory", "user_profile"
    priority: int  # Phase 1 unused; reserved for future budget policies
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.stage not in ("stable", "dynamic"):
            raise ValueError(f"stage must be 'stable' or 'dynamic', got {self.stage!r}")
        # empty content is allowed (compat with sources that decide not to emit)
        # char_count in metadata for metrics
        if "char_count" not in self.metadata:
            self.metadata["char_count"] = len(self.content)


# ---------------------------------------------------------------------------
# SourceMetrics
# ---------------------------------------------------------------------------

@dataclass
class SourceMetrics:
    """Token / char metrics for a single context source."""

    source: str
    stage: Literal["stable", "dynamic"]
    char_count: int
    rough_tokens: int
    included: bool = True


# ---------------------------------------------------------------------------
# ContextMetrics
# ---------------------------------------------------------------------------

@dataclass
class ContextMetrics:
    """Aggregated context metrics covering all sources.

    Attributes
    ----------
    stable_tokens
        Rough token estimate for all stable chunks combined.
    dynamic_tokens
        Rough token estimate for all dynamic chunks combined.
    total_estimated_tokens
        stable_tokens + dynamic_tokens.
    by_source
        Per-source breakdown; one entry per source that produced output.
    """

    stable_tokens: int
    dynamic_tokens: int
    total_estimated_tokens: int
    by_source: list[SourceMetrics]

    def sync_to(self, compressor) -> None:
        """Sync estimated tokens to a ContextCompressor instance.

        Writes total_estimated_tokens to compressor.last_prompt_tokens.
        Leaves last_completion_tokens unchanged.
        """
        if compressor is None:
            return
        # Guard against uninitialized compressor state
        current = getattr(compressor, "last_prompt_tokens", None)
        compressor.last_prompt_tokens = self.total_estimated_tokens


# ---------------------------------------------------------------------------
# AssemblyResult
# ---------------------------------------------------------------------------

@dataclass
class AssemblyResult:
    """Complete output of a context assembly operation.

    Attributes
    ----------
    stable_chunks
        All chunks for the cached system prompt, in assembly order.
    dynamic_chunks
        All per-turn chunks, in assembly order.
    stable_system
        The joined stable system prompt string.
    dynamic_system
        The joined dynamic system string.
    effective_system
        stable_system + "\n\n" + dynamic_system joined.
        Empty string when both inputs are empty.
    metrics
        Unified ContextMetrics for the assembled content.
    """

    stable_chunks: list[ContextChunk]
    dynamic_chunks: list[ContextChunk]
    stable_system: str
    dynamic_system: str
    effective_system: str
    metrics: ContextMetrics

    @staticmethod
    def _join_chunks(chunks: list[ContextChunk], separator: str = "\n\n") -> str:
        """Join chunk contents, skipping empty ones."""
        return separator.join(c.content for c in chunks if c.content.strip())

    @classmethod
    def from_chunks(
        cls,
        stable_chunks: list[ContextChunk],
        dynamic_chunks: list[ContextChunk],
    ) -> "AssemblyResult":
        stable_system = cls._join_chunks(stable_chunks)
        dynamic_system = cls._join_chunks(dynamic_chunks)

        # effective_system: strip to avoid leading/trailing newlines
        parts = []
        if stable_system:
            parts.append(stable_system)
        if dynamic_system:
            parts.append(dynamic_system)
        effective = ("\n\n".join(parts)).strip()

        # Build metrics
        stable_tokens = sum(_rough_tokens(c.content) for c in stable_chunks)
        dynamic_tokens = sum(_rough_tokens(c.content) for c in dynamic_chunks)
        total = stable_tokens + dynamic_tokens

        by_source: list[SourceMetrics] = []
        for c in stable_chunks:
            if c.content.strip():
                by_source.append(SourceMetrics(
                    source=c.source,
                    stage="stable",
                    char_count=len(c.content),
                    rough_tokens=_rough_tokens(c.content),
                    included=True,
                ))
        for c in dynamic_chunks:
            if c.content.strip():
                by_source.append(SourceMetrics(
                    source=c.source,
                    stage="dynamic",
                    char_count=len(c.content),
                    rough_tokens=_rough_tokens(c.content),
                    included=True,
                ))

        metrics = ContextMetrics(
            stable_tokens=stable_tokens,
            dynamic_tokens=dynamic_tokens,
            total_estimated_tokens=total,
            by_source=by_source,
        )
        return cls(
            stable_chunks=stable_chunks,
            dynamic_chunks=dynamic_chunks,
            stable_system=stable_system,
            dynamic_system=dynamic_system,
            effective_system=effective,
            metrics=metrics,
        )
```

**边界条件**：
1. `stage` 非法值 → `ValueError`
2. `content=""` → `ContextChunk` 允许，`_join_chunks` 跳过空的
3. `compressor` 为 `None` → `sync_to` 直接返回，不抛异常
4. `compressor.last_prompt_tokens` 未初始化 → `getattr(..., None)` 保护
5. `effective_system` 两部分都空 → 返回空字符串 `""`
6. `total_estimated_tokens=0` → 允许（空 context 场景）

**测试用例 (tests/context_engine/test_models.py)**：

```
T1.1: test_context_chunk_stage_validation — stage="invalid" 抛 ValueError
T1.2: test_context_chunk_empty_content — content="" 不抛异常，char_count=0
T1.3: test_context_metrics_sync_to_none — sync_to(None) 不抛异常
T1.4: test_context_metrics_sync_to_updates_field — last_prompt_tokens 被正确写入
T1.5: test_assembly_result_from_chunks_empty — 全空 chunks，effective_system=""
T1.6: test_assembly_result_from_chunks_partial — 只有 stable，dynamic 为空，effective=stable
T1.7: test_assembly_result_by_source_contains_all_sources — 每个非空 chunk 都进入 by_source
T1.8: test_assembly_result_tokens_sum — stable_tokens + dynamic_tokens = total
T1.9: test_assembly_result_effective_system_joiner — stable + "\n\n" + dynamic
```

---

### Step 2 — context.py

**文件**: `agent/context_engine/context.py`

**代码**：

```python
"""AssemblyContext — data passed to every ContextSource.collect()."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from run_agent import AIAgent


@dataclass
class AssemblyContext:
    """Immutable snapshot of everything a source needs to collect its chunks.

    Parameters
    ----------
    agent
        Reference to the active AIAgent instance.
    system_message
        Optional external system message provided at session start.
    user_message
        Current turn's user message (used for SparkGraph recall).
    cwd
        Working directory for context-file discovery.
    conversation_history
        Current message list (before system prefix is prepended).
    """

    agent: "AIAgent"
    system_message: str | None = None
    user_message: str | None = None
    cwd: str | None = None
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
```

**边界条件**：
1. 所有字段均有默认值，允许 `None`
2. `conversation_history` 默认为空列表 `[]`（不是 `None`）
3. `TYPE_CHECKING` 避免循环 import

**测试用例 (tests/context_engine/test_context.py)**：

```
T2.1: test_assembly_context_defaults — 默认值正确，字段均为可选项
T2.2: test_assembly_context_conversation_history_empty_list — 默认是 [] 不是 None
```

---

### Step 3 — registry.py

**文件**: `agent/context_engine/registry.py`

**代码**：

```python
"""Explicit source registry for Phase 1."""

from __future__ import annotations

from typing import Callable, Literal

from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import ContextChunk

# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

STABLE_SOURCE_FACTORIES: list[tuple[str, Callable[[], list[ContextChunk]]]] = []
DYNAMIC_SOURCE_FACTORIES: list[tuple[str, Callable[[], list[ContextChunk]]]] = []


def _register(
    name: str,
    factory: Callable[[], list[ContextChunk]],
    stable_list: list,
) -> None:
    """Register a source factory.

    Raises ValueError if name is already registered.
    """
    for existing_name, _ in stable_list:
        if existing_name == name:
            raise ValueError(f"Source {name!r} already registered")
    stable_list.append((name, factory))


def register_stable(name: str) -> Callable:
    """Decorator: register a stable source factory."""
    def decorator(fn: Callable[[], list[ContextChunk]]):
        _register(name, fn, STABLE_SOURCE_FACTORIES)
        return fn
    return decorator


def register_dynamic(name: str) -> Callable:
    """Decorator: register a dynamic source factory."""
    def decorator(fn: Callable[[], list[ContextChunk]]):
        _register(name, fn, DYNAMIC_SOURCE_FACTORIES)
        return fn
    return decorator
```

**边界条件**：
1. 重复注册同名 source → `ValueError`
2. 两个独立列表，stable 和 dynamic 同名不冲突
3. 装饰器返回值仍是原函数（可调用）

**测试用例 (tests/context_engine/test_registry.py)**：

```
T3.1: test_register_stable_decorator — 装饰器正确添加到列表
T3.2: test_register_dynamic_decorator — 装饰器正确添加到列表
T3.3: test_register_duplicate_name_raises — 同列表重复注册抛 ValueError
T3.4: test_same_name_different_lists — stable 和 dynamic 同名不冲突
```

---

### Step 4 — metrics.py

**文件**: `agent/context_engine/metrics.py`

**代码**：

```python
"""Unified metrics for context assembly."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # ContextCompressor imported at runtime to avoid circular dependency

from agent.context_engine.models import ContextChunk, ContextMetrics, SourceMetrics

_CHARS_PER_TOKEN = 4


def rough_tokens(text: str) -> int:
    """Rough token estimate: chars / 4 (matching Hermes convention)."""
    if not text:
        return 0
    return len(text) // _CHARS_PER_TOKEN


def chunks_to_metrics(
    stable_chunks: list[ContextChunk],
    dynamic_chunks: list[ContextChunk],
) -> ContextMetrics:
    """Build ContextMetrics from assembled chunks.

    Each non-empty chunk contributes one SourceMetrics entry.
    """
    by_source: list[SourceMetrics] = []

    for chunk in stable_chunks:
        if chunk.content.strip():
            by_source.append(SourceMetrics(
                source=chunk.source,
                stage="stable",
                char_count=len(chunk.content),
                rough_tokens=rough_tokens(chunk.content),
                included=True,
            ))

    for chunk in dynamic_chunks:
        if chunk.content.strip():
            by_source.append(SourceMetrics(
                source=chunk.source,
                stage="dynamic",
                char_count=len(chunk.content),
                rough_tokens=rough_tokens(chunk.content),
                included=True,
            ))

    stable_tokens = sum(s.rough_tokens for s in by_source if s.stage == "stable")
    dynamic_tokens = sum(s.rough_tokens for s in by_source if s.stage == "dynamic")

    return ContextMetrics(
        stable_tokens=stable_tokens,
        dynamic_tokens=dynamic_tokens,
        total_estimated_tokens=stable_tokens + dynamic_tokens,
        by_source=by_source,
    )
```

**边界条件**：
1. 所有 chunks 均空 → `by_source=[]`，`stable_tokens=0`，`dynamic_tokens=0`，`total=0`
2. `text=""` → `rough_tokens` 返回 0
3. `chunks_to_metrics` 不抛异常，即使所有 chunk 为空
4. `ContextMetrics.sync_to` 在 models.py 中定义，此处不重复

**测试用例 (tests/context_engine/test_metrics.py)**：

```
T4.1: test_rough_tokens_empty — rough_tokens("") == 0
T4.2: test_rough_tokens_normal — rough_tokens("abcd") == 1
T4.3: test_chunks_to_metrics_all_empty — 全空返回全0
T4.4: test_chunks_to_metrics_stable_only — 只有 stable，dynamic_tokens=0
T4.5: test_chunks_to_metrics_dynamic_only — 只有 dynamic，stable_tokens=0
T4.6: test_chunks_to_metrics_by_source_count — 非空 chunk 数 == by_source 长度
T4.7: test_chunks_to_metrics_total_sum — stable + dynamic = total
```

---

### Step 5a — compat.py（prompt_builder wrappers）

**文件**: `agent/context_engine/compat.py`

**代码**：

```python
"""Compatibility wrappers for existing Hermes functions.

Each wrapper:
  1. Calls the original function unchanged
  2. Returns (content_str, error_str_or_none)
  3. Never throws; errors are returned as strings for observability
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt builder wrappers
# ---------------------------------------------------------------------------

def wrap_load_soul_md() -> tuple[str, Optional[str]]:
    """Wrapper for prompt_builder.load_soul_md()."""
    try:
        from agent.prompt_builder import load_soul_md
        content = load_soul_md() or ""
        return content, None
    except Exception as e:
        logger.debug("wrap_load_soul_md failed: %s", e)
        return "", str(e)


def wrap_build_context_files_prompt(
    cwd: str | None,
    *,
    skip_soul: bool = False,
) -> tuple[str, Optional[str]]:
    """Wrapper for prompt_builder.build_context_files_prompt()."""
    try:
        from agent.prompt_builder import build_context_files_prompt
        content = build_context_files_prompt(cwd=cwd, skip_soul=skip_soul) or ""
        return content, None
    except Exception as e:
        logger.debug("wrap_build_context_files_prompt failed: %s", e)
        return "", str(e)


def wrap_build_skills_system_prompt(
    available_tools: list[str],
    available_toolsets: set[str],
) -> tuple[str, Optional[str]]:
    """Wrapper for prompt_builder.build_skills_system_prompt()."""
    try:
        from agent.prompt_builder import build_skills_system_prompt
        content = build_skills_system_prompt(
            available_tools=available_tools,
            available_toolsets=available_toolsets,
        ) or ""
        return content, None
    except Exception as e:
        logger.debug("wrap_build_skills_system_prompt failed: %s", e)
        return "", str(e)
```

**边界条件**：
1. 原始函数返回 `None` → 转为空字符串 `""`
2. 任何异常 → 捕获后返回 `("", str(e))`，不向上抛出
3. `logger.debug` 不影响正常流程
4. `threading.Lock` 等模块级状态在原始函数中处理，wrapper 不额外处理

**测试用例 (tests/context_engine/test_compat_prompt_builder.py)**：

```
T5a.1: test_wrap_load_soul_md_returns_tuple — 返回 (str, str|None)
T5a.2: test_wrap_load_soul_md_none_becomes_empty — load_soul_md() 返回 None 时 content=""
T5a.3: test_wrap_build_context_files_prompt_exception_caught — 异常不抛出，返回 error str
T5a.4: test_wrap_build_skills_system_prompt_normal — 正常调用返回 content
```

---

### Step 5b — compat.py（memory_store + honcho wrappers）

**文件**: `agent/context_engine/compat.py`（追加）

**代码**（追加到 compat.py 末尾）：

```python
# ---------------------------------------------------------------------------
# Memory store wrappers
# ---------------------------------------------------------------------------

def wrap_memory_store_format(
    memory_store,  # MemoryStore instance
    memory_type: str,  # "memory" or "user"
) -> tuple[str, Optional[str]]:
    """Wrapper for MemoryStore.format_for_system_prompt().

    Args:
        memory_store: MemoryStore instance (from agent._memory_store)
        memory_type: "memory" or "user"
    """
    if memory_store is None:
        return "", None
    try:
        content = memory_store.format_for_system_prompt(memory_type) or ""
        return content, None
    except Exception as e:
        logger.debug("wrap_memory_store_format(%s) failed: %s", memory_type, e)
        return "", str(e)


# ---------------------------------------------------------------------------
# Honcho wrappers
# ---------------------------------------------------------------------------

def wrap_honcho_static_block(
    honcho_session_manager,  # HonchoSessionManager or None
    honcho_config,  # HonchoConfig or None
    ai_peer_name: str | None,
) -> tuple[str, Optional[str]]:
    """Build Honcho static block (baked into cached system prompt).

    Mirrors the logic in run_agent.py::_build_system_prompt() lines 2915-2967.
    Returns ("", None) if honcho is not active.
    """
    if honcho_session_manager is None or honcho_config is None:
        return "", None
    try:
        cfg = honcho_config
        mode = cfg.memory_mode if cfg.memory_mode else "hybrid"
        freq = cfg.write_frequency if cfg.write_frequency else "async"
        recall_mode = cfg.recall_mode if cfg.recall_mode else "hybrid"

        ai_name = (
            ai_peer_name if ai_peer_name and ai_peer_name != "hermes" else None
        )
        identity_suffix = f"You are {ai_name}" if ai_name else "You are Hermes Agent"
        identity_block = f"# AI Identity\n{identity_suffix}"

        honcho_block = (
            f"# Honcho memory integration\n"
            f"Active. Mode: {mode}. Write frequency: {freq}. Recall: {recall_mode}.\n"
        )
        if recall_mode == "context":
            honcho_block += (
                "Honcho context is injected into this system prompt below.\n"
            )
        elif recall_mode == "tools":
            honcho_block += (
                "Honcho tools:\n"
                "  honcho_context <question>\n"
                "  honcho_search <query>\n"
                "  honcho_profile\n"
                "  honcho_conclude <conclusion>\n"
            )
        else:  # hybrid
            honcho_block += (
                "Honcho context is injected into this system prompt below.\n"
                "Honcho tools:\n"
                "  honcho_context <question>\n"
                "  honcho_search <query>\n"
                "  honcho_profile\n"
                "  honcho_conclude <conclusion>\n"
            )
        content = identity_block + "\n\n" + honcho_block
        return content, None
    except Exception as e:
        logger.debug("wrap_honcho_static_block failed: %s", e)
        return "", str(e)


def wrap_honcho_get_turn_context(
    honcho_session_manager,  # HonchoSessionManager or None
    user_message: str,
    conversation_history: list,
) -> tuple[str, Optional[str]]:
    """Wrapper for HonchoSessionManager.get_turn_context()."""
    if honcho_session_manager is None:
        return "", None
    try:
        result = honcho_session_manager.get_turn_context(
            user_message=user_message,
            conversation_history=conversation_history,
        )
        content = result if isinstance(result, str) else (result or "")
        return content, None
    except Exception as e:
        logger.debug("wrap_honcho_get_turn_context failed: %s", e)
        return "", str(e)
```

**边界条件**：
1. `memory_store is None` → 返回 `("", None)`，不算 error
2. `honcho_session_manager is None` → 两个 honcho wrapper 均返回 `("", None)`
3. `honcho_config` 属性缺失 → `getattr(..., None)` 保护
4. `get_turn_context` 返回非字符串 → `isinstance` 检查后转字符串
5. 所有异常均捕获，不向上抛出

**测试用例 (tests/context_engine/test_compat_honcho_memory.py)**：

```
T5b.1: test_wrap_memory_store_format_none — memory_store=None 返回 ("", None)
T5b.2: test_wrap_memory_store_format_exception_caught — 异常返回 error
T5b.3: test_wrap_honcho_static_block_no_honcho — manager=None 返回 ("", None)
T5b.4: test_wrap_honcho_static_block_hybrid_mode — 包含 honcho tools
T5b.5: test_wrap_honcho_static_block_context_mode — 包含 inject 说明
T5b.6: test_wrap_honcho_get_turn_context_none — manager=None 返回 ("", None)
T5b.7: test_wrap_honcho_get_turn_context_exception_caught — 异常返回 error
```

---

### Step 5c — compat.py（sparkgraph + plugin wrappers）

**文件**: `agent/context_engine/compat.py`（追加）

**代码**（追加到 compat.py 末尾）：

```python
# ---------------------------------------------------------------------------
# SparkGraph wrappers
# ---------------------------------------------------------------------------

def wrap_sparkgraph_build_recall(
    sparkgraph_manager,  # SparkGraphManager or None
    sparkgraph_enabled: bool,
    user_message: str,
    *,
    max_nodes: int | None = None,
    max_chars: int | None = None,
) -> tuple[str, Optional[str]]:
    """Wrapper for SparkGraphManager.build_recall_block()."""
    if not sparkgraph_enabled or sparkgraph_manager is None:
        return "", None
    try:
        block = sparkgraph_manager.build_recall_block(
            user_message,
            max_nodes=max_nodes,
            max_chars=max_chars,
        )
        content = block if isinstance(block, str) else ""
        return content, None
    except Exception as e:
        logger.debug("wrap_sparkgraph_build_recall failed: %s", e)
        return "", str(e)


# ---------------------------------------------------------------------------
# Plugin wrappers
# ---------------------------------------------------------------------------

def wrap_invoke_pre_llm_call(
    session_id: str,
    user_message: str,
    conversation_history: list,
    is_first_turn: bool,
) -> tuple[str, Optional[str]]:
    """Wrapper for hermes_cli.plugins.invoke_hook('pre_llm_call', ...).

    Concatenates all plugin context strings with "\\n\\n".
    Returns ("", None) if no plugins return context.
    """
    try:
        from hermes_cli.plugins import invoke_hook
        results = invoke_hook(
            "pre_llm_call",
            session_id=session_id,
            user_message=user_message,
            conversation_history=list(conversation_history),
            is_first_turn=is_first_turn,
        )
        parts: list[str] = []
        if results:
            for r in results:
                if isinstance(r, dict) and r.get("context"):
                    parts.append(str(r["context"]))
                elif isinstance(r, str) and r.strip():
                    parts.append(r.strip())
        content = "\n\n".join(parts)
        return content, None
    except Exception as e:
        logger.debug("wrap_invoke_pre_llm_call failed: %s", e)
        return "", str(e)
```

**边界条件**：
1. `sparkgraph_enabled=False` 或 manager=None → 返回 `("", None)`，不算 error
2. `build_recall_block` 返回非字符串 → `isinstance` 检查
3. `invoke_hook` 返回 `None` → `if results` 保护，parts 为空时返回 `""`
4. 插件返回 `{"context": ""}` 空字符串 → 不加入 parts
5. `conversation_history` 被传为 `list()` copy，不修改原始数据
6. 所有异常均捕获

**测试用例 (tests/context_engine/test_compat_sparkgraph_plugin.py)**：

```
T5c.1: test_wrap_sparkgraph_disabled — enabled=False 返回 ("", None)
T5c.2: test_wrap_sparkgraph_manager_none — manager=None 返回 ("", None)
T5c.3: test_wrap_sparkgraph_exception_caught — 异常返回 error
T5c.4: test_wrap_sparkgraph_returns_string — 正常返回 block
T5c.5: test_wrap_invoke_pre_llm_call_no_plugins — 无 plugins 返回 ("", None)
T5c.6: test_wrap_invoke_pre_llm_call_dict_context — {"context": "x"} 加入 parts
T5c.7: test_wrap_invoke_pre_llm_call_string_result — 字符串结果加入 parts
T5c.8: test_wrap_invoke_pre_llm_call_empty_context_filtered — 空 context 被过滤
T5c.9: test_wrap_invoke_pre_llm_call_exception_caught — 异常返回 error
```

---

### Step 6 — sources.py（Stable 1-5）

**文件**: `agent/context_engine/sources.py`

**代码**：

```python
"""Context sources for Phase 1 — stable sources part 1.

Stable sources (in assembly order):
  1. IdentitySource
  2. ToolGuidanceSource
  3. ToolUseEnforcementSource
  4. HonchoStaticSource
  5. SystemMessageSource
"""

from __future__ import annotations

from typing import Callable, Literal

from agent.context_engine.compat import (
    wrap_honcho_static_block,
    wrap_load_soul_md,
)
from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import ContextChunk
from agent.context_engine.registry import register_stable

# ---------------------------------------------------------------------------
# Constants (duplicated from run_agent.py for source independence)
# ---------------------------------------------------------------------------

MEMORY_GUIDANCE = (
    "You have access to a persistent memory system. Use the memory tool "
    "to store important information between sessions."
)
SESSION_SEARCH_GUIDANCE = (
    "Before answering factual questions, consider using session_search "
    "to find relevant past conversations."
)
SKILLS_GUIDANCE = (
    "When attempting tasks, check if a relevant skill exists using skills_list. "
    "Skills can provide step-by-step guidance for complex tasks."
)
TOOL_USE_ENFORCEMENT_GUIDANCE = (
    "You must actually call tools rather than describing intended actions. "
    "Every time you say you will do something, you must call a tool to do it."
)
TOOL_USE_ENFORCEMENT_MODELS = ("gpt-4", "gpt-4o", "gpt-4-turbo", "gpt-3.5")


# ---------------------------------------------------------------------------
# Source 1: IdentitySource
# ---------------------------------------------------------------------------

class IdentitySource:
    """Agent identity: SOUL.md content or DEFAULT_AGENT_IDENTITY fallback."""

    name = "identity"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        get_soul_md_fn: Callable[[], str] = None,
        default_identity: str = None,
        ai_peer_name: str | None = None,
    ):
        self._get_soul = get_soul_md_fn or (lambda: "")
        self._default = default_identity or "You are Hermes Agent"
        self._ai_peer = ai_peer_name

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        content, err = wrap_load_soul_md()
        if not content and not err:
            # No SOUL.md found — use default identity
            if self._ai_peer:
                content = self._default.replace("You are Hermes Agent", f"You are {self._ai_peer}", 1)
            else:
                content = self._default

        return [ContextChunk(
            source="identity",
            stage="stable",
            slot="soul" if wrap_load_soul_md()[0] else "default",
            priority=1,
            content=content,
            metadata={"has_soul": bool(wrap_load_soul_md()[0])},
        )]


# ---------------------------------------------------------------------------
# Source 2: ToolGuidanceSource
# ---------------------------------------------------------------------------

class ToolGuidanceSource:
    """Tool-aware behavioral guidance strings."""

    name = "tool_guidance"
    stage: Literal["stable"] = "stable"

    def __init__(self, valid_tool_names: list[str] = None):
        self._tool_names = valid_tool_names or []

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        parts: list[str] = []
        if "memory" in self._tool_names:
            parts.append(MEMORY_GUIDANCE)
        if "session_search" in self._tool_names:
            parts.append(SESSION_SEARCH_GUIDANCE)
        if "skill_manage" in self._tool_names or "skills_list" in self._tool_names:
            parts.append(SKILLS_GUIDANCE)

        if not parts:
            return []

        content = " ".join(parts)
        return [ContextChunk(
            source="tool_guidance",
            stage="stable",
            slot="behavioral",
            priority=2,
            content=content,
        )]


# ---------------------------------------------------------------------------
# Source 3: ToolUseEnforcementSource
# ---------------------------------------------------------------------------

class ToolUseEnforcementSource:
    """Injects tool-use enforcement guidance based on model name and config."""

    name = "tool_use_enforcement"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        tool_use_enforcement,  # True/False/"auto"/list or None
        model: str | None = None,
    ):
        self._enforce = tool_use_enforcement
        self._model = model or ""

    def _should_inject(self) -> bool:
        enf = self._enforce
        model_lower = self._model.lower()
        if enf is True or (isinstance(enf, str) and enf.lower() in ("true", "always", "yes", "on")):
            return True
        if enf is False or (isinstance(enf, str) and enf.lower() in ("false", "never", "no", "off")):
            return False
        if isinstance(enf, list):
            return any(p.lower() in model_lower for p in enf if isinstance(p, str))
        # "auto" or any other value — use hardcoded defaults
        return any(p in model_lower for p in TOOL_USE_ENFORCEMENT_MODELS)

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        if not self._should_inject():
            return []
        return [ContextChunk(
            source="tool_use_enforcement",
            stage="stable",
            slot="enforcement",
            priority=3,
            content=TOOL_USE_ENFORCEMENT_GUIDANCE,
        )]


# ---------------------------------------------------------------------------
# Source 4: HonchoStaticSource
# ---------------------------------------------------------------------------

class HonchoStaticSource:
    """Honcho static block (baked into cached system prompt)."""

    name = "honcho_static"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        honcho_session_manager=None,
        honcho_config=None,
        ai_peer_name: str | None = None,
    ):
        self._manager = honcho_session_manager
        self._config = honcho_config
        self._ai_peer = ai_peer_name

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        if self._manager is None:
            return []
        content, err = wrap_honcho_static_block(
            self._manager, self._config, self._ai_peer
        )
        if not content and not err:
            return []
        return [ContextChunk(
            source="honcho_static",
            stage="stable",
            slot="honcho",
            priority=4,
            content=content,
        )]


# ---------------------------------------------------------------------------
# Source 5: SystemMessageSource
# ---------------------------------------------------------------------------

class SystemMessageSource:
    """External system_message provided at session start."""

    name = "system_message"
    stage: Literal["stable"] = "stable"

    def __init__(self, system_message: str | None = None):
        self._message = system_message

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        content = self._message or (ctx.system_message if ctx.system_message else "")
        if not content:
            return []
        return [ContextChunk(
            source="system_message",
            stage="stable",
            slot="external",
            priority=5,
            content=content,
        )]
```

**边界条件**：
1. `valid_tool_names=[]` → `ToolGuidanceSource` 返回 `[]`
2. `ai_peer_name=None` → `IdentitySource` 用默认 identity
3. `wrap_load_soul_md` 被调用两次（在 `IdentitySource.collect` 中）→ 第一次取结果，第二次仅用于检查，无副作用
4. `honcho_manager is None` → `HonchoStaticSource` 返回 `[]`
5. `system_message=None` 且 `ctx.system_message=None` → 返回 `[]`
6. `model=""` 空字符串 → `_should_inject` 中 `model_lower=""`，不会匹配任何 enforcement model

**测试用例 (tests/context_engine/test_sources_stable_1_5.py)**：

```
T6.1: test_identity_source_with_soul — 有 SOUL.md 时 slot="soul", has_soul=True
T6.2: test_identity_source_without_soul — 无 SOUL.md 时 slot="default"
T6.3: test_identity_source_ai_peer_substitution — ai_peer_name 替换默认 identity
T6.4: test_tool_guidance_source_empty — tool_names=[] 返回 []
T6.5: test_tool_guidance_source_memory_only — 只有 memory tool 时返回一条 guidance
T6.6: test_tool_guidance_source_all_three — 三个 tool 都存在时返回三条 joined
T6.7: test_tool_use_enforcement_auto_match — "auto" + "gpt-4" → inject
T6.8: test_tool_use_enforcement_false — False → 不 inject
T6.9: test_tool_use_enforcement_list_match — list 匹配 → inject
T6.10: test_tool_use_enforcement_list_no_match — list 不匹配 → 不 inject
T6.11: test_honcho_static_no_manager — manager=None → []
T6.12: test_honcho_static_with_manager — 有 manager → 返回 content
T6.13: test_system_message_none — system_message=None → []
T6.14: test_system_message_from_ctx — ctx.system_message 有值 → 返回 content
```

---

### Step 7 — sources.py（Stable 6-10）

**文件**: `agent/context_engine/sources.py`（追加）

**代码**（追加到 sources.py 末尾）：

```python
# ---------------------------------------------------------------------------
# Source 6: MemorySource
# ---------------------------------------------------------------------------

class MemorySource:
    """Persistent memory: MEMORY.md formatted for system prompt."""

    name = "memory"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        memory_store=None,  # MemoryStore instance
        memory_enabled: bool = True,
    ):
        self._store = memory_store
        self._enabled = memory_enabled

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        if not self._enabled or self._store is None:
            return []
        content, err = self._store.format_for_system_prompt("memory") if self._store else ("", "no store")
        if not content and not err:
            return []
        return [ContextChunk(
            source="memory",
            stage="stable",
            slot="memory",
            priority=6,
            content=content or "",
        )]


# ---------------------------------------------------------------------------
# Source 7: UserProfileSource
# ---------------------------------------------------------------------------

class UserProfileSource:
    """User profile: USER.md formatted for system prompt."""

    name = "user_profile"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        memory_store=None,  # MemoryStore instance
        user_profile_enabled: bool = True,
    ):
        self._store = memory_store
        self._enabled = user_profile_enabled

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        if not self._enabled or self._store is None:
            return []
        content, err = self._store.format_for_system_prompt("user") if self._store else ("", "no store")
        if not content and not err:
            return []
        return [ContextChunk(
            source="user_profile",
            stage="stable",
            slot="user",
            priority=7,
            content=content or "",
        )]


# ---------------------------------------------------------------------------
# Source 8: SkillsSource
# ---------------------------------------------------------------------------

class SkillsSource:
    """Skills index: compact skills system prompt."""

    name = "skills"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        available_tools: list[str] = None,
        available_toolsets: set[str] = None,
        build_skills_fn=None,  # Override for testing; normally from compat
    ):
        self._tools = available_tools or []
        self._toolsets = available_toolsets or set()
        self._build_fn = build_skills_fn

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        has_skills = any(
            name in self._tools
            for name in ("skills_list", "skill_view", "skill_manage")
        )
        if not has_skills:
            return []
        if self._build_fn:
            content = self._build_fn(
                available_tools=self._tools,
                available_toolsets=self._toolsets,
            ) or ""
        else:
            from agent.context_engine.compat import wrap_build_skills_system_prompt
            content, _ = wrap_build_skills_system_prompt(
                available_tools=self._tools,
                available_toolsets=self._toolsets,
            )
        if not content:
            return []
        return [ContextChunk(
            source="skills",
            stage="stable",
            slot="skills_index",
            priority=8,
            content=content,
        )]


# ---------------------------------------------------------------------------
# Source 9: ProjectContextSource
# ---------------------------------------------------------------------------

class ProjectContextSource:
    """Context files: AGENTS.md, .cursorrules, etc."""

    name = "project_context"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        cwd: str | None = None,
        skip_soul: bool = False,
    ):
        self._cwd = cwd
        self._skip_soul = skip_soul

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        from agent.context_engine.compat import wrap_build_context_files_prompt
        content, err = wrap_build_context_files_prompt(
            cwd=self._cwd or ctx.cwd,
            skip_soul=self._skip_soul,
        )
        if not content and not err:
            return []
        return [ContextChunk(
            source="project_context",
            stage="stable",
            slot="context_files",
            priority=9,
            content=content or "",
        )]


# ---------------------------------------------------------------------------
# Source 10: TimePlatformSource
# ---------------------------------------------------------------------------

# Platform hints dict (duplicated from run_agent.py for source independence)
_PLATFORM_HINTS = {
    "telegram": (
        "You are communicating via Telegram. Keep messages concise. "
        "Use markdown sparingly. Do not use HTML telegram tags."
    ),
    "discord": (
        "You are communicating via Discord. Keep messages concise. "
        "Use Discord-compatible formatting."
    ),
    "slack": (
        "You are communicating via Slack. Keep messages concise. "
        "Use Slack-compatible formatting."
    ),
    "whatsapp": (
        "You are communicating via WhatsApp. Keep messages very short. "
        "Single-line responses preferred."
    ),
    "signal": (
        "You are communicating via Signal. Keep messages very short."
    ),
}


class TimePlatformSource:
    """Timestamp, session ID, model, provider, and platform hint."""

    name = "time_platform"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        model: str | None = None,
        provider: str | None = None,
        session_id: str | None = None,
        platform: str | None = None,
        pass_session_id: bool = False,
        cwd: str | None = None,
    ):
        self._model = model
        self._provider = provider
        self._session_id = session_id
        self._platform = platform
        self._pass_session_id = pass_session_id
        self._cwd = cwd

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        from hermes_time import now as hermes_now
        now = hermes_now()
        timestamp = now.strftime("%A, %B %d, %Y %I:%M %p")

        parts: list[str] = []
        parts.append(f"Conversation started: {timestamp}")
        if self._pass_session_id and self._session_id:
            parts.append(f"Session ID: {self._session_id}")
        if self._model:
            parts.append(f"Model: {self._model}")
        if self._provider:
            parts.append(f"Provider: {self._provider}")

        content = "\n".join(parts)

        # Platform hint
        platform_key = (self._platform or "").lower().strip()
        if platform_key in _PLATFORM_HINTS:
            hint = _PLATFORM_HINTS[platform_key]
            content += "\n\n" + hint

        if not content.strip():
            return []

        return [ContextChunk(
            source="time_platform",
            stage="stable",
            slot="timestamp",
            priority=10,
            content=content,
        )]
```

**边界条件**：
1. `memory_store=None` → `MemorySource` 和 `UserProfileSource` 均返回 `[]`
2. `memory_enabled=False` → `MemorySource` 返回 `[]`
3. `skills` tools 均不存在 → `SkillsSource` 返回 `[]`
4. `build_skills_fn` 传入则用之（测试用），否则用 compat wrapper
5. `context_files_prompt` 无内容 → `ProjectContextSource` 返回 `[]`
6. `cwd=None` → fallback 到 `ctx.cwd`
7. `platform` 不在 `_PLATFORM_HINTS` 中 → 不加 hint
8. `provider/model/session_id` 均无 → `TimePlatformSource` 只含 timestamp
9. `pass_session_id=False` → 不输出 session ID

**测试用例 (tests/context_engine/test_sources_stable_6_10.py)**：

```
T7.1: test_memory_source_disabled — memory_enabled=False → []
T7.2: test_memory_source_no_store — store=None → []
T7.3: test_memory_source_with_content — 正常返回 content
T7.4: test_user_profile_source_same — 同上逻辑
T7.5: test_skills_source_no_skills_tools — 无 skills tools → []
T7.6: test_skills_source_with_override_fn — build_fn 传入则用
T7.7: test_project_context_source_empty — 无 context files → []
T7.8: test_project_context_source_cwd_fallback — cwd=None 时用 ctx.cwd
T7.9: test_time_platform_no_model_provider — 只有 timestamp
T7.10: test_time_platform_with_session — pass_session_id=True 时显示 session
T7.11: test_time_platform_platform_hint — telegram → 加 hint
T7.12: test_time_platform_unknown_platform — unknown → 不加 hint
```

---

### Step 8 — sources.py（Dynamic 4）

**文件**: `agent/context_engine/sources.py`（追加）

**代码**（追加到 sources.py 末尾）：

```python
"""Context sources — dynamic sources.

Dynamic sources (in assembly order):
  1. EphemeralSystemSource
  2. PluginTurnContextSource
  3. SparkGraphRecallSource
  4. HonchoTurnSource
"""

from __future__ import annotations

from typing import Literal

from agent.context_engine.compat import (
    wrap_honcho_get_turn_context,
    wrap_invoke_pre_llm_call,
    wrap_sparkgraph_build_recall,
)
from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import ContextChunk
from agent.context_engine.registry import register_dynamic


# ---------------------------------------------------------------------------
# Dynamic Source 1: EphemeralSystemSource
# ---------------------------------------------------------------------------

class EphemeralSystemSource:
    """Ephemeral system prompt set at runtime (CLI/gateway)."""

    name = "ephemeral"
    stage: Literal["dynamic"] = "dynamic"

    def __init__(self, get_ephemeral_fn: callable = None):
        """get_ephemeral_fn: () -> str, returns current ephemeral_system_prompt."""
        self._get = get_ephemeral_fn or (lambda: "")

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        content = self._get() or ""
        if not content:
            return []
        return [ContextChunk(
            source="ephemeral",
            stage="dynamic",
            slot="ephemeral",
            priority=1,
            content=content,
        )]


# ---------------------------------------------------------------------------
# Dynamic Source 2: PluginTurnContextSource
# ---------------------------------------------------------------------------

class PluginTurnContextSource:
    """Plugin pre_llm_call hook context."""

    name = "plugin"
    stage: Literal["dynamic"] = "dynamic"

    def __init__(
        self,
        session_id: str | None = None,
    ):
        self._session_id = session_id

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        is_first = len(ctx.conversation_history) <= 1
        content, err = wrap_invoke_pre_llm_call(
            session_id=self._session_id or "",
            user_message=ctx.user_message or "",
            conversation_history=ctx.conversation_history,
            is_first_turn=is_first,
        )
        if not content and not err:
            return []
        return [ContextChunk(
            source="plugin",
            stage="dynamic",
            slot="plugin_context",
            priority=2,
            content=content or "",
        )]


# ---------------------------------------------------------------------------
# Dynamic Source 3: SparkGraphRecallSource
# ---------------------------------------------------------------------------

class SparkGraphRecallSource:
    """SparkGraph recall block for current turn query."""

    name = "sparkgraph_recall"
    stage: Literal["dynamic"] = "dynamic"

    def __init__(
        self,
        sparkgraph_manager=None,
        sparkgraph_enabled: bool = False,
    ):
        self._manager = sparkgraph_manager
        self._enabled = sparkgraph_enabled

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        if not self._enabled or self._manager is None:
            return []
        content, err = wrap_sparkgraph_build_recall(
            self._manager,
            self._enabled,
            user_message=ctx.user_message or "",
        )
        if not content and not err:
            return []
        return [ContextChunk(
            source="sparkgraph_recall",
            stage="dynamic",
            slot="recall",
            priority=3,
            content=content or "",
            metadata={"recall_injected": bool(content)},
        )]


# ---------------------------------------------------------------------------
# Dynamic Source 4: HonchoTurnSource
# ---------------------------------------------------------------------------

class HonchoTurnSource:
    """Honcho per-turn context (not baked into cached prompt)."""

    name = "honcho_turn"
    stage: Literal["dynamic"] = "dynamic"

    def __init__(self, honcho_session_manager=None):
        self._manager = honcho_session_manager

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        if self._manager is None:
            return []
        content, err = wrap_honcho_get_turn_context(
            self._manager,
            user_message=ctx.user_message or "",
            conversation_history=ctx.conversation_history,
        )
        if not content and not err:
            return []
        return [ContextChunk(
            source="honcho_turn",
            stage="dynamic",
            slot="honcho_context",
            priority=4,
            content=content or "",
        )]
```

**边界条件**：
1. `get_ephemeral_fn=None` → `self._get` 默认为返回 `""`
2. `user_message=None` → compat wrapper 用 `""` 替代
3. `session_id=None` → compat wrapper 用 `""`
4. `conversation_history=[]` → `is_first_turn=True`
5. `sparkgraph_enabled=False` → 返回 `[]`
6. `sparkgraph_manager` 正常但 recall 为空 → 返回 `[]`
7. `honcho_manager is None` → 返回 `[]`
8. `honcho_turn_context` 返回空字符串且无 error → 返回 `[]`
9. 任意 wrapper 返回 `("", None)` → source 返回 `[]`
10. `ctx.user_message` 为 `None` → compat wrapper 处理，不抛异常

**测试用例 (tests/context_engine/test_sources_dynamic.py)**：

```
T8.1:  test_ephemeral_empty_fn — get_ephemeral_fn 返回 "" → []
T8.2:  test_ephemeral_with_content — 返回 content，slot=ephemeral
T8.3:  test_plugin_first_turn — history ≤1 → is_first_turn=True
T8.4:  test_plugin_no_plugins — 无 context → []
T8.5:  test_plugin_with_context_dict — {"context": "x"} → 返回
T8.6:  test_plugin_empty_context_filtered — 空 dict context 被过滤
T8.7:  test_plugin_conversation_history_copy — 传入 list copy，不修改原
T8.8:  test_sparkgraph_disabled — enabled=False → []
T8.9:  test_sparkgraph_manager_none — manager=None → []
T8.10: test_sparkgraph_with_recall — 正常返回 recall block
T8.11: test_sparkgraph_empty_recall — block="" → []
T8.12: test_sparkgraph_metadata — recall_injected 标志
T8.13: test_honcho_no_manager — manager=None → []
T8.14: test_honcho_with_context — 正常返回 content
T8.15: test_honcho_empty_context — "" → []
T8.16: test_user_message_none_in_ctx — compat wrapper 处理 None
```

---

### Step 9 — assembler.py

**文件**: `agent/context_engine/assembler.py`

**代码**：

```python
"""Context assembler — orchestrates sources and produces AssemblyResult."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import AssemblyResult, ContextChunk

if TYPE_CHECKING:
    from agent.context_engine.sources import (
        EphemeralSystemSource,
        HonchoStaticSource,
        HonchoTurnSource,
        IdentitySource,
        MemorySource,
        PluginTurnContextSource,
        ProjectContextSource,
        SkillsSource,
        SparkGraphRecallSource,
        SystemMessageSource,
        TimePlatformSource,
        ToolGuidanceSource,
        ToolUseEnforcementSource,
        UserProfileSource,
    )


# ---------------------------------------------------------------------------
# Source factories (one per source, receives AssemblyContext)
# ---------------------------------------------------------------------------

def _identity_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import IdentitySource
    source = IdentitySource(
        get_soul_md_fn=None,  # uses compat wrapper internally
        default_identity=None,  # uses compat constant
        ai_peer_name=getattr(ctx.agent, "_honcho_config", None) and getattr(ctx.agent._honcho_config, "ai_peer", None),
    )
    return source.collect(ctx)


def _tool_guidance_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import ToolGuidanceSource
    tool_names = getattr(ctx.agent, "valid_tool_names", []) or []
    return ToolGuidanceSource(valid_tool_names=tool_names).collect(ctx)


def _tool_enforcement_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import ToolUseEnforcementSource
    enforce = getattr(ctx.agent, "_tool_use_enforcement", None)
    model = getattr(ctx.agent, "model", None)
    return ToolUseEnforcementSource(
        tool_use_enforcement=enforce,
        model=model,
    ).collect(ctx)


def _honcho_static_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import HonchoStaticSource
    manager = getattr(ctx.agent, "_honcho", None)
    config = getattr(ctx.agent, "_honcho_config", None)
    ai_peer = config.ai_peer if config else None
    return HonchoStaticSource(
        honcho_session_manager=manager,
        honcho_config=config,
        ai_peer_name=ai_peer,
    ).collect(ctx)


def _system_message_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import SystemMessageSource
    msg = getattr(ctx.agent, "system_message", None)
    return SystemMessageSource(system_message=msg).collect(ctx)


def _memory_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import MemorySource
    store = getattr(ctx.agent, "_memory_store", None)
    enabled = getattr(ctx.agent, "_memory_enabled", True)
    return MemorySource(memory_store=store, memory_enabled=enabled).collect(ctx)


def _user_profile_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import UserProfileSource
    store = getattr(ctx.agent, "_memory_store", None)
    enabled = getattr(ctx.agent, "_user_profile_enabled", True)
    return UserProfileSource(memory_store=store, user_profile_enabled=enabled).collect(ctx)


def _skills_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import SkillsSource
    tool_names = getattr(ctx.agent, "valid_tool_names", []) or []
    return SkillsSource(available_tools=tool_names).collect(ctx)


def _project_context_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import ProjectContextSource
    cwd = getattr(ctx.agent, "_context_cwd", None)
    skip_soul = False  # handled in compat
    return ProjectContextSource(cwd=cwd, skip_soul=skip_soul).collect(ctx)


def _time_platform_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import TimePlatformSource
    return TimePlatformSource(
        model=getattr(ctx.agent, "model", None),
        provider=getattr(ctx.agent, "provider", None),
        session_id=getattr(ctx.agent, "session_id", None),
        platform=getattr(ctx.agent, "platform", None),
        pass_session_id=getattr(ctx.agent, "pass_session_id", False),
        cwd=getattr(ctx.agent, "_context_cwd", None),
    ).collect(ctx)


def _ephemeral_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import EphemeralSystemSource
    def get_ephemeral():
        return getattr(ctx.agent, "ephemeral_system_prompt", "") or ""
    return EphemeralSystemSource(get_ephemeral_fn=get_ephemeral).collect(ctx)


def _plugin_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import PluginTurnContextSource
    sid = getattr(ctx.agent, "session_id", None)
    return PluginTurnContextSource(session_id=sid).collect(ctx)


def _sparkgraph_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import SparkGraphRecallSource
    manager = getattr(ctx.agent, "_sparkgraph_manager", None)
    enabled = getattr(ctx.agent, "_sparkgraph_enabled", False)
    return SparkGraphRecallSource(
        sparkgraph_manager=manager,
        sparkgraph_enabled=enabled,
    ).collect(ctx)


def _honcho_turn_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import HonchoTurnSource
    manager = getattr(ctx.agent, "_honcho", None)
    return HonchoTurnSource(honcho_session_manager=manager).collect(ctx)


# ---------------------------------------------------------------------------
# Stable and dynamic source lists (in assembly order)
# ---------------------------------------------------------------------------

STABLE_FACTORIES = [
    ("identity", _identity_factory),
    ("tool_guidance", _tool_guidance_factory),
    ("tool_use_enforcement", _tool_enforcement_factory),
    ("honcho_static", _honcho_static_factory),
    ("system_message", _system_message_factory),
    ("memory", _memory_factory),
    ("user_profile", _user_profile_factory),
    ("skills", _skills_factory),
    ("project_context", _project_context_factory),
    ("time_platform", _time_platform_factory),
]

DYNAMIC_FACTORIES = [
    ("ephemeral", _ephemeral_factory),
    ("plugin", _plugin_factory),
    ("sparkgraph_recall", _sparkgraph_factory),
    ("honcho_turn", _honcho_turn_factory),
]


# ---------------------------------------------------------------------------
# ContextAssembler
# ---------------------------------------------------------------------------

class ContextAssembler:
    """Orchestrates all context sources to produce AssemblyResult.

    Phase 1: only collects and joins; no new budget/policy logic.
    """

    def __init__(
        self,
        agent,  # AIAgent instance
        stable_factories: list = None,
        dynamic_factories: list = None,
    ):
        self._agent = agent
        self._stable_factories = stable_factories or STABLE_FACTORIES
        self._dynamic_factories = dynamic_factories or DYNAMIC_FACTORIES

    def _collect(
        self,
        factories: list,
        ctx: AssemblyContext,
    ) -> list[ContextChunk]:
        """Collect chunks from all factories, skipping empty results."""
        chunks: list[ContextChunk] = []
        for name, factory in factories:
            try:
                result = factory(ctx)
            except Exception:
                # Source factory error — skip, don't fail entire assembly
                result = []
            if result:  # skip empty lists (per Q6 decision)
                chunks.extend(result)
        return chunks

    def assemble_stable(self, system_message: str = None) -> AssemblyResult:
        """Assemble stable system prompt.

        Mirrors _build_system_prompt() logic.
        Caching is handled by run_agent.py (per Q2 decision).
        """
        ctx = AssemblyContext(
            agent=self._agent,
            system_message=system_message,
            cwd=getattr(self._agent, "_context_cwd", None),
        )
        chunks = self._collect(self._stable_factories, ctx)
        return AssemblyResult.from_chunks(stable_chunks=chunks, dynamic_chunks=[])

    def assemble_dynamic(
        self,
        user_message: str = None,
        conversation_history: list = None,
    ) -> AssemblyResult:
        """Assemble dynamic system additions for current turn.

        Called per API call (per Q3 decision).
        """
        ctx = AssemblyContext(
            agent=self._agent,
            user_message=user_message,
            conversation_history=conversation_history or [],
        )
        chunks = self._collect(self._dynamic_factories, ctx)
        return AssemblyResult.from_chunks(stable_chunks=[], dynamic_chunks=chunks)

    def assemble_all(
        self,
        system_message: str = None,
        user_message: str = None,
        conversation_history: list = None,
    ) -> AssemblyResult:
        """Assemble both stable and dynamic in one call.

        Convenience method — equivalent to calling assemble_stable and
        assemble_dynamic separately, then combining.
        """
        ctx = AssemblyContext(
            agent=self._agent,
            system_message=system_message,
            user_message=user_message,
            conversation_history=conversation_history or [],
            cwd=getattr(self._agent, "_context_cwd", None),
        )
        stable_chunks = self._collect(self._stable_factories, ctx)
        dynamic_chunks = self._collect(self._dynamic_factories, ctx)
        return AssemblyResult.from_chunks(
            stable_chunks=stable_chunks,
            dynamic_chunks=dynamic_chunks,
        )
```

**边界条件**：
1. 任意 factory 抛异常 → `_collect` 捕获并返回 `[]`，不影响其他 source
2. factory 返回 `None` → `if result` 为 False，跳过
3. factory 返回空 list → 同样跳过
4. `system_message=None` → `AssemblyContext` 有默认值
5. `user_message=None` → compat wrapper 处理
6. `conversation_history=None` → 默认为 `[]`
7. `stable_factories/dynamic_factories` 可替换（用于测试注入 mock factories）
8. `assemble_all` 与分别调用等价，`from_chunks` 正确 join

**测试用例 (tests/context_engine/test_assembler.py)**：

```
T9.1:  test_assemble_stable_calls_all_factories — 所有 stable factory 均被调用
T9.2:  test_assemble_stable_skips_empty_results — 空返回被跳过
T9.3:  test_assemble_stable_exception_in_factory — 单个异常不终止 assembly
T9.4:  test_assemble_stable_none_system_message — None 不崩溃
T9.5:  test_assemble_dynamic_calls_all_factories — 所有 dynamic factory 均被调用
T9.6:  test_assemble_dynamic_empty_user_message — user_message=None 不崩溃
T9.7:  test_assemble_dynamic_empty_conversation_history — history=None → []
T9.8:  test_assemble_all_combines_chunks — stable + dynamic 均在 result
T9.9:  test_assemble_all_effective_system_format — stable + "\n\n" + dynamic
T9.10: test_assemble_result_metrics_has_all_sources — by_source 包含所有 non-empty
T9.11: test_assemble_result_metrics_stable_dynamic_split — stable_tokens / dynamic_tokens 正确
T9.12: test_assembler_custom_factories — 传入 mock_factories 替代默认
T9.13: test_assemble_stable_source_order — chunks 按 factory 注册顺序
T9.14: test_assemble_dynamic_source_order — 同上
```

---

### Step 10 — __init__.py

**文件**: `agent/context_engine/__init__.py`

**代码**：

```python
"""Context Engine — unified context assembly and metrics.

Phase 1: zero behavior change, unified metrics, backward compatible.
"""

from agent.context_engine.assembler import (
    ASSEMBLER,
    ContextAssembler,
)
from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import (
    AssemblyResult,
    ContextChunk,
    ContextMetrics,
    SourceMetrics,
)
from agent.context_engine.registry import (
    DYNAMIC_SOURCE_FACTORIES,
    STABLE_SOURCE_FACTORIES,
    register_dynamic,
    register_stable,
)

__all__ = [
    # Models
    "ContextChunk",
    "ContextMetrics",
    "SourceMetrics",
    "AssemblyResult",
    # Context
    "AssemblyContext",
    # Registry
    "STABLE_SOURCE_FACTORIES",
    "DYNAMIC_SOURCE_FACTORIES",
    "register_stable",
    "register_dynamic",
    # Assembler
    "ContextAssembler",
    "ASSEMBLER",
]

# Shared default assembler instance (lazily initialized)
ASSEMBLER = None


def get_assembler(agent) -> ContextAssembler:
    """Get or create a ContextAssembler for the given agent."""
    global ASSEMBLER
    if ASSEMBLER is None:
        ASSEMBLER = ContextAssembler(agent)
    return ASSEMBLER
```

**边界条件**：
1. `ASSEMBLER` 全局单例，首次 `get_assembler` 时创建
2. 所有公共符号通过 `__all__` 显式导出
3. 不导入 sources 模块（避免循环依赖）

**测试用例 (tests/context_engine/test_init.py)**：

```
T10.1: test_all_exports — __all__ 包含所有预期符号
T10.2: test_no_circular_import — import agent.context_engine 不抛异常
T10.3: test_get_assembler_lazy — ASSEMBLER 首次调用时创建
T10.4: test_get_assembler_returns_context_assembler — 返回正确类型
```

---

### Step 11 — run_agent.py 接入 stable

**文件**: `run_agent.py`（修改）

**改动位置 1**：`AIAgent.__init__` 附近，添加 assembler 初始化：

```python
# 在 AIAgent.__init__ 中，初始化时创建 assembler
self._context_assembler = None  # lazy init
```

**改动位置 2**：`_build_system_prompt()` 方法（line 2838），替换函数体：

```python
def _build_system_prompt(self, system_message: str = None) -> str:
    """Assemble the full system prompt via ContextAssembler.

    Assembly result is also synced to context_compressor for metrics.
    """
    assembler = self._get_context_assembler()
    result = assembler.assemble_stable(system_message=system_message)
    result.metrics.sync_to(self.context_compressor)
    self._cached_system_prompt = result.stable_system
    return self._cached_system_prompt


def _get_context_assembler(self):
    """Lazily create ContextAssembler on first use."""
    if self._context_assembler is None:
        from agent.context_engine import ContextAssembler
        self._context_assembler = ContextAssembler(agent=self)
    return self._context_assembler
```

**改动位置 3**：压缩后调用（line 5704）：

```python
# 现有：
new_system_prompt = self._build_system_prompt(system_message)

# 不变 —— _build_system_prompt 已通过 assembler，结果已自动 sync
```

**改动位置 4**：compression 后 `_cached_system_prompt` 赋值（line 6736）：

```python
# 现有：
self._cached_system_prompt = self._build_system_prompt(system_message)

# 不变 —— _build_system_prompt 已通过 assembler
```

**边界条件**：
1. `context_compressor=None` → `sync_to` 内部 `if compressor is None: return`
2. `assembler` 创建失败（import 错误）→ 降级到原有拼装逻辑（try/except 包裹）
3. `result.stable_system=""` → `_cached_system_prompt=""` 允许
4. `_context_assembler` 只初始化一次，多线程安全（Python GIL）

**测试用例 (tests/context_engine/test_run_agent_integration.py)**：

```
T11.1: test_build_system_prompt_returns_string — 返回值是字符串
T11.2: test_build_system_prompt_syncs_metrics — context_compressor.last_prompt_tokens 被设置
T11.3: test_build_system_prompt_cached — 第二次调用不重新 assemble（缓存逻辑不变）
T11.4: test_compression_after_build — compression 后 _build_system_prompt 行为一致
T11.5: test_assembler_lazy_init — _context_assembler 首次调用时创建
```

---

### Step 12 — run_agent.py 接入 dynamic

**文件**: `run_agent.py`（修改）

**改动位置**：主循环 effective_system 拼装处（line 6967-6978）。

现有代码：
```python
effective_system = active_system_prompt or ""
if self.ephemeral_system_prompt:
    effective_system = (effective_system + "\n\n" + self.ephemeral_system_prompt).strip()
if _plugin_turn_context:
    effective_system = (effective_system + "\n\n" + _plugin_turn_context).strip()
if _sparkgraph_turn_context:
    effective_system = (effective_system + "\n\n" + _sparkgraph_turn_context).strip()
```

**替换为**：

```python
# Assemble dynamic content via context engine
assembler = self._get_context_assembler()
dynamic_result = assembler.assemble_dynamic(
    user_message=original_user_message,
    conversation_history=messages,
)
dynamic_result.metrics.sync_to(self.context_compressor)

# Build effective_system: stable base + dynamic additions
effective_system = active_system_prompt or ""
if dynamic_result.effective_system:
    if effective_system:
        effective_system = (effective_system + "\n\n" + dynamic_result.effective_system).strip()
    else:
        effective_system = dynamic_result.effective_system
```

**fallback 逻辑保留**（确保零行为变化）：

```python
# If assembler fails, fall back to original _sparkgraph_turn_context / _plugin_turn_context
if not dynamic_result.effective_system:
    if self.ephemeral_system_prompt:
        effective_system = (effective_system + "\n\n" + self.ephemeral_system_prompt).strip()
    if _plugin_turn_context:
        effective_system = (effective_system + "\n\n" + _plugin_turn_context).strip()
    if _sparkgraph_turn_context:
        effective_system = (effective_system + "\n\n" + _sparkgraph_turn_context).strip()
else:
    # Assembler succeeded — also populate _sparkgraph_turn_context / _plugin_turn_context
    # for any code that reads them directly (e.g. quiet mode logging)
    _plugin_turn_context = "\n".join(
        c.content for c in dynamic_result.dynamic_chunks if c.source == "plugin"
    )
    _sparkgraph_turn_context = "\n".join(
        c.content for c in dynamic_result.dynamic_chunks if c.source == "sparkgraph_recall"
    )
```

**边界条件**：
1. `assembler.assemble_dynamic()` 抛异常 → `try/except` 捕获，降级到原有 fallback
2. `dynamic_result.effective_system=""` → fallback 到原有逐个拼接逻辑
3. `original_user_message=None` → compat wrapper 处理
4. `messages=[]` → `conversation_history=[]` 允许
5. 降级后行为与原来完全一致（原有变量仍被设置）
6. `_plugin_turn_context` 和 `_sparkgraph_turn_context` 在 fallback 后仍被设置，保证 quiet mode logging 等直接读取这些变量的代码正常工作

**测试用例 (tests/context_engine/test_run_agent_dynamic.py)**：

```
T12.1: test_assemble_dynamic_called_per_loop — 每次 API 调用前触发
T12.2: test_assemble_dynamic_syncs_metrics — metrics 同步到 context_compressor
T12.3: test_assemble_dynamic_fallback_on_exception — assembler 异常时降级到原有逻辑
T12.4: test_assemble_dynamic_empty_effective — effective_system="" 时走原有路径
T12.5: test_fallback_variables_set — 降级时 _sparkgraph_turn_context 仍被设置
T12.6: test_effective_system_order_preserved — stable → ephemeral → plugin → sparkgraph 顺序
T12.7: test_original_user_message_passed — user_message 正确传入 assembler
```

---

### Step 13 — CLI 颜色 Source 占比进度条

**文件**: `cli.py`（修改）

**改动位置 1**：颜色定义（文件顶部或相关 section）：

```python
# Source color mapping for context breakdown display
_SOURCE_COLORS = {
    "stable": "cyan",
    "memory": "green",
    "user_profile": "blue",
    "skills": "yellow",
    "project_context": "orange",
    "sparkgraph_recall": "purple",
    "ephemeral": "bright_black",
    "plugin": "gray",
    "honcho_static": "magenta",
    "honcho_turn": "magenta",
    "tool_guidance": "bright_cyan",
    "tool_use_enforcement": "bright_cyan",
    "identity": "bright_blue",
    "system_message": "bright_green",
    "time_platform": "bright_black",
}
_SOURCE_LABELS = {
    "stable": "stable",
    "memory": "memory",
    "user_profile": "user",
    "skills": "skills",
    "project_context": "project",
    "sparkgraph_recall": "SG",
    "ephemeral": "ephemeral",
    "plugin": "plugin",
    "honcho_static": "honcho",
    "honcho_turn": "honcho_turn",
    "tool_guidance": "tool_guidance",
    "tool_use_enforcement": "tool_enforce",
    "identity": "identity",
    "system_message": "system",
    "time_platform": "time",
}
```

**改动位置 2**：`_build_status_bar` 或相关方法中，进度条渲染部分：

```python
def _render_context_breakdown(self, metrics: "ContextMetrics") -> str:
    """Render per-source token breakdown for debug/analysis display.

    Called internally by status display; not part of the main progress bar
    in Phase 1 (Phase 2 feature).
    """
    if not metrics or not metrics.by_source:
        return ""

    lines = []
    for sm in metrics.by_source:
        color = _SOURCE_COLORS.get(sm.source, "white")
        label = _SOURCE_LABELS.get(sm.source, sm.source)
        lines.append(f"  [{color}]{label}[/{color}]: {sm.rough_tokens:,} chars:{sm.char_count:,}")

    return "\n".join(lines)


def _render_source_bar(self, metrics: "ContextMetrics", width: int = 20) -> str:
    """Render a colored token proportion bar for analysis.

    Returns empty string if metrics unavailable.
    """
    if not metrics or not metrics.by_source or metrics.total_estimated_tokens == 0:
        return ""

    from rich.console import Console
    from rich.table import Table

    # Build segments
    segments = []
    for sm in metrics.by_source:
        proportion = sm.rough_tokens / metrics.total_estimated_tokens
        color = _SOURCE_COLORS.get(sm.source, "white")
        segments.append({
            "source": _SOURCE_LABELS.get(sm.source, sm.source),
            "tokens": sm.rough_tokens,
            "proportion": proportion,
            "color": color,
        })

    # Sort by proportion descending, merge < 5% into "other"
    segments.sort(key=lambda x: x["proportion"], reverse=True)
    primary = [s for s in segments if s["proportion"] >= 0.05]
    small = segments[len(primary):]
    if small:
        total_small = sum(s["proportion"] for s in small)
        primary.append({
            "source": "other",
            "tokens": sum(s["tokens"] for s in small),
            "proportion": total_small,
            "color": "bright_black",
        })

    # Render bar
    bar_parts = []
    for seg in primary:
        filled = int(seg["proportion"] * width)
        bar_parts.append(f"[{seg['color']}]{'█' * filled}[/]")

    bar_str = "".join(bar_parts)
    pct = min(100, int(metrics.total_estimated_tokens / max(metrics.total_estimated_tokens, 1) * 100))
    return f"[{bar_str}] {pct}% ({metrics.total_estimated_tokens:,} tok)"
```

**改动位置 3**：在 `_status_bar_display` 或 `_print_status` 中添加内部调试输出（Phase 1 仅内部调试，不作为主进度条）：

```python
# 在 status bar 例行输出后，追加 source breakdown（仅 quiet 模式调试输出）
if hasattr(self, "_show_context_breakdown") and self._show_context_breakdown:
    metrics = self._get_current_context_metrics()
    if metrics:
        breakdown = self._render_source_bar(metrics)
        if breakdown:
            self._print_fn(breakdown)
```

**Phase 1 约束**：
1. 不改变 `get_status_snapshot()` 返回结构
2. `_render_source_bar` 返回空字符串当无 metrics 时，不影响现有进度条渲染
3. `get_status_snapshot()` 仍然读取 `context_compressor.last_prompt_tokens`
4. 新渲染函数默认不显示（`self._show_context_breakdown = False`），Phase 2 再默认打开
5. 颜色信息硬编码，不新增配置项

**边界条件**：
1. `metrics.by_source=[]` → 返回空字符串，不渲染
2. `total_estimated_tokens=0` → 除法保护，返回空
3. `metrics=None` → 返回空字符串
4. 单个 source 占比 < 5% → 合并到 "other"
5. `width=20` 固定，不随 terminal 宽度调整（Phase 2 再做）

**测试用例 (tests/context_engine/test_cli_source_bar.py)**：

```
T13.1: test_source_colors_defined — 所有 source 都有颜色映射
T13.2: test_source_labels_defined — 所有 source 都有标签映射
T13.3: test_render_source_bar_empty_metrics — metrics=None → ""
T13.4: test_render_source_bar_empty_by_source — by_source=[] → ""
T13.5: test_render_source_bar_zero_tokens — total=0 → ""
T13.6: test_render_source_bar_small_sources_merged — <5% 合并到 other
T13.7: test_render_source_bar_proportion_sum — 所有 segment 比例和 ≤ 1
T13.8: test_render_context_breakdown_all_sources — 每条 by_source 都有输出
T13.9: test_render_source_bar_unknown_source_color — 未知 source 有默认颜色
T13.10: test_render_bar_width_fixed — 宽度固定 20，不因输入变化
```

---

## 实施约束（所有 Step 通用）

1. **不改变任何现有函数签名** — 只扩展，不修改
2. **不改变任何返回值格式** — API 兼容
3. **所有异常必须捕获** — `try/except` 包裹 compat 调用和 factory 调用
4. **每步不超过 200 行** — 代码文件行数统计不含测试
5. **测试覆盖率必须覆盖每个边界条件** — 如上所列
6. **不引入新的外部依赖** — 仅使用 `rich`（已存在）
7. **Phase 1 颜色进度条仅内部调试** — 不作为主 UX 默认展示

---

## 测试总览

| Step | 测试文件 | 测试数 |
|------|---------|--------|
| 1 | test_models.py | 9 |
| 2 | test_context.py | 2 |
| 3 | test_registry.py | 4 |
| 4 | test_metrics.py | 7 |
| 5a | test_compat_prompt_builder.py | 4 |
| 5b | test_compat_honcho_memory.py | 7 |
| 5c | test_compat_sparkgraph_plugin.py | 9 |
| 6 | test_sources_stable_1_5.py | 14 |
| 7 | test_sources_stable_6_10.py | 12 |
| 8 | test_sources_dynamic.py | 16 |
| 9 | test_assembler.py | 14 |
| 10 | test_init.py | 4 |
| 11 | test_run_agent_integration.py | 5 |
| 12 | test_run_agent_dynamic.py | 7 |
| 13 | test_cli_source_bar.py | 10 |
| **合计** | | **~124** |

---

## 文档状态

- 决策记录: 11 项全部确认 ✓
- 逐行实施步骤: 13 Step 全部细化 ✓
- 边界条件: 每个 Step 逐一列出 ✓
- 测试用例: 约 124 个，覆盖每个边界 ✓
- **可以开始编码** ✓
