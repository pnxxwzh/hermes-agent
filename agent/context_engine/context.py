"""AssemblyContext — data passed to every ContextSource.collect()."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from run_agent import AIAgent


@dataclass
class AssemblyContext:
    """Immutable snapshot of everything a source needs to collect its chunks.

    Parameters
    ----------
    agent
        Reference to the active AIAgent instance.
    system_message
        Optional external system message provided at session start.
    user_message
        Current turn's user message (used for SparkGraph recall).
    cwd
        Working directory for context-file discovery.
    conversation_history
        Current message list (before system prefix is prepended).
    prefill_messages
        Request-time assistant prefill messages, if any.
    tool_schemas
        Tool schema payloads available for the current request.
    """

    agent: "AIAgent"
    system_message: str | None = None
    user_message: str | None = None
    cwd: str | None = None
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
    prefill_messages: list[dict[str, Any]] = field(default_factory=list)
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)
