"""Prompt helpers for SparkGraph-integrated flush flows."""

from __future__ import annotations


def build_flush_prompt(*, include_memory: bool, include_sparkgraph: bool) -> str:
    """Build the flush prompt used before compression/reset/exit."""
    parts = ["[System: The session is being compressed."]

    if include_memory:
        parts.append(
            "Save anything worth remembering — prioritize user preferences, "
            "corrections, and recurring patterns over task-specific details."
        )

    if include_sparkgraph:
        parts.append(
            "Proactively call sparkgraph_record for valuable knowledge points that should be "
            "retrievable later: concrete facts, recurring issues, stable resources, lasting "
            "decisions, and stable preferences. Do not record greetings. Do not use "
            "sparkgraph_record for greetings, temporary task state, progress updates, or "
            "speculative guesses. If there is no durable knowledge worth retrieving later, "
            "do not call sparkgraph_record. "
            "When multiple recorded items have a clear semantic relationship, include an "
            "'edges' array in the sparkgraph_record call to link them: SOLVES for issue→skill "
            "resolution, DEPENDS_ON for prerequisites, RELATED_TO for loose connections, "
            "DERIVED_FROM for successor/replacement, CONFLICTS_WITH for mutual exclusion. "
            "Only link items with clear, intentional relationships; do not over-connect."
        )

    parts.append("]")
    return " ".join(parts)
