"""Context assembler — orchestrates sources and produces AssemblyResult."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import AssemblyResult, ContextChunk

if TYPE_CHECKING:
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


# ---------------------------------------------------------------------------
# Stable and dynamic source lists (in assembly order)
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
    ):
        self._agent = agent
        self._stable_factories = STABLE_FACTORIES if stable_factories is None else stable_factories
        self._dynamic_factories = DYNAMIC_FACTORIES if dynamic_factories is None else dynamic_factories

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
            except Exception:
                result = []
            if result:
                chunks.extend(result)
        return chunks

    def assemble_stable(self, system_message: str = None) -> AssemblyResult:
        """Assemble stable system prompt.

        Caching is handled by run_agent.py.
        """
        ctx = AssemblyContext(
            agent=self._agent,
            system_message=system_message,
            cwd=getattr(self._agent, "_context_cwd", None),
        )
        chunks = self._collect(self._stable_factories, ctx)
        return AssemblyResult.from_chunks(stable_chunks=chunks, dynamic_chunks=[])

    def assemble_dynamic(
        self,
        user_message: str = None,
        conversation_history: list = None,
    ) -> AssemblyResult:
        """Assemble dynamic system additions for current turn."""
        ctx = AssemblyContext(
            agent=self._agent,
            user_message=user_message,
            conversation_history=conversation_history or [],
        )
        chunks = self._collect(self._dynamic_factories, ctx)
        return AssemblyResult.from_chunks(stable_chunks=[], dynamic_chunks=chunks)

    def assemble_all(
        self,
        system_message: str = None,
        user_message: str = None,
        conversation_history: list = None,
    ) -> AssemblyResult:
        """Assemble both stable and dynamic in one call."""
        ctx = AssemblyContext(
            agent=self._agent,
            system_message=system_message,
            user_message=user_message,
            conversation_history=conversation_history or [],
            cwd=getattr(self._agent, "_context_cwd", None),
        )
        stable_chunks = self._collect(self._stable_factories, ctx)
        dynamic_chunks = self._collect(self._dynamic_factories, ctx)
        return AssemblyResult.from_chunks(
            stable_chunks=stable_chunks,
            dynamic_chunks=dynamic_chunks,
        )
