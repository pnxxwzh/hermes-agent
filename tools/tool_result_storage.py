"""Tool result persistence -- preserve large outputs instead of truncating them."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from tools.budget_config import (
    DEFAULT_PREVIEW_SIZE_CHARS,
    BudgetConfig,
    DEFAULT_BUDGET,
)

logger = logging.getLogger(__name__)

PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
STORAGE_DIR = "/tmp/hermes-results"
HEREDOC_MARKER = "HERMES_PERSIST_EOF"
_BUDGET_TOOL_NAME = "__budget_enforcement__"


def generate_preview(content: str, max_chars: int = DEFAULT_PREVIEW_SIZE_CHARS) -> tuple[str, bool]:
    """Truncate at the last newline within max_chars and return (preview, has_more)."""
    if len(content) <= max_chars:
        return content, False
    truncated = content[:max_chars]
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars // 2:
        truncated = truncated[: last_nl + 1]
    return truncated, True


def is_persisted_output(content: Any) -> bool:
    """Return whether a tool result already carries the persisted preview wrapper."""
    if not isinstance(content, str):
        return False
    return PERSISTED_OUTPUT_TAG in content and PERSISTED_OUTPUT_CLOSING_TAG in content


def _heredoc_marker(content: str) -> str:
    """Return a heredoc delimiter that does not collide with content."""
    if HEREDOC_MARKER not in content:
        return HEREDOC_MARKER
    return f"HERMES_PERSIST_{uuid.uuid4().hex[:8]}"


def _write_to_sandbox(content: str, remote_path: str, env) -> bool:
    """Write content into the active sandbox via env.execute()."""
    marker = _heredoc_marker(content)
    cmd = (
        f"mkdir -p {STORAGE_DIR} && cat > {remote_path} << '{marker}'\n"
        f"{content}\n"
        f"{marker}"
    )
    result = env.execute(cmd, timeout=30)
    if not isinstance(result, dict):
        return False
    return result.get("returncode", 1) == 0


def _build_persisted_message(
    preview: str,
    has_more: bool,
    original_size: int,
    file_path: str,
) -> str:
    """Build the persisted preview block shown in model context."""
    size_kb = original_size / 1024
    if size_kb >= 1024:
        size_str = f"{size_kb / 1024:.1f} MB"
    else:
        size_str = f"{size_kb:.1f} KB"

    msg = f"{PERSISTED_OUTPUT_TAG}\n"
    msg += f"This tool result was too large ({original_size:,} characters, {size_str}).\n"
    msg += f"Full output saved to: {file_path}\n"
    msg += "Use the read_file tool with offset and limit to access specific sections of this output.\n\n"
    msg += f"Preview (first {len(preview)} chars):\n"
    msg += preview
    if has_more:
        msg += "\n..."
    msg += f"\n{PERSISTED_OUTPUT_CLOSING_TAG}"
    return msg


def _inline_truncation_message(preview: str, original_size: int) -> str:
    return (
        f"{preview}\n\n"
        f"[Truncated: tool response was {original_size:,} chars. "
        "Full output could not be saved to sandbox.]"
    )


def maybe_persist_tool_result(
    *,
    content: str,
    tool_name: str,
    tool_use_id: str,
    env=None,
    config: BudgetConfig = DEFAULT_BUDGET,
    threshold: int | float | None = None,
) -> tuple[str, str]:
    """Persist oversized results into sandbox and return (replacement, persistence_state)."""
    effective_threshold = threshold if threshold is not None else config.resolve_threshold(tool_name)
    if effective_threshold == float("inf"):
        return content, "inline"
    if len(content) <= effective_threshold:
        return content, "inline"

    remote_path = f"{STORAGE_DIR}/{tool_use_id}.txt"
    preview, has_more = generate_preview(content, max_chars=config.preview_size)

    if env is not None:
        try:
            if _write_to_sandbox(content, remote_path, env):
                logger.info(
                    "Persisted large tool result: %s (%s, %d chars -> %s)",
                    tool_name,
                    tool_use_id,
                    len(content),
                    remote_path,
                )
                return _build_persisted_message(preview, has_more, len(content), remote_path), "persisted_preview"
        except Exception as exc:
            logger.warning("Sandbox write failed for %s: %s", tool_use_id, exc)

    logger.info(
        "Inline-truncating large tool result: %s (%d chars, no sandbox write)",
        tool_name,
        len(content),
    )
    return _inline_truncation_message(preview, len(content)), "inline"


def enforce_turn_budget(
    tool_messages: list[dict],
    *,
    env=None,
    config: BudgetConfig = DEFAULT_BUDGET,
    persistence_by_tool_call_id: dict[str, str] | None = None,
) -> tuple[list[dict], dict[str, str]]:
    """Persist large results until the aggregate tool-message turn budget fits."""
    persistence_state = dict(persistence_by_tool_call_id or {})
    candidates: list[tuple[int, int]] = []
    total_size = 0
    for idx, msg in enumerate(tool_messages):
        content = str(msg.get("content", "") or "")
        size = len(content)
        total_size += size
        if not is_persisted_output(content):
            candidates.append((idx, size))

    if total_size <= config.turn_budget:
        return tool_messages, persistence_state

    candidates.sort(key=lambda item: item[1], reverse=True)
    for idx, size in candidates:
        if total_size <= config.turn_budget:
            break
        msg = tool_messages[idx]
        content = str(msg.get("content", "") or "")
        tool_use_id = str(msg.get("tool_call_id") or f"budget_{idx}")
        replacement, state = maybe_persist_tool_result(
            content=content,
            tool_name=_BUDGET_TOOL_NAME,
            tool_use_id=tool_use_id,
            env=env,
            config=config,
            threshold=0,
        )
        if replacement == content:
            continue
        total_size -= size
        total_size += len(replacement)
        msg["content"] = replacement
        persistence_state[tool_use_id] = "persisted_budget" if state.startswith("persisted") else state
        logger.info("Budget enforcement: persisted tool result %s (%d chars)", tool_use_id, size)

    return tool_messages, persistence_state
