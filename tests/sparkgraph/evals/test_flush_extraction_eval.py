import json
from pathlib import Path
from types import SimpleNamespace

from agent.sparkgraph.flush_eval import (
    FlushEvalCase,
    build_flush_eval_messages,
    evaluate_flush_case,
    parse_sparkgraph_tool_items,
)


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "flush" / "main_model_cases.json"
)


def _load_cases():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [FlushEvalCase(**item) for item in payload]


def _tool_call(arguments: str):
    return SimpleNamespace(
        function=SimpleNamespace(name="sparkgraph_record", arguments=arguments)
    )


def test_flush_eval_fixture_schema_valid():
    cases = _load_cases()
    assert len(cases) >= 4
    assert cases[0].name == "FX-1 socksio fact"


def test_flush_eval_messages_append_real_flush_prompt():
    messages = build_flush_eval_messages(
        [{"role": "user", "content": "以后回答我尽量简洁。"}],
        include_memory=False,
        include_sparkgraph=True,
    )
    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    assert "sparkgraph_record" in messages[-1]["content"]
    assert "Do not use sparkgraph_record for greetings" in messages[-1]["content"]


def test_flush_eval_parser_extracts_items_from_tool_call():
    items = parse_sparkgraph_tool_items(
        [
            _tool_call(
                '{"items":[{"summary":"User prefers concise replies","type":"PREFERENCE","evidence":"以后回答我尽量简洁。"}]}'
            )
        ]
    )
    assert items == [
        {
            "summary": "User prefers concise replies",
            "type": "PREFERENCE",
            "evidence": "以后回答我尽量简洁。",
        }
    ]


def test_flush_eval_detects_empty_greeting_case():
    case = FlushEvalCase(
        name="greeting",
        messages=[{"role": "user", "content": "你好"}],
        min_items=0,
        max_items=0,
        allowed_empty=True,
    )
    ok, reason = evaluate_flush_case(case, [])
    assert ok, reason


def test_flush_eval_rejects_over_split_output():
    case = FlushEvalCase(
        name="issue",
        messages=[{"role": "user", "content": "PaddleOCR 报 libGL.so.1 错误"}],
        min_items=1,
        max_items=2,
        allowed_empty=False,
    )
    obj = [
        {"summary": "libGL errors can break OCR startup", "type": "ISSUE", "evidence": "ImportError: libGL.so.1"},
        {"summary": "Install libgl1-mesa-glx", "type": "RESOURCE", "evidence": "sudo apt install -y libgl1-mesa-glx"},
        {"summary": "System libraries matter", "type": "FACT", "evidence": "安装后恢复正常"},
    ]
    ok, reason = evaluate_flush_case(case, obj)
    assert ok is False
    assert "too many" in reason
