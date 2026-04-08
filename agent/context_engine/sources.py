"""Context sources for Phase 1 — stable sources part 1.

Stable sources (in assembly order):
  1. IdentitySource
  2. ToolGuidanceSource
  3. ToolUseEnforcementSource
  4. HonchoStaticSource
  5. SystemMessageSource
"""

from __future__ import annotations

from typing import Literal

from agent.prompt_builder import (
    MEMORY_GUIDANCE,
    SESSION_SEARCH_GUIDANCE,
    SKILLS_GUIDANCE,
    TOOL_USE_ENFORCEMENT_GUIDANCE,
    TOOL_USE_ENFORCEMENT_MODELS,
)

from agent.context_engine.compat import (
    wrap_honcho_static_block,
    wrap_load_soul_md,
)
from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import ContextChunk


# ---------------------------------------------------------------------------
# Source 1: IdentitySource
# ---------------------------------------------------------------------------

class IdentitySource:
    """Agent identity: SOUL.md content or DEFAULT_AGENT_IDENTITY fallback."""

    name = "identity"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        default_identity: str = None,
        ai_peer_name: str | None = None,
    ):
        self._default = default_identity or "You are Hermes Agent"
        self._ai_peer = ai_peer_name

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        soul_content, _ = wrap_load_soul_md()
        has_soul = bool(soul_content)

        if has_soul:
            content = soul_content
            slot = "soul"
        else:
            if self._ai_peer:
                content = self._default.replace(
                    "You are Hermes Agent", f"You are {self._ai_peer}", 1,
                )
            else:
                content = self._default
            slot = "default"

        return [ContextChunk(
            source="identity",
            stage="stable",
            slot=slot,
            priority=1,
            content=content,
            metadata={"has_soul": has_soul},
        )]


# ---------------------------------------------------------------------------
# Source 2: ToolGuidanceSource
# ---------------------------------------------------------------------------

class ToolGuidanceSource:
    """Tool-aware behavioral guidance strings."""

    name = "tool_guidance"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        valid_tool_names: list[str] = None,
        preserve_legacy_memory_guidance: bool = False,
    ):
        self._tool_names = valid_tool_names or []
        self._preserve_legacy_memory_guidance = preserve_legacy_memory_guidance

    def _build_memory_guidance(self) -> str:
        if self._preserve_legacy_memory_guidance:
            return MEMORY_GUIDANCE

        has_session_search = "session_search" in self._tool_names
        has_skill_tool = (
            "skill_manage" in self._tool_names or "skills_list" in self._tool_names
        )

        parts = [
            "You have persistent memory across sessions. Save durable facts using the memory "
            "tool: user preferences, environment details, tool quirks, and stable conventions. "
            "Memory is injected into every turn, so keep it compact and focused on facts that "
            "will still matter later.\n"
            "Prioritize what reduces future user steering — the most valuable memory is one "
            "that prevents the user from having to correct or remind you again. "
            "User preferences and recurring corrections matter more than procedural task details.\n"
        ]

        progress_guidance = (
            "Do NOT save task progress, session outcomes, completed-work logs, or temporary TODO "
            "state to memory;"
        )
        if has_session_search:
            progress_guidance += " use session_search to recall those from past transcripts."
        else:
            progress_guidance += " keep memory focused on durable facts instead."
        parts.append(progress_guidance)

        if has_skill_tool:
            parts.append(
                "If you've discovered a new way to do something, solved a problem that could be "
                "necessary later, save it as a skill with the skill tool."
            )

        return " ".join(parts)

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        parts: list[str] = []
        if "memory" in self._tool_names:
            parts.append(self._build_memory_guidance())
        if "session_search" in self._tool_names:
            parts.append(SESSION_SEARCH_GUIDANCE)
        if "skill_manage" in self._tool_names or "skills_list" in self._tool_names:
            parts.append(SKILLS_GUIDANCE)

        if not parts:
            return []

        content = " ".join(parts)
        return [ContextChunk(
            source="tool_guidance",
            stage="stable",
            slot="behavioral",
            priority=2,
            content=content,
        )]


# ---------------------------------------------------------------------------
# Source 3: ToolUseEnforcementSource
# ---------------------------------------------------------------------------

class ToolUseEnforcementSource:
    """Injects tool-use enforcement guidance based on model name and config."""

    name = "tool_use_enforcement"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        tool_use_enforcement,  # True/False/"auto"/list or None
        model: str | None = None,
        has_tools: bool = True,
    ):
        self._enforce = tool_use_enforcement
        self._model = model or ""
        self._has_tools = has_tools

    def _should_inject(self) -> bool:
        enf = self._enforce
        model_lower = self._model.lower()
        if enf is True or (isinstance(enf, str) and enf.lower() in ("true", "always", "yes", "on")):
            return True
        if enf is False or (isinstance(enf, str) and enf.lower() in ("false", "never", "no", "off")):
            return False
        if isinstance(enf, list):
            return any(
                p.lower() in model_lower
                for p in enf
                if isinstance(p, str)
            )
        # "auto" or any other value — use hardcoded defaults
        return any(p in model_lower for p in TOOL_USE_ENFORCEMENT_MODELS)

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        if not self._has_tools:
            return []
        if not self._should_inject():
            return []
        return [ContextChunk(
            source="tool_use_enforcement",
            stage="stable",
            slot="enforcement",
            priority=3,
            content=TOOL_USE_ENFORCEMENT_GUIDANCE,
        )]


# ---------------------------------------------------------------------------
# Source 4: HonchoStaticSource
# ---------------------------------------------------------------------------

class HonchoStaticSource:
    """Honcho static block (baked into cached system prompt)."""

    name = "honcho_static"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        honcho_session_manager=None,
        honcho_config=None,
        ai_peer_name: str | None = None,
    ):
        self._manager = honcho_session_manager
        self._config = honcho_config
        self._ai_peer = ai_peer_name

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        if self._manager is None:
            return []
        content, err = wrap_honcho_static_block(
            self._manager, self._config, self._ai_peer
        )
        if not content and not err:
            return []
        return [ContextChunk(
            source="honcho_static",
            stage="stable",
            slot="honcho",
            priority=4,
            content=content,
        )]


# ---------------------------------------------------------------------------
# Source 5: SystemMessageSource
# ---------------------------------------------------------------------------

class SystemMessageSource:
    """External system_message provided at session start."""

    name = "system_message"
    stage: Literal["stable"] = "stable"

    def __init__(self, system_message: str | None = None):
        self._message = system_message

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        content = self._message if self._message is not None else (ctx.system_message or "")
        if not content:
            return []
        return [ContextChunk(
            source="system_message",
            stage="stable",
            slot="external",
            priority=5,
            content=content,
        )]


# ---------------------------------------------------------------------------
# Source 6: MemorySource
# ---------------------------------------------------------------------------

class MemorySource:
    """Persistent memory: MEMORY.md formatted for system prompt."""

    name = "memory"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        memory_store=None,
        memory_enabled: bool = True,
    ):
        self._store = memory_store
        self._enabled = memory_enabled

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        if not self._enabled or self._store is None:
            return []
        content = self._store.format_for_system_prompt("memory")
        if not content:
            return []
        return [ContextChunk(
            source="memory",
            stage="stable",
            slot="memory",
            priority=6,
            content=content,
        )]


# ---------------------------------------------------------------------------
# Source 7: UserProfileSource
# ---------------------------------------------------------------------------

class UserProfileSource:
    """User profile: USER.md formatted for system prompt."""

    name = "user_profile"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        memory_store=None,
        user_profile_enabled: bool = True,
    ):
        self._store = memory_store
        self._enabled = user_profile_enabled

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        if not self._enabled or self._store is None:
            return []
        content = self._store.format_for_system_prompt("user")
        if not content:
            return []
        return [ContextChunk(
            source="user_profile",
            stage="stable",
            slot="user",
            priority=7,
            content=content,
        )]


# ---------------------------------------------------------------------------
# Source 8: SkillsSource
# ---------------------------------------------------------------------------

class SkillsSource:
    """Skills index: compact skills system prompt."""

    name = "skills"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        available_tools: list[str] = None,
        available_toolsets: set[str] = None,
        build_skills_fn=None,  # Override for testing
    ):
        self._tools = available_tools or []
        self._toolsets = available_toolsets or set()
        self._build_fn = build_skills_fn

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        has_skills = any(
            name in self._tools
            for name in ("skills_list", "skill_view", "skill_manage")
        )
        if not has_skills:
            return []
        if self._build_fn:
            content = self._build_fn(
                available_tools=self._tools,
                available_toolsets=self._toolsets,
            ) or ""
        else:
            from agent.context_engine.compat import wrap_build_skills_system_prompt
            content, _ = wrap_build_skills_system_prompt(
                available_tools=self._tools,
                available_toolsets=self._toolsets,
            )
        if not content:
            return []
        return [ContextChunk(
            source="skills",
            stage="stable",
            slot="skills_index",
            priority=8,
            content=content,
        )]


# ---------------------------------------------------------------------------
# Source 9: ProjectContextSource
# ---------------------------------------------------------------------------

class ProjectContextSource:
    """Context files: AGENTS.md, .cursorrules, etc."""

    name = "project_context"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        cwd: str | None = None,
        skip_soul: bool = False,
    ):
        self._cwd = cwd
        self._skip_soul = skip_soul

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        from agent.context_engine.compat import wrap_build_context_files_prompt
        content, err = wrap_build_context_files_prompt(
            cwd=self._cwd or ctx.cwd,
            skip_soul=self._skip_soul,
        )
        if not content and not err:
            return []
        return [ContextChunk(
            source="project_context",
            stage="stable",
            slot="context_files",
            priority=9,
            content=content or "",
        )]


# ---------------------------------------------------------------------------
# Source 10: TimePlatformSource
# ---------------------------------------------------------------------------

_PLATFORM_HINTS = {
    "telegram": (
        "You are communicating via Telegram. Keep messages concise. "
        "Use markdown sparingly. Do not use HTML telegram tags."
    ),
    "discord": (
        "You are communicating via Discord. Keep messages concise. "
        "Use Discord-compatible formatting."
    ),
    "slack": (
        "You are communicating via Slack. Keep messages concise. "
        "Use Slack-compatible formatting."
    ),
    "whatsapp": (
        "You are communicating via WhatsApp. Keep messages very short. "
        "Single-line responses preferred."
    ),
    "signal": (
        "You are communicating via Signal. Keep messages very short."
    ),
}


class TimePlatformSource:
    """Timestamp, session ID, model, provider, and platform hint."""

    name = "time_platform"
    stage: Literal["stable"] = "stable"

    def __init__(
        self,
        model: str | None = None,
        provider: str | None = None,
        session_id: str | None = None,
        platform: str | None = None,
        pass_session_id: bool = False,
    ):
        self._model = model
        self._provider = provider
        self._session_id = session_id
        self._platform = platform
        self._pass_session_id = pass_session_id

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        from hermes_time import now as hermes_now
        now = hermes_now()
        timestamp = now.strftime("%A, %B %d, %Y %I:%M %p")

        parts: list[str] = []
        parts.append(f"Conversation started: {timestamp}")
        if self._pass_session_id and self._session_id:
            parts.append(f"Session ID: {self._session_id}")
        if self._model:
            parts.append(f"Model: {self._model}")
        if self._provider:
            parts.append(f"Provider: {self._provider}")

        content = "\n".join(parts)

        platform_key = (self._platform or "").lower().strip()
        if platform_key in _PLATFORM_HINTS:
            hint = _PLATFORM_HINTS[platform_key]
            content += "\n\n" + hint

        if not content.strip():
            return []

        return [ContextChunk(
            source="time_platform",
            stage="stable",
            slot="timestamp",
            priority=10,
            content=content,
        )]


# ===========================================================================
# Dynamic sources (per-turn, not baked into cached system prompt)
# ===========================================================================

from agent.context_engine.context import AssemblyContext
from agent.context_engine.models import ContextChunk


# ---------------------------------------------------------------------------
# Dynamic Source 1: EphemeralSystemSource
# ---------------------------------------------------------------------------

class EphemeralSystemSource:
    """Ephemeral system prompt set at runtime (CLI/gateway)."""

    name = "ephemeral"
    stage = "dynamic"

    def __init__(self, get_ephemeral_fn: callable = None):
        """get_ephemeral_fn: () -> str, returns current ephemeral_system_prompt."""
        self._get = get_ephemeral_fn or (lambda: "")

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        content = self._get() or ""
        if not content:
            return []
        return [ContextChunk(
            source="ephemeral",
            stage="dynamic",
            slot="ephemeral",
            priority=1,
            content=content,
        )]


# ---------------------------------------------------------------------------
# Dynamic Source 2: PluginTurnContextSource
# ---------------------------------------------------------------------------

class PluginTurnContextSource:
    """Plugin pre_llm_call hook context."""

    name = "plugin"
    stage = "dynamic"

    def __init__(self, session_id: str | None = None):
        self._session_id = session_id

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        from agent.context_engine.compat import wrap_invoke_pre_llm_call
        agent = getattr(ctx, "agent", None)
        if getattr(agent, "_plugin_turn_context_ready", False) is True:
            content = getattr(agent, "_plugin_turn_context", "") or ""
            if not content:
                return []
            return [ContextChunk(
                source="plugin",
                stage="dynamic",
                slot="plugin_context",
                priority=2,
                content=content,
            )]
        is_first = len(ctx.conversation_history) <= 1
        content, err = wrap_invoke_pre_llm_call(
            session_id=self._session_id or "",
            user_message=ctx.user_message or "",
            conversation_history=ctx.conversation_history,
            is_first_turn=is_first,
            model=getattr(agent, "model", "") or "",
            platform=getattr(agent, "platform", "") or "",
        )
        if agent is not None:
            setattr(agent, "_plugin_turn_context", content or "")
            setattr(agent, "_plugin_turn_context_ready", True)
        if not content and not err:
            return []
        return [ContextChunk(
            source="plugin",
            stage="dynamic",
            slot="plugin_context",
            priority=2,
            content=content or "",
        )]


# ---------------------------------------------------------------------------
# Dynamic Source 3: SparkGraphRecallSource
# ---------------------------------------------------------------------------

class SparkGraphRecallSource:
    """SparkGraph recall block for current turn query."""

    name = "sparkgraph_recall"
    stage = "dynamic"

    def __init__(
        self,
        sparkgraph_manager=None,
        sparkgraph_enabled: bool = False,
    ):
        self._manager = sparkgraph_manager
        self._enabled = sparkgraph_enabled

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        from agent.context_engine.compat import wrap_sparkgraph_build_recall
        agent = getattr(ctx, "agent", None)
        if not self._enabled or self._manager is None:
            return []
        if getattr(agent, "_sparkgraph_turn_context_ready", False) is True:
            content = getattr(agent, "_sparkgraph_turn_context", "") or ""
        else:
            content, err = wrap_sparkgraph_build_recall(
                self._manager,
                self._enabled,
                user_message=ctx.user_message or "",
            )
            if agent is not None:
                setattr(agent, "_sparkgraph_turn_context", content or "")
                setattr(agent, "_sparkgraph_turn_context_ready", True)
            if not content and not err:
                return []
        if not content:
            return []
        return [ContextChunk(
            source="sparkgraph_recall",
            stage="dynamic",
            slot="recall",
            priority=3,
            content=content or "",
            metadata={"recall_injected": bool(content)},
        )]


# ---------------------------------------------------------------------------
# Dynamic Source 4: HonchoTurnSource
# ---------------------------------------------------------------------------

class HonchoTurnSource:
    """Honcho per-turn context (not baked into cached system prompt)."""

    name = "honcho_turn"
    stage = "dynamic"

    def __init__(self, honcho_session_manager=None):
        self._manager = honcho_session_manager

    def collect(self, ctx: AssemblyContext) -> list[ContextChunk]:
        from agent.context_engine.compat import wrap_honcho_get_turn_context
        if self._manager is None:
            return []
        content, err = wrap_honcho_get_turn_context(
            self._manager,
            user_message=ctx.user_message or "",
            conversation_history=ctx.conversation_history,
        )
        if not content and not err:
            return []
        return [ContextChunk(
            source="honcho_turn",
            stage="dynamic",
            slot="honcho_context",
            priority=4,
            content=content or "",
        )]
