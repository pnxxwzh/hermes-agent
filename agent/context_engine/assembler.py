"""Context assembler — orchestrates sources and produces AssemblyResult."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent.context_engine.context import AssemblyContext
from agent.context_engine.input_models import InputAssembly, InputNode
from agent.context_engine.models import AssemblyResult, ContextChunk
from agent.context_engine.registry import DYNAMIC_SOURCE_FACTORIES, STABLE_SOURCE_FACTORIES

if TYPE_CHECKING:
    from agent.context_engine.input_sources import (
        ConversationMessagesSource,
        PrefillMessagesSource,
        ToolSchemasSource,
    )
    from agent.context_engine.sources import (
        EphemeralSystemSource,
        HonchoStaticSource,
        HonchoTurnSource,
        IdentitySource,
        MemorySource,
        PluginTurnContextSource,
        ProjectContextSource,
        SkillsSource,
        SparkGraphRecallSource,
        SystemMessageSource,
        TimePlatformSource,
        ToolGuidanceSource,
        ToolUseEnforcementSource,
        UserProfileSource,
    )


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source factories (one per source, receives AssemblyContext)
# ---------------------------------------------------------------------------

def _identity_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import IdentitySource
    skip_context_files = bool(getattr(ctx.agent, "skip_context_files", False))
    source = IdentitySource(
        default_identity=None,  # uses compat constant
        ai_peer_name=getattr(ctx.agent, "_honcho_config", None)
        and getattr(ctx.agent._honcho_config, "ai_peer", None),
        load_soul=not skip_context_files,
    )
    return source.collect(ctx)


def _tool_guidance_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import ToolGuidanceSource
    tool_names = getattr(ctx.agent, "valid_tool_names", []) or []
    return ToolGuidanceSource(
        valid_tool_names=tool_names,
        preserve_legacy_memory_guidance=True,
    ).collect(ctx)


def _tool_enforcement_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import ToolUseEnforcementSource
    enforce = getattr(ctx.agent, "_tool_use_enforcement", None)
    model = getattr(ctx.agent, "model", None)
    tool_names = getattr(ctx.agent, "valid_tool_names", []) or []
    return ToolUseEnforcementSource(
        tool_use_enforcement=enforce,
        model=model,
        has_tools=bool(tool_names),
    ).collect(ctx)


def _honcho_static_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import HonchoStaticSource
    manager = getattr(ctx.agent, "_honcho", None)
    config = getattr(ctx.agent, "_honcho_config", None)
    ai_peer = config.ai_peer if config else None
    return HonchoStaticSource(
        honcho_session_manager=manager,
        honcho_config=config,
        ai_peer_name=ai_peer,
    ).collect(ctx)


def _system_message_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import SystemMessageSource
    msg = ctx.system_message
    return SystemMessageSource(system_message=msg).collect(ctx)


def _memory_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import MemorySource
    store = getattr(ctx.agent, "_memory_store", None)
    enabled = getattr(ctx.agent, "_memory_enabled", True)
    return MemorySource(memory_store=store, memory_enabled=enabled).collect(ctx)


def _user_profile_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import UserProfileSource
    store = getattr(ctx.agent, "_memory_store", None)
    enabled = getattr(ctx.agent, "_user_profile_enabled", True)
    return UserProfileSource(memory_store=store, user_profile_enabled=enabled).collect(ctx)


def _skills_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import SkillsSource
    tool_names = getattr(ctx.agent, "valid_tool_names", []) or []
    try:
        from run_agent import get_toolset_for_tool as _get_toolset_for_tool
    except Exception:
        _get_toolset_for_tool = None

    available_toolsets: set[str] = set()
    if _get_toolset_for_tool is not None:
        for tool_name in tool_names:
            toolset = _get_toolset_for_tool(tool_name)
            if toolset:
                available_toolsets.add(str(toolset))

    return SkillsSource(
        available_tools=tool_names,
        available_toolsets=available_toolsets,
    ).collect(ctx)


def _project_context_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import ProjectContextSource
    cwd = getattr(ctx.agent, "_context_cwd", None)
    skip_context_files = bool(getattr(ctx.agent, "skip_context_files", False))
    return ProjectContextSource(
        cwd=cwd,
        skip_soul=True,
        enabled=not skip_context_files,
    ).collect(ctx)


def _time_platform_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import TimePlatformSource
    return TimePlatformSource(
        model=getattr(ctx.agent, "model", None),
        provider=getattr(ctx.agent, "provider", None),
        session_id=getattr(ctx.agent, "session_id", None),
        platform=getattr(ctx.agent, "platform", None),
        pass_session_id=getattr(ctx.agent, "pass_session_id", False),
    ).collect(ctx)


def _ephemeral_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import EphemeralSystemSource

    def get_ephemeral():
        return getattr(ctx.agent, "ephemeral_system_prompt", "") or ""

    return EphemeralSystemSource(get_ephemeral_fn=get_ephemeral).collect(ctx)


def _plugin_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import PluginTurnContextSource
    sid = getattr(ctx.agent, "session_id", None)
    return PluginTurnContextSource(session_id=sid).collect(ctx)


def _sparkgraph_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import SparkGraphRecallSource
    manager = getattr(ctx.agent, "_sparkgraph_manager", None)
    enabled = getattr(ctx.agent, "_sparkgraph_enabled", False)
    return SparkGraphRecallSource(
        sparkgraph_manager=manager,
        sparkgraph_enabled=enabled,
        session_id=getattr(ctx.agent, "session_id", None),
    ).collect(ctx)


def _honcho_turn_factory(ctx: AssemblyContext) -> list[ContextChunk]:
    from agent.context_engine.sources import HonchoTurnSource
    manager = getattr(ctx.agent, "_honcho", None)
    return HonchoTurnSource(honcho_session_manager=manager).collect(ctx)


def _conversation_messages_factory(ctx: AssemblyContext) -> list[InputNode]:
    from agent.context_engine.input_sources import ConversationMessagesSource
    return ConversationMessagesSource().collect(ctx)


def _prefill_messages_factory(ctx: AssemblyContext) -> list[InputNode]:
    from agent.context_engine.input_sources import PrefillMessagesSource
    return PrefillMessagesSource().collect(ctx)


def _tool_schemas_factory(ctx: AssemblyContext) -> list[InputNode]:
    from agent.context_engine.input_sources import ToolSchemasSource
    return ToolSchemasSource().collect(ctx)


# ---------------------------------------------------------------------------
# Stable, dynamic, and request source lists (in assembly order)
# ---------------------------------------------------------------------------

STABLE_FACTORIES = [
    ("identity", _identity_factory),
    ("tool_guidance", _tool_guidance_factory),
    ("tool_use_enforcement", _tool_enforcement_factory),
    ("honcho_static", _honcho_static_factory),
    ("system_message", _system_message_factory),
    ("memory", _memory_factory),
    ("user_profile", _user_profile_factory),
    ("skills", _skills_factory),
    ("project_context", _project_context_factory),
    ("time_platform", _time_platform_factory),
]

DYNAMIC_FACTORIES = [
    ("ephemeral", _ephemeral_factory),
    ("plugin", _plugin_factory),
    ("sparkgraph_recall", _sparkgraph_factory),
    ("honcho_turn", _honcho_turn_factory),
]

REQUEST_FACTORIES = [
    ("conversation_messages", _conversation_messages_factory),
    ("prefill_messages", _prefill_messages_factory),
    ("tool_schemas", _tool_schemas_factory),
]


# ---------------------------------------------------------------------------
# ContextAssembler
# ---------------------------------------------------------------------------

class ContextAssembler:
    """Orchestrates all context sources to produce AssemblyResult."""

    def __init__(
        self,
        agent,
        stable_factories: list = None,
        dynamic_factories: list = None,
        request_factories: list = None,
    ):
        self._agent = agent
        self._stable_factories = (
            self._merge_registered_factories(STABLE_FACTORIES, STABLE_SOURCE_FACTORIES)
            if stable_factories is None
            else stable_factories
        )
        self._dynamic_factories = (
            self._merge_registered_factories(DYNAMIC_FACTORIES, DYNAMIC_SOURCE_FACTORIES)
            if dynamic_factories is None
            else dynamic_factories
        )
        self._request_factories = REQUEST_FACTORIES if request_factories is None else request_factories

    @staticmethod
    def _merge_registered_factories(
        base_factories: list,
        registered_factories: list,
    ) -> list:
        merged = list(base_factories)
        known_names = {name for name, _ in base_factories}
        for name, factory in registered_factories:
            if name in known_names:
                continue
            merged.append((name, factory))
            known_names.add(name)
        return merged

    def _collect(
        self,
        factories: list,
        ctx: AssemblyContext,
    ) -> list[ContextChunk]:
        """Collect chunks from all factories, skipping empty results."""
        chunks: list[ContextChunk] = []
        for name, factory in factories:
            try:
                result = factory(ctx)
            except Exception as exc:
                logger.warning(
                    "Context source '%s' failed during assembly: %s",
                    name,
                    exc,
                )
                result = []
            if result:
                chunks.extend(result)
        return chunks

    def _collect_input_nodes(
        self,
        factories: list,
        ctx: AssemblyContext,
    ) -> list[InputNode]:
        """Collect request-stage input nodes from all factories."""
        nodes: list[InputNode] = []
        for name, factory in factories:
            try:
                result = factory(ctx)
            except Exception as exc:
                logger.warning(
                    "Input source '%s' failed during assembly: %s",
                    name,
                    exc,
                )
                result = []
            if result:
                nodes.extend(result)
        return nodes

    @staticmethod
    def _build_effective_system(
        stable_chunks: list[ContextChunk],
        dynamic_chunks: list[ContextChunk],
    ) -> str:
        """Build the effective system prompt string from context chunks."""
        return AssemblyResult.from_chunks(
            stable_chunks=stable_chunks,
            dynamic_chunks=dynamic_chunks,
        ).effective_system

    @staticmethod
    def _build_normalized_messages(
        conversation_history: list | None,
    ) -> list[dict]:
        """Build a normalized, detached copy of semantic conversation messages."""
        if not conversation_history:
            return []
        normalized: list[dict] = []
        for message in conversation_history:
            if isinstance(message, dict):
                normalized.append(dict(message))
            else:
                normalized.append({"content": message})
        return normalized

    def assemble(
        self,
        *,
        system_message: str | None = None,
        user_message: str | None = None,
        conversation_history: list | None = None,
        prefill_messages: list | None = None,
        tool_schemas: list | None = None,
        _include_stable: bool = True,
        _include_dynamic: bool = True,
        _include_request: bool = True,
    ) -> InputAssembly:
        """Assemble the full semantic model input without transport shaping."""
        ctx = AssemblyContext(
            agent=self._agent,
            system_message=system_message,
            user_message=user_message,
            cwd=getattr(self._agent, "_context_cwd", None),
            conversation_history=conversation_history or [],
            prefill_messages=prefill_messages or [],
            tool_schemas=tool_schemas or [],
        )
        stable_chunks = self._collect(self._stable_factories, ctx) if _include_stable else []
        dynamic_chunks = self._collect(self._dynamic_factories, ctx) if _include_dynamic else []
        request_nodes = (
            self._collect_input_nodes(self._request_factories, ctx)
            if _include_request
            else []
        )
        stable_nodes = [InputNode.from_context_chunk(chunk) for chunk in stable_chunks]
        dynamic_nodes = [InputNode.from_context_chunk(chunk) for chunk in dynamic_chunks]
        return InputAssembly(
            stable_nodes=stable_nodes,
            dynamic_nodes=dynamic_nodes,
            request_nodes=request_nodes,
            effective_system=self._build_effective_system(stable_chunks, dynamic_chunks),
            normalized_messages=(
                self._build_normalized_messages(conversation_history)
                if _include_request
                else []
            ),
            prefill_messages=(
                self._build_normalized_messages(prefill_messages)
                if _include_request
                else []
            ),
            tool_schemas=list(tool_schemas or []) if _include_request else [],
        )

    def assemble_stable(self, system_message: str = None) -> AssemblyResult:
        """Assemble stable system prompt.

        Caching is handled by run_agent.py.
        """
        assembly = self.assemble(
            system_message=system_message,
            _include_dynamic=False,
            _include_request=False,
        )
        return AssemblyResult.from_chunks(
            stable_chunks=assembly.context_chunks("stable"),
            dynamic_chunks=[],
        )

    def assemble_dynamic(
        self,
        user_message: str = None,
        conversation_history: list = None,
    ) -> AssemblyResult:
        """Assemble dynamic system additions for current turn."""
        assembly = self.assemble(
            user_message=user_message,
            conversation_history=conversation_history,
            _include_stable=False,
            _include_request=False,
        )
        return AssemblyResult.from_chunks(
            stable_chunks=[],
            dynamic_chunks=assembly.context_chunks("dynamic"),
        )

    def assemble_all(
        self,
        system_message: str = None,
        user_message: str = None,
        conversation_history: list = None,
    ) -> AssemblyResult:
        """Assemble both stable and dynamic in one call."""
        assembly = self.assemble(
            system_message=system_message,
            user_message=user_message,
            conversation_history=conversation_history,
        )
        return AssemblyResult.from_chunks(
            stable_chunks=assembly.context_chunks("stable"),
            dynamic_chunks=assembly.context_chunks("dynamic"),
        )
