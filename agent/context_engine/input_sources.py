"""Request-level input sources for the unified context engine."""

from __future__ import annotations

from typing import Any

from agent.context_engine.context import AssemblyContext
from agent.context_engine.input_models import InputNode
from agent.context_engine.request_metrics import rough_tokens_from_message


def _safe_char_count(value: Any) -> int:
    if value is None:
        return 0
    try:
        return len(str(value))
    except Exception:
        try:
            return len(repr(type(value)))
        except Exception:
            return 0


def _message_bucket(message: dict[str, Any]) -> str:
    role = str(message.get("role", "") or "").strip().lower()
    if role == "user":
        return "messages_user"
    if role == "assistant":
        return "messages_assistant"
    if role == "tool":
        return "messages_tool"
    return "messages_other"


class ConversationMessagesSource:
    """Convert conversation history into request-stage message nodes."""

    name = "conversation_messages"
    stage = "request"

    def collect(self, ctx: AssemblyContext) -> list[InputNode]:
        nodes: list[InputNode] = []
        for index, message in enumerate(ctx.conversation_history or []):
            payload = dict(message) if isinstance(message, dict) else {"content": message}
            bucket = _message_bucket(payload)
            nodes.append(InputNode(
                kind="message",
                name=bucket,
                stage="request",
                content=payload,
                char_count=_safe_char_count(payload),
                rough_tokens=rough_tokens_from_message(payload),
                metadata={
                    "index": index,
                    "role": payload.get("role"),
                },
            ))
        return nodes


class PrefillMessagesSource:
    """Convert prefill messages into request-stage prefill nodes."""

    name = "prefill_messages"
    stage = "request"

    def collect(self, ctx: AssemblyContext) -> list[InputNode]:
        nodes: list[InputNode] = []
        for index, message in enumerate(ctx.prefill_messages or []):
            payload = dict(message) if isinstance(message, dict) else {"content": message}
            nodes.append(InputNode(
                kind="prefill",
                name="prefill_messages",
                stage="request",
                content=payload,
                char_count=_safe_char_count(payload),
                rough_tokens=rough_tokens_from_message(payload),
                metadata={"index": index},
            ))
        return nodes


class ToolSchemasSource:
    """Convert tool schemas into request-stage tool-schema nodes."""

    name = "tool_schemas"
    stage = "request"

    def collect(self, ctx: AssemblyContext) -> list[InputNode]:
        if not ctx.tool_schemas:
            return []
        payload = list(ctx.tool_schemas)
        return [InputNode(
            kind="tool_schema",
            name="tool_schemas",
            stage="request",
            content=payload,
            char_count=_safe_char_count(payload),
            rough_tokens=_safe_char_count(payload) // 4,
            metadata={"count": len(payload)},
        )]
