"""SparkGraph store primitives for the minimal v2 persistence layer."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.sparkgraph.config import SparkGraphConfig
from agent.sparkgraph.db import (
    EDGES_TABLE,
    EVIDENCE_TABLE,
    NODES_FTS_TABLE,
    NODES_TABLE,
    VECTORS_TABLE,
    connect_db,
    initialize_schema,
)
from agent.sparkgraph.embedding import pack_embedding, unpack_embedding
from agent.sparkgraph.types import EdgeType, NodeStatus, NodeType


@dataclass(frozen=True)
class SparkGraphNodeInput:
    type: NodeType
    summary: str
    canonical_key: str
    source_kind: str
    detail: str = ""
    status: NodeStatus = NodeStatus.CANDIDATE
    confidence: float = 0.0
    stability: float = 0.0
    reuse_score: float = 0.0
    meta: dict[str, Any] | None = None


class SparkGraphStore:
    """Small sqlite-backed store used by later flush and recall layers."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = connect_db(db_path)
        initialize_schema(self._conn)

    @classmethod
    def from_config(cls, config: SparkGraphConfig) -> "SparkGraphStore":
        return cls(config.db_path)

    @property
    def conn(self):
        return self._conn

    def close(self):
        self._conn.close()

    def insert_node(self, item: SparkGraphNodeInput) -> str:
        now = int(time.time())
        node_id = uuid.uuid4().hex
        self._conn.execute(
            f"""
            INSERT INTO {NODES_TABLE} (
                id, type, summary, detail, status, confidence, stability,
                reuse_score, source_kind, canonical_key, meta, created_at, updated_at, last_recalled_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node_id,
                item.type.value,
                item.summary,
                item.detail,
                item.status.value,
                item.confidence,
                item.stability,
                item.reuse_score,
                item.source_kind,
                item.canonical_key,
                json.dumps(item.meta or {}, sort_keys=True),
                now,
                now,
                0,
            ),
        )
        self._conn.commit()
        return node_id

    def insert_edge(
        self,
        *,
        from_id: str,
        to_id: str,
        edge_type: EdgeType,
        weight: float = 1.0,
        meta: dict[str, Any] | None = None,
    ) -> str:
        edge_id = uuid.uuid4().hex
        self._conn.execute(
            f"""
            INSERT INTO {EDGES_TABLE} (id, from_id, to_id, type, weight, meta, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge_id,
                from_id,
                to_id,
                edge_type.value,
                weight,
                json.dumps(meta or {}, sort_keys=True),
                int(time.time()),
            ),
        )
        self._conn.commit()
        return edge_id

    def append_evidence(
        self,
        *,
        node_id: str,
        session_id: str,
        turn_index: int,
        source_text: str,
        source_kind: str,
    ) -> bool:
        evidence_id = uuid.uuid4().hex
        source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        cursor = self._conn.execute(
            f"""
            INSERT OR IGNORE INTO {EVIDENCE_TABLE} (
                id, node_id, session_id, turn_index, source_hash, source_kind, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                node_id,
                session_id,
                turn_index,
                source_hash,
                source_kind,
                int(time.time()),
            ),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_node(self, node_id: str):
        row = self._conn.execute(
            f"SELECT * FROM {NODES_TABLE} WHERE id = ?",
            (node_id,),
        ).fetchone()
        return dict(row) if row else None

    def count_evidence(self, node_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS count FROM sg_evidence WHERE node_id = ?",
            (node_id,),
        ).fetchone()
        return int(row["count"]) if row else 0

    def count_nodes(self, *, status: str | None = None) -> int:
        sql = f"SELECT COUNT(*) AS count FROM {NODES_TABLE}"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        row = self._conn.execute(sql, tuple(params)).fetchone()
        return int(row["count"]) if row else 0

    def count_evidence_rows(self) -> int:
        row = self._conn.execute(
            f"SELECT COUNT(*) AS count FROM {EVIDENCE_TABLE}"
        ).fetchone()
        return int(row["count"]) if row else 0

    def count_vectors(self) -> int:
        row = self._conn.execute(
            f"SELECT COUNT(*) AS count FROM {VECTORS_TABLE}"
        ).fetchone()
        return int(row["count"]) if row else 0

    def count_nodes_by_type(self) -> dict[str, int]:
        rows = self._conn.execute(
            f"SELECT type, COUNT(*) AS count FROM {NODES_TABLE} GROUP BY type"
        ).fetchall()
        return {str(row["type"]): int(row["count"]) for row in rows}

    def count_nodes_by_status(self) -> dict[str, int]:
        rows = self._conn.execute(
            f"SELECT status, COUNT(*) AS count FROM {NODES_TABLE} GROUP BY status"
        ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def update_node_scoring(
        self,
        node_id: str,
        *,
        confidence: float,
        stability: float,
        reuse_score: float,
        status: str,
        confidence_components: dict[str, Any] | None = None,
    ) -> None:
        current = self.get_node(node_id)
        if not current:
            return
        meta = json.loads(current.get("meta") or "{}")
        if confidence_components is not None:
            meta["confidence_components"] = confidence_components
            meta["confidence_version"] = 1
        self._conn.execute(
            f"""
            UPDATE {NODES_TABLE}
            SET confidence = ?, stability = ?, reuse_score = ?, status = ?, meta = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                confidence,
                stability,
                reuse_score,
                status,
                json.dumps(meta, sort_keys=True),
                int(time.time()),
                node_id,
            ),
        )
        self._conn.commit()

    def update_node_status(self, node_id: str, *, status: str) -> None:
        self._conn.execute(
            f"""
            UPDATE {NODES_TABLE}
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                int(time.time()),
                node_id,
            ),
        )
        self._conn.commit()

    def list_nodes(
        self,
        *,
        status: str | None = None,
        updated_before: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        params: list[Any] = []
        if status:
            where_parts.append("status = ?")
            params.append(status)
        if updated_before is not None:
            where_parts.append("updated_at < ?")
            params.append(int(updated_before))

        sql = f"SELECT * FROM {NODES_TABLE}"
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        sql += " ORDER BY updated_at ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))

        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Sanitize arbitrary user text for FTS5 MATCH queries."""
        quoted_parts: list[str] = []

        def _preserve_quoted(match: re.Match[str]) -> str:
            quoted_parts.append(match.group(0))
            return f"\x00Q{len(quoted_parts) - 1}\x00"

        sanitized = re.sub(r'"[^"]*"', _preserve_quoted, query)
        sanitized = re.sub(r'[+{}()\"^]', " ", sanitized)
        sanitized = re.sub(r"\*+", "*", sanitized)
        sanitized = re.sub(r"(^|\s)\*", r"\1", sanitized)
        sanitized = re.sub(r"(?i)^(AND|OR|NOT)\b\s*", "", sanitized.strip())
        sanitized = re.sub(r"(?i)\s+(AND|OR|NOT)\s*$", "", sanitized.strip())
        sanitized = re.sub(r"\b(\w+(?:-\w+)+)\b", r'"\1"', sanitized)

        for idx, quoted in enumerate(quoted_parts):
            sanitized = sanitized.replace(f"\x00Q{idx}\x00", quoted)

        return sanitized.strip()

    def search_nodes(self, query: str, *, status: str | None = None, limit: int = 8):
        if not query or not query.strip():
            return []

        sanitized = self._sanitize_fts_query(query)
        if not sanitized:
            return []

        where_parts = [f"{NODES_FTS_TABLE} MATCH ?"]
        params: list[Any] = [sanitized]
        if status:
            where_parts.append("n.status = ?")
            params.append(status)
        params.append(max(1, int(limit)))

        try:
            rows = self._conn.execute(
                f"""
                SELECT n.*
                FROM {NODES_FTS_TABLE} f
                JOIN {NODES_TABLE} n ON n.rowid = f.rowid
                WHERE {" AND ".join(where_parts)}
                ORDER BY n.updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in rows]

    def upsert_vector(self, *, node_id: str, content_hash: str, embedding: list[float]) -> None:
        packed = pack_embedding(embedding)
        now = int(time.time())
        self._conn.execute(
            f"""
            INSERT INTO {VECTORS_TABLE} (node_id, content_hash, embedding, dims, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                content_hash = excluded.content_hash,
                embedding = excluded.embedding,
                dims = excluded.dims,
                updated_at = excluded.updated_at
            """,
            (node_id, content_hash, sqlite3.Binary(packed), len(embedding), now),
        )
        self._conn.commit()

    def get_vector(self, node_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"SELECT node_id, content_hash, embedding, dims, updated_at FROM {VECTORS_TABLE} WHERE node_id = ?",
            (node_id,),
        ).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["embedding"] = unpack_embedding(payload["embedding"], int(payload["dims"]))
        return payload

    def list_vector_nodes(self, *, status: str | None = None, limit: int = 64) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        params: list[Any] = []
        if status:
            where_parts.append("n.status = ?")
            params.append(status)
        params.append(max(1, int(limit)))

        sql = (
            f"SELECT n.*, v.content_hash, v.embedding, v.dims, v.updated_at AS vector_updated_at "
            f"FROM {VECTORS_TABLE} v "
            f"JOIN {NODES_TABLE} n ON n.id = v.node_id"
        )
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        sql += " ORDER BY n.updated_at DESC LIMIT ?"

        rows = self._conn.execute(sql, tuple(params)).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["embedding"] = unpack_embedding(payload["embedding"], int(payload["dims"]))
            items.append(payload)
        return items

    def list_nodes_missing_vectors(self, *, status: str | None = None, limit: int = 32) -> list[dict[str, Any]]:
        where_parts: list[str] = ["v.node_id IS NULL"]
        params: list[Any] = []
        if status:
            where_parts.append("n.status = ?")
            params.append(status)
        params.append(max(1, int(limit)))

        rows = self._conn.execute(
            f"""
            SELECT n.*
            FROM {NODES_TABLE} n
            LEFT JOIN {VECTORS_TABLE} v ON v.node_id = n.id
            WHERE {" AND ".join(where_parts)}
            ORDER BY n.updated_at DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_recalled(self, node_ids: list[str], *, now_ts: int | None = None) -> None:
        if not node_ids:
            return
        stamp = int(now_ts or time.time())
        for node_id in node_ids:
            current = self.get_node(node_id)
            if not current:
                continue
            meta = json.loads(current.get("meta") or "{}")
            meta["recall_hits"] = int(meta.get("recall_hits") or 0) + 1
            self._conn.execute(
                f"""
                UPDATE {NODES_TABLE}
                SET last_recalled_at = ?, meta = ?, updated_at = updated_at
                WHERE id = ?
                """,
                (
                    stamp,
                    json.dumps(meta, sort_keys=True),
                    node_id,
                ),
            )
        self._conn.commit()

    def get_related_nodes(self, node_ids: list[str], *, active_only: bool = True, limit: int = 4):
        if not node_ids:
            return []

        placeholders = ", ".join("?" for _ in node_ids)
        params: list[Any] = list(node_ids)
        where_parts = [
            f"(e.from_id IN ({placeholders}) OR e.to_id IN ({placeholders}))",
            "n.id NOT IN (" + placeholders + ")",
        ]
        params.extend(node_ids)
        params.extend(node_ids)
        if active_only:
            where_parts.append("n.status = ?")
            params.append(NodeStatus.ACTIVE.value)
        params.append(max(1, int(limit)))

        rows = self._conn.execute(
            f"""
            SELECT DISTINCT n.*
            FROM {EDGES_TABLE} e
            JOIN {NODES_TABLE} n
              ON n.id = CASE
                  WHEN e.from_id IN ({placeholders}) THEN e.to_id
                  ELSE e.from_id
              END
            WHERE {" AND ".join(where_parts)}
            ORDER BY n.updated_at DESC
            LIMIT ?
            """,
            tuple(node_ids + params),
        ).fetchall()
        return [dict(row) for row in rows]
