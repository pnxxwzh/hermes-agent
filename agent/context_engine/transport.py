"""Transport adapters for provider/API-mode payload shaping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.prompt_caching import apply_anthropic_cache_control


@dataclass
class TransportPayload:
    """A transport-ready snapshot derived from an InputAssembly."""

    api_messages: list[dict[str, Any]]
    api_kwargs: dict[str, Any]


class TransportAdapter:
    """Build provider payloads from a semantic InputAssembly."""

    api_mode = "chat_completions"

    def build_api_messages(
        self,
        assembly,
        agent,
        *,
        current_turn_user_idx: int | None = None,
        honcho_turn_context: str = "",
    ) -> list[dict[str, Any]]:
        api_messages: list[dict[str, Any]] = []
        for idx, msg in enumerate(assembly.normalized_messages):
            if not isinstance(msg, dict):
                continue
            api_msg = dict(msg)

            if (
                current_turn_user_idx is not None
                and idx == current_turn_user_idx
                and msg.get("role") == "user"
                and honcho_turn_context
            ):
                from run_agent import _inject_honcho_turn_context

                api_msg["content"] = _inject_honcho_turn_context(
                    api_msg.get("content", ""),
                    honcho_turn_context,
                )

            if msg.get("role") == "assistant":
                reasoning_text = msg.get("reasoning")
                if reasoning_text:
                    api_msg["reasoning_content"] = reasoning_text

            api_msg.pop("reasoning", None)
            api_msg.pop("finish_reason", None)

            if "api.mistral.ai" in getattr(agent, "_base_url_lower", ""):
                agent._sanitize_tool_calls_for_strict_api(api_msg)

            api_messages.append(api_msg)

        if assembly.effective_system:
            api_messages = [{"role": "system", "content": assembly.effective_system}] + api_messages

        if assembly.prefill_messages:
            sys_offset = 1 if assembly.effective_system else 0
            for idx, message in enumerate(assembly.prefill_messages):
                api_messages.insert(sys_offset + idx, dict(message))

        if getattr(agent, "_use_prompt_caching", False):
            api_messages = apply_anthropic_cache_control(
                api_messages,
                cache_ttl=getattr(agent, "_cache_ttl", None),
                native_anthropic=(getattr(agent, "api_mode", "") == "anthropic_messages"),
            )

        return agent._sanitize_api_messages(api_messages)

    def build_api_kwargs(
        self,
        assembly,
        agent,
        *,
        current_turn_user_idx: int | None = None,
        honcho_turn_context: str = "",
    ) -> dict[str, Any]:
        api_messages = self.build_api_messages(
            assembly,
            agent,
            current_turn_user_idx=current_turn_user_idx,
            honcho_turn_context=honcho_turn_context,
        )
        original_tools = getattr(agent, "tools", None)
        try:
            agent.tools = list(assembly.tool_schemas or [])
            return agent._build_api_kwargs(api_messages)
        finally:
            agent.tools = original_tools

    def build_payload(
        self,
        assembly,
        agent,
        *,
        current_turn_user_idx: int | None = None,
        honcho_turn_context: str = "",
    ) -> TransportPayload:
        api_messages = self.build_api_messages(
            assembly,
            agent,
            current_turn_user_idx=current_turn_user_idx,
            honcho_turn_context=honcho_turn_context,
        )
        original_tools = getattr(agent, "tools", None)
        try:
            agent.tools = list(assembly.tool_schemas or [])
            api_kwargs = agent._build_api_kwargs(api_messages)
        finally:
            agent.tools = original_tools
        return TransportPayload(
            api_messages=api_messages,
            api_kwargs=api_kwargs,
        )


class ChatCompletionsTransportAdapter(TransportAdapter):
    api_mode = "chat_completions"


class CodexResponsesTransportAdapter(TransportAdapter):
    api_mode = "codex_responses"


class AnthropicMessagesTransportAdapter(TransportAdapter):
    api_mode = "anthropic_messages"


def get_transport_adapter(api_mode: str | None) -> TransportAdapter:
    """Return the transport adapter for the requested API mode."""
    mode = str(api_mode or "chat_completions").strip().lower()
    if mode == "codex_responses":
        return CodexResponsesTransportAdapter()
    if mode == "anthropic_messages":
        return AnthropicMessagesTransportAdapter()
    return ChatCompletionsTransportAdapter()
