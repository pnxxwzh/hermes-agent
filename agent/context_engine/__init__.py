"""Context Engine — unified context assembly and metrics.

Phase 1: zero behavior change, unified metrics, backward compatible.
"""

from agent.context_engine.assembler import ContextAssembler
from agent.context_engine.context import AssemblyContext
from agent.context_engine.input_models import AssemblySnapshots, InputAssembly, InputNode
from agent.context_engine.input_sources import (
    ConversationMessagesSource,
    PrefillMessagesSource,
    ToolSchemasSource,
)
from agent.context_engine.models import (
    AssemblyResult,
    ContextChunk,
    ContextMetrics,
    SourceMetrics,
)
from agent.context_engine.request_metrics import (
    RequestBucketMetrics,
    RequestMetrics,
    build_request_metrics,
    rough_tokens_from_message,
    rough_tokens_from_text,
)
from agent.context_engine.registry import (
    DYNAMIC_SOURCE_FACTORIES,
    STABLE_SOURCE_FACTORIES,
    register_dynamic,
    register_stable,
)
from agent.context_engine.transport import (
    AnthropicMessagesTransportAdapter,
    ChatCompletionsTransportAdapter,
    CodexResponsesTransportAdapter,
    TransportAdapter,
    TransportPayload,
    get_transport_adapter,
)

__all__ = [
    # Models
    "ContextChunk",
    "ContextMetrics",
    "SourceMetrics",
    "AssemblyResult",
    "InputNode",
    "InputAssembly",
    "AssemblySnapshots",
    "RequestBucketMetrics",
    "RequestMetrics",
    "TransportAdapter",
    "TransportPayload",
    "ChatCompletionsTransportAdapter",
    "CodexResponsesTransportAdapter",
    "AnthropicMessagesTransportAdapter",
    # Context
    "AssemblyContext",
    # Input sources
    "ConversationMessagesSource",
    "PrefillMessagesSource",
    "ToolSchemasSource",
    # Request metrics
    "build_request_metrics",
    "rough_tokens_from_message",
    "rough_tokens_from_text",
    "get_transport_adapter",
    # Registry
    "STABLE_SOURCE_FACTORIES",
    "DYNAMIC_SOURCE_FACTORIES",
    "register_stable",
    "register_dynamic",
    # Assembler
    "ContextAssembler",
    "ASSEMBLER",
    "get_assembler",
]


# Shared default assembler instance (lazily initialized)
ASSEMBLER = None


def get_assembler(agent) -> ContextAssembler:
    """Get or create a ContextAssembler for the given agent."""
    global ASSEMBLER
    if ASSEMBLER is None or getattr(ASSEMBLER, "_agent", None) is not agent:
        ASSEMBLER = ContextAssembler(agent)
    return ASSEMBLER
