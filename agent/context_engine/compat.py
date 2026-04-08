"""Compatibility wrappers for existing Hermes functions.

Each wrapper:
  1. Calls the original function unchanged
  2. Returns (content_str, error_str_or_none)
  3. Never throws; errors are returned as strings for observability
"""

from __future__ import annotations

import logging
from typing import Callable, Optional
from unittest.mock import Mock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt builder wrappers
# ---------------------------------------------------------------------------

def wrap_load_soul_md() -> tuple[str, Optional[str]]:
    """Wrapper for prompt_builder.load_soul_md()."""
    try:
        from agent.prompt_builder import load_soul_md
        content = load_soul_md() or ""
        return content, None
    except Exception as e:
        logger.debug("wrap_load_soul_md failed: %s", e)
        return "", str(e)


def wrap_build_context_files_prompt(
    cwd: str | None,
    *,
    skip_soul: bool = False,
) -> tuple[str, Optional[str]]:
    """Wrapper for prompt_builder.build_context_files_prompt()."""
    try:
        from agent.prompt_builder import build_context_files_prompt
        content = build_context_files_prompt(cwd=cwd, skip_soul=skip_soul) or ""
        return content, None
    except Exception as e:
        logger.debug("wrap_build_context_files_prompt failed: %s", e)
        return "", str(e)


def wrap_build_skills_system_prompt(
    available_tools: list[str],
    available_toolsets: set[str],
) -> tuple[str, Optional[str]]:
    """Wrapper for prompt_builder.build_skills_system_prompt()."""
    try:
        from agent import prompt_builder

        run_agent_fn = None
        try:
            import run_agent as run_agent_module
            run_agent_fn = getattr(run_agent_module, "build_skills_system_prompt", None)
        except Exception:
            run_agent_fn = None

        prompt_builder_fn = prompt_builder.build_skills_system_prompt

        if isinstance(prompt_builder_fn, Mock):
            build_fn = prompt_builder_fn
        elif isinstance(run_agent_fn, Mock):
            build_fn = run_agent_fn
        else:
            build_fn = run_agent_fn or prompt_builder_fn

        content = build_fn(
            available_tools=set(available_tools or []),
            available_toolsets=available_toolsets,
        ) or ""
        return content, None
    except Exception as e:
        logger.debug("wrap_build_skills_system_prompt failed: %s", e)
        return "", str(e)


# ---------------------------------------------------------------------------
# Memory store wrappers
# ---------------------------------------------------------------------------

def wrap_memory_store_format(
    memory_store,
    memory_type: str,
) -> tuple[str, Optional[str]]:
    """Wrapper for MemoryStore.format_for_system_prompt().

    Args:
        memory_store: MemoryStore instance (from agent._memory_store). May be None.
        memory_type: "memory" or "user"
    """
    if memory_store is None:
        return "", None
    try:
        content = memory_store.format_for_system_prompt(memory_type) or ""
        return content, None
    except Exception as e:
        logger.debug("wrap_memory_store_format(%s) failed: %s", memory_type, e)
        return "", str(e)


# ---------------------------------------------------------------------------
# Honcho wrappers
# ---------------------------------------------------------------------------

def wrap_honcho_static_block(
    honcho_session_manager,
    honcho_config,
    ai_peer_name: str | None,
) -> tuple[str, Optional[str]]:
    """Build Honcho static block (baked into cached system prompt).

    Mirrors the logic in run_agent.py::_build_system_prompt() lines 2915-2967.
    Returns ("", None) if honcho is not active (manager or config is None).
    """
    if honcho_session_manager is None or honcho_config is None:
        return "", None
    try:
        cfg = honcho_config
        mode = getattr(cfg, "memory_mode", None) or "hybrid"
        freq = getattr(cfg, "write_frequency", None) or "async"
        recall_mode = getattr(cfg, "recall_mode", None) or "hybrid"

        ai_name = (
            ai_peer_name if ai_peer_name and ai_peer_name != "hermes" else None
        )
        identity_suffix = f"You are {ai_name}" if ai_name else "You are Hermes Agent"
        identity_block = f"# AI Identity\n{identity_suffix}"

        honcho_block = (
            f"# Honcho memory integration\n"
            f"Active. Mode: {mode}. Write frequency: {freq}. Recall: {recall_mode}.\n"
        )
        if recall_mode == "context":
            honcho_block += (
                "Honcho context is injected into this system prompt below.\n"
            )
        elif recall_mode == "tools":
            honcho_block += (
                "Honcho tools:\n"
                "  honcho_context <question>\n"
                "  honcho_search <query>\n"
                "  honcho_profile\n"
                "  honcho_conclude <conclusion>\n"
            )
        else:  # hybrid
            honcho_block += (
                "Honcho context is injected into this system prompt below.\n"
                "Honcho tools:\n"
                "  honcho_context <question>\n"
                "  honcho_search <query>\n"
                "  honcho_profile\n"
                "  honcho_conclude <conclusion>\n"
            )
        content = identity_block + "\n\n" + honcho_block
        return content, None
    except Exception as e:
        logger.debug("wrap_honcho_static_block failed: %s", e)
        return "", str(e)


def wrap_honcho_get_turn_context(
    honcho_session_manager,
    user_message: str,
    conversation_history: list,
) -> tuple[str, Optional[str]]:
    """Wrapper for HonchoSessionManager.get_turn_context().

    Returns ("", None) if manager is None.
    """
    if honcho_session_manager is None:
        return "", None
    try:
        result = honcho_session_manager.get_turn_context(
            user_message=user_message,
            conversation_history=conversation_history,
        )
        content = result if isinstance(result, str) else (result or "")
        return content, None
    except Exception as e:
        logger.debug("wrap_honcho_get_turn_context failed: %s", e)
        return "", str(e)


# ---------------------------------------------------------------------------
# SparkGraph wrappers
# ---------------------------------------------------------------------------

def wrap_sparkgraph_build_recall(
    sparkgraph_manager,
    sparkgraph_enabled: bool,
    user_message: str,
    *,
    max_nodes: int | None = None,
    max_chars: int | None = None,
) -> tuple[str, Optional[str]]:
    """Wrapper for SparkGraphManager.build_recall_block().

    Returns ("", None) if sparkgraph is disabled or manager is None.
    """
    if not sparkgraph_enabled or sparkgraph_manager is None:
        return "", None
    try:
        block, _token_estimate = sparkgraph_manager.build_recall_block(
            user_message,
            max_nodes=max_nodes,
            max_chars=max_chars,
        )
        return block, None
    except Exception as e:
        logger.debug("wrap_sparkgraph_build_recall failed: %s", e)
        return "", str(e)


# ---------------------------------------------------------------------------
# Plugin wrappers
# ---------------------------------------------------------------------------

def wrap_invoke_pre_llm_call(
    session_id: str,
    user_message: str,
    conversation_history: list,
    is_first_turn: bool,
    *,
    model: str = "",
    platform: str = "",
) -> tuple[str, Optional[str]]:
    """Wrapper for hermes_cli.plugins.invoke_hook('pre_llm_call', ...).

    Concatenates all plugin context strings with "\\n\\n".
    Returns ("", None) if no plugins return context.
    """
    try:
        from hermes_cli.plugins import invoke_hook
        results = invoke_hook(
            "pre_llm_call",
            session_id=session_id,
            user_message=user_message,
            conversation_history=list(conversation_history),
            is_first_turn=is_first_turn,
            model=model,
            platform=platform,
        )
        parts: list[str] = []
        if results:
            for r in results:
                if isinstance(r, dict) and r.get("context"):
                    parts.append(str(r["context"]))
                elif isinstance(r, str) and r.strip():
                    parts.append(r.strip())
        content = "\n\n".join(parts)
        return content, None
    except Exception as e:
        logger.debug("wrap_invoke_pre_llm_call failed: %s", e)
        return "", str(e)
