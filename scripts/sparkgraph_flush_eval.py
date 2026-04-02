#!/usr/bin/env python3
"""Run SparkGraph flush extraction evals against a live OpenAI-compatible model."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI

from agent.sparkgraph.flush_eval import (
    FlushEvalCase,
    build_flush_eval_messages,
    default_flush_eval_report_path,
    evaluate_flush_case,
    load_flush_eval_report,
    parse_sparkgraph_tool_items,
)
from hermes_cli.config import load_config
from hermes_cli.runtime_provider import resolve_runtime_provider
from tools.sparkgraph_tool import SPARKGRAPH_RECORD_SCHEMA


FIXTURE_PATH = PROJECT_ROOT / "tests" / "sparkgraph" / "evals" / "fixtures" / "flush" / "main_model_cases.json"


def load_cases() -> list[FlushEvalCase]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [FlushEvalCase(**item) for item in payload]


def _default_model_from_config(config: dict[str, Any]) -> str:
    model_cfg = config.get("model")
    if isinstance(model_cfg, dict):
        return str(model_cfg.get("default") or model_cfg.get("model") or "").strip()
    if isinstance(model_cfg, str):
        return model_cfg.strip()
    return ""


def _normalize_eval_provider(provider: str) -> str:
    normalized = (provider or "").strip().lower()
    aliases = {
        "openai-compatible": "custom",
        "local-openai-compatible": "custom",
        "local": "custom",
    }
    return aliases.get(normalized, normalized)


def resolve_eval_runtime(
    *,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    provider: str = "",
) -> dict[str, str]:
    config = load_config()
    resolved_model = (model or _default_model_from_config(config)).strip()
    if not resolved_model:
        raise ValueError("No model configured. Pass --model or configure model.default.")

    runtime = resolve_runtime_provider(
        requested=_normalize_eval_provider(provider) or None,
        explicit_api_key=api_key or None,
        explicit_base_url=base_url or None,
    )
    resolved_base_url = str(runtime.get("base_url") or "").strip()
    if not resolved_base_url:
        raise ValueError("No runtime base URL resolved. Pass --base-url or configure the active provider.")

    resolved_api_key = str(runtime.get("api_key") or "").strip() or "no-key-required"
    resolved_provider = str(runtime.get("provider") or provider or "").strip()
    return {
        "model": resolved_model,
        "base_url": resolved_base_url,
        "api_key": resolved_api_key,
        "provider": resolved_provider,
        "api_mode": str(runtime.get("api_mode") or "").strip(),
        "source": str(runtime.get("source") or "").strip(),
    }


def default_report_path() -> Path:
    return default_flush_eval_report_path()


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def load_report(path: Path) -> dict[str, Any] | None:
    return load_flush_eval_report(path)


def compare_summaries(current: dict[str, Any], previous: dict[str, Any] | None) -> str:
    if not previous:
        return "No previous report to compare."
    prev_summary = previous.get("summary") if isinstance(previous, dict) else None
    if not isinstance(prev_summary, dict):
        return "Previous report missing summary."

    current_passed = int(current.get("passed", 0))
    current_total = int(current.get("total", 0))
    prev_passed = int(prev_summary.get("passed", 0))
    prev_total = int(prev_summary.get("total", 0))
    delta_passed = current_passed - prev_passed
    delta_total = current_total - prev_total
    return (
        "Comparison to previous report:"
        f" passed {prev_passed}->{current_passed} ({delta_passed:+d}),"
        f" total {prev_total}->{current_total} ({delta_total:+d})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SparkGraph flush extraction evals.")
    parser.add_argument("--model", default="", help="Model name. Defaults to current Hermes model.default.")
    parser.add_argument(
        "--provider",
        default="",
        help="Optional provider override for runtime resolution. Use 'custom' for local OpenAI-compatible endpoints.",
    )
    parser.add_argument("--base-url", default="", help="OpenAI-compatible base URL. Defaults to resolved active runtime.")
    parser.add_argument("--api-key", default="", help="API key for the endpoint. Defaults to resolved active runtime.")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=800)
    parser.add_argument("--write-report", action="store_true", help="Persist the eval summary to the current Hermes profile.")
    parser.add_argument("--report-path", default="", help="Optional custom report path.")
    parser.add_argument("--compare-last", action="store_true", help="Compare the current summary against the previous saved report.")
    args = parser.parse_args()

    runtime = resolve_eval_runtime(
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        provider=args.provider,
    )
    client = OpenAI(base_url=runtime["base_url"], api_key=runtime["api_key"])
    cases = load_cases()

    print(
        "Using runtime:"
        f" provider={runtime['provider'] or '(unknown)'}"
        f" source={runtime['source'] or '(unknown)'}"
        f" api_mode={runtime['api_mode'] or '(default)'}"
        f" model={runtime['model']}"
        f" base_url={runtime['base_url']}"
    )

    passed = 0
    case_results: list[dict[str, Any]] = []
    for case in cases:
        messages = build_flush_eval_messages(case.messages)
        response = client.chat.completions.create(
            model=runtime["model"],
            messages=messages,
            tools=[{"type": "function", "function": SPARKGRAPH_RECORD_SCHEMA}],
            tool_choice="auto",
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
        tool_calls = response.choices[0].message.tool_calls
        items = parse_sparkgraph_tool_items(tool_calls)
        ok, reason = evaluate_flush_case(case, items)
        if ok:
            passed += 1
        case_results.append(
            {
                "name": case.name,
                "passed": ok,
                "reason": reason,
                "items_count": len(items) if isinstance(items, list) else None,
            }
        )
        print(f"[{'PASS' if ok else 'FAIL'}] {case.name}: {reason}")

    total = len(cases)
    print(f"\nSummary: {passed}/{total} passed")
    report_path = Path(args.report_path).expanduser() if args.report_path else default_report_path()
    if args.compare_last:
        previous = load_report(report_path)
        print(compare_summaries({"passed": passed, "total": total}, previous))
    if args.write_report:
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runtime": {
                "provider": runtime["provider"],
                "source": runtime["source"],
                "api_mode": runtime["api_mode"],
                "model": runtime["model"],
                "base_url": runtime["base_url"],
            },
            "settings": {
                "temperature": args.temperature,
                "max_tokens": args.max_tokens,
            },
            "summary": {
                "passed": passed,
                "total": total,
            },
            "cases": case_results,
        }
        write_report(report, report_path)
        print(f"Report saved to: {report_path}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
