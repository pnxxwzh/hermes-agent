from agent.sparkgraph.prompting import build_flush_prompt


def test_flush_prompt_memory_only():
    prompt = build_flush_prompt(include_memory=True, include_sparkgraph=False)
    assert "Save anything worth remembering" in prompt
    assert "sparkgraph_record" not in prompt


def test_flush_prompt_sparkgraph_only():
    prompt = build_flush_prompt(include_memory=False, include_sparkgraph=True)
    assert "sparkgraph_record" in prompt
    assert "Do not record greetings" in prompt
