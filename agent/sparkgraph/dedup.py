"""SparkGraph canonicalization and lightweight dedup helpers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from agent.sparkgraph.store import SparkGraphStore
from agent.sparkgraph.types import NodeStatus, NodeType

NEAR_DUPLICATE_THRESHOLD = 0.6
FTS_CANDIDATE_LIMIT = 5


@dataclass(frozen=True)
class DedupMatch:
    node_id: str
    match_type: str
    similarity: float
    node_type: str | None = None


def _normalize_for_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[^\w\s-]", " ", normalized)
    normalized = re.sub(r"[-_]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def build_canonical_key(node_type: NodeType | str, summary: str) -> str:
    """Build a stable canonical key from node type and summary text."""
    node_type_value = node_type.value if isinstance(node_type, NodeType) else str(node_type).strip().upper()
    normalized = _normalize_for_key(summary)
    slug = normalized.replace(" ", "-") if normalized else "empty"
    return f"{node_type_value.lower()}:{slug}"


def _similarity(left: str, right: str) -> float:
    left_norm = _normalize_for_key(left)
    right_norm = _normalize_for_key(right)
    char_ratio = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    if not left_tokens or not right_tokens:
        return char_ratio
    token_ratio = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    return max(char_ratio, token_ratio)


def find_dedup_match(
    store: SparkGraphStore,
    *,
    node_type: NodeType,
    summary: str,
    canonical_key: str,
) -> DedupMatch | None:
    """Find an exact or near-duplicate node candidate."""
    with store._conn_lock:
        exact = store.conn.execute(
            """
            SELECT id, summary
            FROM sg_nodes
            WHERE type = ? AND canonical_key = ?
            LIMIT 1
            """,
            (node_type.value, canonical_key),
        ).fetchone()
    if exact:
        return DedupMatch(
            node_id=exact["id"],
            match_type="exact",
            similarity=1.0,
            node_type=node_type.value,
        )

    best: DedupMatch | None = None
    candidates = [row for row in store.search_nodes(summary, status=NodeStatus.ACTIVE.value)[:FTS_CANDIDATE_LIMIT] if row["type"] == node_type.value]
    if not candidates:
        with store._conn_lock:
            fallback_rows = store.conn.execute(
                """
                SELECT id, summary, type
                FROM sg_nodes
                WHERE type = ? AND status = ?
                ORDER BY updated_at DESC
                LIMIT 20
                """,
                (node_type.value, NodeStatus.ACTIVE.value),
            ).fetchall()
        candidates = [dict(row) for row in fallback_rows]

    for row in candidates:
        score = _similarity(summary, row["summary"])
        if score < NEAR_DUPLICATE_THRESHOLD:
            continue
        candidate = DedupMatch(
            node_id=row["id"],
            match_type="near",
            similarity=score,
            node_type=str(row.get("type") or ""),
        )
        if best is None or candidate.similarity > best.similarity:
            best = candidate
    return best


def find_cross_type_dedup_match(
    store: SparkGraphStore,
    *,
    node_types: tuple[NodeType, ...],
    summary: str,
) -> DedupMatch | None:
    """Find a near-duplicate across a small set of compatible node types.

    This is used to prevent the same troubleshooting guidance from forking
    into FACT/ISSUE twins across different write paths.
    """
    allowed_types = {node_type.value for node_type in node_types}
    best: DedupMatch | None = None

    candidates = [
        row for row in store.search_nodes(summary, status=NodeStatus.ACTIVE.value)[:FTS_CANDIDATE_LIMIT * 2]
        if row["type"] in allowed_types
    ]
    if not candidates:
        placeholders = ", ".join("?" for _ in allowed_types)
        with store._conn_lock:
            fallback_rows = store.conn.execute(
                f"""
                SELECT id, summary, type
                FROM sg_nodes
                WHERE type IN ({placeholders}) AND status = ?
                ORDER BY updated_at DESC
                LIMIT 20
                """,
                tuple(sorted(allowed_types)) + (NodeStatus.ACTIVE.value,),
            ).fetchall()
        candidates = [dict(row) for row in fallback_rows]

    for row in candidates:
        score = _similarity(summary, row["summary"])
        if score < NEAR_DUPLICATE_THRESHOLD:
            continue
        candidate = DedupMatch(
            node_id=row["id"],
            match_type="cross_type_near",
            similarity=score,
            node_type=str(row.get("type") or ""),
        )
        if best is None or candidate.similarity > best.similarity:
            best = candidate
    return best
