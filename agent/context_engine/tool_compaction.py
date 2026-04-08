"""Request-view shaping for tool-heavy message histories."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from agent.context_engine.tool_groups import (
    ToolCompactionConfig,
    ToolGroup,
    apply_tool_heat_budget,
    build_tool_groups,
)
from tools.tool_result_storage import is_persisted_output


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def truncate_head_tail(text: str, head_chars: int, tail_chars: int) -> str:
    """Keep the head and tail of a long payload and collapse the middle."""
    text = text or ""
    head_chars = max(0, int(head_chars))
    tail_chars = max(0, int(tail_chars))
    marker_budget = 64
    if len(text) <= head_chars + tail_chars + marker_budget:
        return text
    head = text[:head_chars]
    tail = text[-tail_chars:] if tail_chars > 0 else ""
    omitted = max(0, len(text) - len(head) - len(tail))
    marker = f"\n\n...[truncated {omitted} chars]...\n\n"
    return f"{head}{marker}{tail}"


def compact_tool_group(
    group: ToolGroup,
    config: ToolCompactionConfig,
) -> list[dict[str, Any]]:
    """Return request-view tool messages for the given group heat level."""
    if group.reduction == "full":
        return [dict(message) for message in group.tool_messages]

    if group.reduction == "compact":
        head_chars = config.warm_head_chars
        tail_chars = config.warm_tail_chars
    else:
        head_chars = config.cold_head_chars
        tail_chars = config.cold_tail_chars

    compacted: list[dict[str, Any]] = []
    for message in group.tool_messages:
        compacted_message = dict(message)
        content = _coerce_text(message.get("content"))
        if is_persisted_output(content):
            compacted_message["content"] = content
        else:
            compacted_message["content"] = truncate_head_tail(
                content,
                head_chars=head_chars,
                tail_chars=tail_chars,
            )
        compacted.append(compacted_message)
    return compacted


@dataclass
class ShapedToolHistory:
    """Request-view snapshot after tool heat/reduction has been applied."""

    shaped_messages: list[dict[str, Any]]
    hot_groups: list[ToolGroup]
    warm_groups: list[ToolGroup]
    cold_groups: list[ToolGroup]
    message_heat_by_index: dict[int, str]
    message_persistence_by_index: dict[int, str]


def shape_tool_history(
    messages: list[dict[str, Any]],
    config: ToolCompactionConfig,
) -> ShapedToolHistory:
    """Build a request-view message list with tool outputs compacted by heat."""
    if not config.enabled:
        return ShapedToolHistory(
            shaped_messages=[dict(message) for message in messages],
            hot_groups=[],
            warm_groups=[],
            cold_groups=[],
            message_heat_by_index={},
            message_persistence_by_index={},
        )

    groups = apply_tool_heat_budget(build_tool_groups(messages), config)
    group_by_assistant_index = {group.assistant_index: group for group in groups}
    shaped_messages: list[dict[str, Any]] = []
    message_heat_by_index: dict[int, str] = {}
    message_persistence_by_index: dict[int, str] = {}
    source_idx = 0
    while source_idx < len(messages):
        group = group_by_assistant_index.get(source_idx)
        if group is None:
            shaped_messages.append(dict(messages[source_idx]))
            source_idx += 1
            continue

        shaped_messages.append(dict(messages[source_idx]))
        compacted_tool_messages = compact_tool_group(group, config)
        next_index = len(shaped_messages)
        for tool_message in compacted_tool_messages:
            shaped_messages.append(tool_message)
            message_heat_by_index[next_index] = group.heat
            content = tool_message.get("content")
            if is_persisted_output(content):
                message_persistence_by_index[next_index] = "persisted_preview"
            else:
                message_persistence_by_index[next_index] = "inline"
            next_index += 1
        source_idx = group.tool_end_index + 1 if group.tool_messages else source_idx + 1

    hot_groups = [group for group in groups if group.heat == "hot"]
    warm_groups = [group for group in groups if group.heat == "warm"]
    cold_groups = [group for group in groups if group.heat == "cold"]
    return ShapedToolHistory(
        shaped_messages=shaped_messages,
        hot_groups=hot_groups,
        warm_groups=warm_groups,
        cold_groups=cold_groups,
        message_heat_by_index=message_heat_by_index,
        message_persistence_by_index=message_persistence_by_index,
    )
