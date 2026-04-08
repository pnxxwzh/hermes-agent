"""Tool-group extraction and heat budgeting for request-time compaction."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal


def _safe_len(value: Any) -> int:
    if value is None:
        return 0
    try:
        return len(str(value))
    except Exception:
        try:
            return len(repr(type(value)))
        except Exception:
            return 0


@dataclass
class ToolGroup:
    """One assistant tool-call turn plus its following tool result messages."""

    turn_index: int
    assistant_index: int
    tool_start_index: int
    tool_end_index: int
    assistant_message: dict[str, Any]
    tool_messages: list[dict[str, Any]]
    char_count: int
    heat: Literal["hot", "warm", "cold"] = "hot"
    reduction: Literal["full", "compact", "summary"] = "full"


@dataclass
class ToolCompactionConfig:
    """Runtime configuration for request-view tool-output shaping."""

    enabled: bool = True
    retain_recent_user_turns: int = 3
    retain_recent_tool_groups_in_turn: int = 2
    current_turn_tool_budget_chars: int = 12000
    historical_tool_budget_chars: int = 24000
    warm_head_chars: int = 1200
    warm_tail_chars: int = 400
    cold_head_chars: int = 400
    cold_tail_chars: int = 200


def build_tool_groups(messages: list[dict[str, Any]]) -> list[ToolGroup]:
    """Group assistant tool-calls with their contiguous tool result messages."""
    groups: list[ToolGroup] = []
    current_turn = 0
    idx = 0
    while idx < len(messages):
        message = messages[idx]
        role = str(message.get("role", "") or "").strip().lower()
        if role == "user":
            current_turn += 1
            idx += 1
            continue
        if role == "assistant" and message.get("tool_calls"):
            tool_messages: list[dict[str, Any]] = []
            cursor = idx + 1
            while cursor < len(messages):
                candidate = messages[cursor]
                if str(candidate.get("role", "") or "").strip().lower() != "tool":
                    break
                tool_messages.append(candidate)
                cursor += 1
            groups.append(
                ToolGroup(
                    turn_index=current_turn,
                    assistant_index=idx,
                    tool_start_index=idx + 1,
                    tool_end_index=cursor - 1 if tool_messages else idx,
                    assistant_message=message,
                    tool_messages=list(tool_messages),
                    char_count=sum(_safe_len(tool.get("content")) for tool in tool_messages),
                )
            )
            idx = cursor
            continue
        idx += 1
    return groups


def apply_tool_heat_budget(
    groups: list[ToolGroup],
    config: ToolCompactionConfig,
) -> list[ToolGroup]:
    """Assign hot/warm/cold heat levels to groups based on recency and budgets."""
    if not groups:
        return []

    latest_turn = max(group.turn_index for group in groups)
    current_groups = [replace(group) for group in groups if group.turn_index == latest_turn]
    historical_groups = [replace(group) for group in groups if group.turn_index != latest_turn]
    updated: dict[tuple[int, int], ToolGroup] = {}

    strong_keep = max(1, int(config.retain_recent_tool_groups_in_turn))
    current_budget = max(0, int(config.current_turn_tool_budget_chars))
    used_current = 0
    for reverse_idx, group in enumerate(reversed(current_groups)):
        is_recent = reverse_idx < strong_keep
        next_used = used_current + group.char_count
        if is_recent or next_used <= current_budget:
            group.heat = "hot"
            group.reduction = "full"
            used_current = next_used
        else:
            group.heat = "warm"
            group.reduction = "compact"
        updated[(group.turn_index, group.assistant_index)] = group

    if historical_groups:
        recent_turns = max(1, int(config.retain_recent_user_turns))
        oldest_warm_turn = max(0, latest_turn - recent_turns)
        historical_budget = max(0, int(config.historical_tool_budget_chars))
        used_history = 0
        for group in reversed(historical_groups):
            keep_warm = group.turn_index > oldest_warm_turn
            next_used = used_history + group.char_count
            if keep_warm and next_used <= historical_budget:
                group.heat = "warm"
                group.reduction = "compact"
                used_history = next_used
            else:
                group.heat = "cold"
                group.reduction = "summary"
            updated[(group.turn_index, group.assistant_index)] = group

    ordered: list[ToolGroup] = []
    for group in groups:
        ordered.append(updated.get((group.turn_index, group.assistant_index), replace(group)))
    return ordered
