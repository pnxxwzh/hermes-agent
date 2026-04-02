from agent.sparkgraph.formatter import format_recall_block


def test_format_recall_block_includes_header_and_nodes():
    block = format_recall_block(
        [
            {"type": "FACT", "summary": "socksio may be required for SOCKS proxy support"},
            {"type": "PREFERENCE", "summary": "User prefers concise replies"},
        ]
    )
    assert "[SparkGraph Recall]" in block
    assert "[FACT]" in block
    assert "[PREFERENCE]" in block


def test_format_recall_block_respects_char_budget():
    block = format_recall_block(
        [
            {"type": "FACT", "summary": "A" * 200},
            {"type": "FACT", "summary": "B" * 200},
        ],
        max_chars=180,
    )
    assert block.count("- [") == 0 or block.count("- [") == 1


def test_format_recall_block_returns_empty_when_no_valid_nodes():
    block = format_recall_block([{"summary": "", "type": ""}])
    assert block == ""
