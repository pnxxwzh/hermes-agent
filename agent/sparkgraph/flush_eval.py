"""Evaluation helpers for main-model SparkGraph flush extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.sparkgraph.prompting import build_flush_prompt
from hermes_constants import get_hermes_home


ALLOWED_TYPES = {"FACT", "PREFERENCE", "ISSUE", "RESOURCE", "DECISION"}


@dataclass(frozen=True)
class FlushEvalCase:
    name: str
    messages: list[dict[str, str]]
    min_items: int
    max_items: int
    allowed_empty: bool = False


def default_flush_eval_report_path(hermes_home: Path | None = None) -> Path:
    return (hermes_home or get_hermes_home()) / "sparkgraph" / "evals" / "flush-last.json"


def load_flush_eval_report(
    path: Path | None = None,
    *,
    hermes_home: Path | None = None,
) -> dict[str, Any] | None:
    report_path = path or default_flush_eval_report_path(hermes_home)
    if not report_path.exists():
        return None
    return json.loads(report_path.read_text(encoding="utf-8"))


def build_flush_eval_messages(
    messages: list[dict[str, str]],
    *,
    cached_system_prompt: str = "You are helpful.",
    include_memory: bool = False,
    include_sparkgraph: bool = True,
) -> list[dict[str, str]]:
    api_messages: list[dict[str, str]] = []
    if cached_system_prompt:
        api_messages.append({"role": "system", "content": cached_system_prompt})
    api_messages.extend({"role": msg["role"], "content": msg["content"]} for msg in messages)
    api_messages.append(
        {
            "role": "user",
            "content": build_flush_prompt(
                include_memory=include_memory,
                include_sparkgraph=include_sparkgraph,
            ),
        }
    )
    return api_messages


def parse_sparkgraph_tool_items(tool_calls: list[Any] | None) -> list[dict[str, Any]] | None:
    if not tool_calls:
        return []
    for tc in tool_calls:
        function = getattr(tc, "function", None)
        if function is None and isinstance(tc, dict):
            function = tc.get("function")
        if function is None:
            continue

        name = getattr(function, "name", None)
        arguments = getattr(function, "arguments", None)
        if isinstance(function, dict):
            name = function.get("name")
            arguments = function.get("arguments")

        if name != "sparkgraph_record":
            continue

        try:
            payload = json.loads(arguments or "{}")
        except Exception:
            return None
        items = payload.get("items")
        return items if isinstance(items, list) else None
    return []


def validate_flush_items(obj: list[dict[str, Any]] | None) -> tuple[bool, str]:
    if obj is None:
        return False, "tool payload parse failed"
    if not isinstance(obj, list):
        return False, "items is not a list"
    for item in obj:
        if not isinstance(item, dict):
            return False, "item is not an object"
        for field in ("summary", "type", "evidence"):
            if field not in item:
                return False, f"missing field {field}"
        if item["type"] not in ALLOWED_TYPES:
            return False, f"invalid type {item['type']}"
    return True, "OK"


def evaluate_flush_case(case: FlushEvalCase, obj: list[dict[str, Any]] | None) -> tuple[bool, str]:
    ok, reason = validate_flush_items(obj)
    if not ok:
        return False, reason
    count = len(obj)
    if case.allowed_empty and count == 0:
        return True, "OK"
    if count < case.min_items:
        return False, f"items too few: {count}"
    if count > case.max_items:
        return False, f"items too many: {count}"
    return True, "OK"
