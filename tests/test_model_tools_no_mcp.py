"""Tests for config-only no_mcp sentinel behavior."""

from unittest.mock import patch

from model_tools import get_tool_definitions


def test_get_tool_definitions_excludes_mcp_when_no_mcp_is_resolved_away():
    tool_defs = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "search",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mcp_exa_search",
                "description": "mcp search",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    def _fake_get_definitions(tools_to_include, quiet=False):
        return [
            tool
            for tool in tool_defs
            if tool["function"]["name"] in tools_to_include
        ]

    with patch("model_tools.registry.get_definitions", side_effect=_fake_get_definitions), patch(
        "model_tools.validate_toolset",
        side_effect=lambda name: name in {"web"},
    ), patch(
        "model_tools.resolve_toolset",
        side_effect=lambda name: {"web_search"} if name == "web" else set(),
    ):
        resolved = get_tool_definitions(enabled_toolsets=["web"], quiet_mode=True)

    names = [tool["function"]["name"] for tool in resolved]
    assert names == ["web_search"]
