"""Tests for SparkGraphManager recall feedback behavior."""

from unittest.mock import MagicMock, patch

from agent.sparkgraph.manager import SparkGraphManager


def test_build_recall_block_only_updates_included_nodes(tmp_path):
    manager = SparkGraphManager.from_raw_config({}, hermes_home=tmp_path)
    store = MagicMock()
    manager.store = store

    recalled_nodes = [
        {"id": "n1", "summary": "first"},
        {"id": "n2", "summary": "second"},
    ]

    with (
        patch("agent.sparkgraph.manager.recall_nodes", return_value=(recalled_nodes, [], 12)) as mock_recall,
        patch(
            "agent.sparkgraph.manager.build_recall_payload",
            return_value=("[SparkGraph Recall]\n- [FACT] first", ["n1"]),
        ),
        patch("agent.sparkgraph.manager.apply_recall_feedback") as mock_feedback,
    ):
        block, token_estimate = manager.build_recall_block("query", max_chars=40, session_id="sess-1")

    assert block.startswith("[SparkGraph Recall]")
    assert token_estimate == 12
    mock_feedback.assert_called_once_with(
        store,
        [{"id": "n1", "summary": "first"}],
        session_id="sess-1",
    )
    assert mock_recall.call_args.kwargs["persist_feedback"] is False
    store.mark_recalled.assert_called_once_with(["n1"])
