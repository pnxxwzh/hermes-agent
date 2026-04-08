"""SparkGraph store primitives for the minimal v2 persistence layer."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.sparkgraph.config import SparkGraphConfig
from agent.sparkgraph.db import (
    EDGES_TABLE,
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
    status: NodeStatus = NodeStatus.ACTIVE
    confidence: float = 0.0
    default_inject: bool = True
    meta: dict[str, Any] | None = None


class SparkGraphStore:
    """Small sqlite-backed store used by later flush and recall layers."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn_lock = threading.RLock()
        self._conn = connect_db(db_path)
        with self._conn_lock:
            initialize_schema(self._conn)

    @classmethod
    def from_config(cls, config: SparkGraphConfig) -> "SparkGraphStore":
        return cls(config.db_path)

    @property
    def conn(self):
        return self._conn

    def close(self):
        with self._conn_lock:
            self._conn.close()

    @staticmethod
    def _meta_with_source_sessions(
        meta: dict[str, Any] | None,
        sessions: list[str],
    ) -> dict[str, Any]:
        payload = dict(meta or {})
        payload["source_sessions"] = list(sessions)
        return payload

    def insert_node(self, item: SparkGraphNodeInput) -> str:
        now = int(time.time())
        node_id = uuid.uuid4().hex
        validated_count = item.meta.get("validated_count", 0) if item.meta else 0
        # source_sessions：优先从 meta 合并，写入独立列（同时回填 meta，保持一致）
        sessions_from_meta = item.meta.get("source_sessions", []) if item.meta else []
        meta_payload = self._meta_with_source_sessions(item.meta, sessions_from_meta)
        source_sessions = json.dumps(sessions_from_meta)
        with self._conn_lock:
            self._conn.execute(
                f"""
                INSERT INTO {NODES_TABLE} (
                    id, type, summary, detail, status, confidence,
                    source_kind, canonical_key, meta, source_sessions, default_inject,
                    created_at, updated_at, last_recalled_at, validated_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    item.type.value,
                    item.summary,
                    item.detail,
                    item.status.value,
                    item.confidence,
                    item.source_kind,
                    item.canonical_key,
                    json.dumps(meta_payload, sort_keys=True),
                    source_sessions,
                    1 if item.default_inject else 0,
                    now,
                    now,
                    0,
                    validated_count,
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
        with self._conn_lock:
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

    def get_node(self, node_id: str):
        with self._conn_lock:
            row = self._conn.execute(
                f"SELECT * FROM {NODES_TABLE} WHERE id = ?",
                (node_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_node_by_canonical_key(self, canonical_key: str, node_type: NodeType):
        """通过 canonical_key + type 精确查找节点（用于 IntegrityError 恢复路径）。"""
        with self._conn_lock:
            row = self._conn.execute(
                f"SELECT * FROM {NODES_TABLE} WHERE canonical_key = ? AND type = ? LIMIT 1",
                (canonical_key, node_type.value),
            ).fetchone()
        return dict(row) if row else None

    def increment_validated_count(self, node_ids: list[str], *, now_ts: int | None = None) -> None:
        """命中的节点 validated_count++。recall_hits 已删除（与 validated_count 冗余）。"""
        if not node_ids:
            return
        stamp = int(now_ts or time.time())
        with self._conn_lock:
            for node_id in node_ids:
                self._conn.execute(
                    f"""UPDATE {NODES_TABLE} SET validated_count = validated_count + 1,
                        last_recalled_at = ?, updated_at = updated_at WHERE id = ?""",
                    (stamp, node_id),
                )
            self._conn.commit()

    def merge_source_sessions(self, node_id: str, new_session_id: str) -> None:
        """去重命中时：合并当前 session 到 source_sessions（去重）。"""
        current = self.get_node(node_id)
        if not current:
            return
        sessions = json.loads(current.get("source_sessions") or "[]")
        if new_session_id and new_session_id not in sessions:
            sessions.append(new_session_id)
        meta = self._meta_with_source_sessions(
            json.loads(current.get("meta") or "{}"),
            sessions,
        )
        with self._conn_lock:
            self._conn.execute(
                f"UPDATE {NODES_TABLE} SET source_sessions = ?, meta = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(sessions, sort_keys=True),
                    json.dumps(meta, sort_keys=True),
                    int(time.time()),
                    node_id,
                ),
            )
            self._conn.commit()

    def merge_nodes(self, keep_id: str, merge_id: str) -> None:
        """将 merge_id 合并到 keep_id：
        1. keep validated_count += merge validated_count
        2. keep source_sessions = union of both sessions
        3. 迁移 merge 的所有边到 keep
        4. 删除 merge 自环（同 from_id==to_id）
        5. 去重重复边（同 from_id+to_id+type 只留一条）
        6. merge 节点标记 deprecated
        """
        keep = self.get_node(keep_id)
        merge = self.get_node(merge_id)
        if not keep or not merge:
            return
        now = int(time.time())

        # 1. 累加 validated_count
        merged_validated = int(keep.get("validated_count") or 0) + int(merge.get("validated_count") or 0)

        # 2. 合并 source_sessions
        keep_sessions = set(json.loads(keep.get("source_sessions") or "[]"))
        merge_sessions = set(json.loads(merge.get("source_sessions") or "[]"))
        all_sessions = keep_sessions | merge_sessions
        keep_meta = self._meta_with_source_sessions(
            json.loads(keep.get("meta") or "{}"),
            sorted(all_sessions),
        )

        with self._conn_lock:
            self._conn.execute(
                f"""UPDATE {NODES_TABLE}
                    SET validated_count = ?, source_sessions = ?, meta = ?, updated_at = ?
                    WHERE id = ?""",
                (
                    merged_validated,
                    json.dumps(list(all_sessions), sort_keys=True),
                    json.dumps(keep_meta, sort_keys=True),
                    now,
                    keep_id,
                ),
            )

            # 3. 迁移边（from_id/to_id 替换）——去重在迁移前先执行，防止 UNIQUE 约束冲突
            # 先删：删除会导致 UNIQUE 冲突的 merge 边（这些边迁移后与 keep 的边重复，保留 keep 的那条）
            #
            # 三种冲突模式：
            # Case 1 — merge→X where X is a keep destination:
            #   migrate creates duplicate keep→X → delete merge→X
            # Case 2 — A→merge where A also→keep:
            #   migrate creates duplicate A→keep → delete A→merge
            # Case 3 — keep→merge:
            #   migrate keep→merge to keep→keep creates a self-loop,
            #   which violates CHECK (from_id <> to_id) → delete keep→merge
            # Case 4 — merge→keep:
            #   migrate merge→keep to keep→keep creates a self-loop,
            #   which violates CHECK (from_id <> to_id) → delete merge→keep
            self._conn.execute(f"""
                DELETE FROM {EDGES_TABLE}
                WHERE (
                    from_id = ?
                    AND to_id IN (SELECT to_id FROM {EDGES_TABLE} WHERE from_id = ?)
                )
                OR (
                    to_id = ?
                    AND from_id IN (SELECT from_id FROM {EDGES_TABLE} WHERE to_id = ?)
                )
                OR (
                    from_id = ?
                    AND to_id = ?
                )
                OR (
                    from_id = ?
                    AND to_id = ?
                )
            """, (
                merge_id, keep_id,
                merge_id, keep_id,
                keep_id, merge_id,
                merge_id, keep_id,
            ))

            # 迁移 from_id
            self._conn.execute(
                f"""UPDATE {EDGES_TABLE}
                    SET from_id = ? WHERE from_id = ?""",
                (keep_id, merge_id),
            )
            # 迁移 to_id
            self._conn.execute(
                f"""UPDATE {EDGES_TABLE}
                    SET to_id = ? WHERE to_id = ?""",
                (keep_id, merge_id),
            )

            # 4. 删除自环（限制范围：只删 merge_id 产生的自环，避免影响数据库中其他不相关的自环）
            self._conn.execute(f"DELETE FROM {EDGES_TABLE} WHERE from_id = to_id AND (from_id = ? OR to_id = ?)", (merge_id, merge_id))

            # 5. 去重：保留第一条，删除后续重复
            self._conn.execute(f"""
                DELETE FROM {EDGES_TABLE}
                WHERE rowid NOT IN (
                    SELECT MIN(rowid) FROM {EDGES_TABLE}
                    GROUP BY from_id, to_id, type
                )
            """)

            # 6. merge 节点 deprecated
            self._conn.execute(
                f"UPDATE {NODES_TABLE} SET status = ?, updated_at = ? WHERE id = ?",
                (NodeStatus.DEPRECATED.value, now, merge_id),
            )

            self._conn.commit()

        # 7. 失效 PPR 图缓存（merge 改变了图结构）
        # Deferred import to avoid circular dependency: pagerank → store
        try:
            from agent.sparkgraph.pagerank import invalidate_graph_cache
            invalidate_graph_cache()
        except Exception:
            # Non-fatal: cache will naturally expire after TTL
            pass

    def count_nodes(self, *, status: str | None = None) -> int:
        sql = f"SELECT COUNT(*) AS count FROM {NODES_TABLE}"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        with self._conn_lock:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        return int(row["count"]) if row else 0

    def count_nodes_by_type(self) -> dict[str, int]:
        with self._conn_lock:
            rows = self._conn.execute(
                f"SELECT type, COUNT(*) AS count FROM {NODES_TABLE} GROUP BY type"
            ).fetchall()
        return {str(row["type"]): int(row["count"]) for row in rows}

    def count_nodes_by_status(self) -> dict[str, int]:
        with self._conn_lock:
            rows = self._conn.execute(
                f"SELECT status, COUNT(*) AS count FROM {NODES_TABLE} GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def update_node_scoring(
        self,
        node_id: str,
        *,
        confidence: float,
        status: str,
        confidence_components: dict[str, Any] | None = None,
        detail: str | None = None,
    ) -> None:
        """Update scoring fields and optionally the detail column.

        Args:
            detail: When provided, overwrites the node's detail field with the new value.
                    This is used during same-type dedup hits so that the newest evidence
                    from the arriving record replaces the old detail.
        """
        current = self.get_node(node_id)
        if not current:
            return
        meta = json.loads(current.get("meta") or "{}")
        if confidence_components is not None:
            meta["confidence_components"] = confidence_components
        elif confidence_components is None and "confidence_components" in meta:
            # Explicit None → 清除旧字段，保持 meta 干净
            del meta["confidence_components"]

        with self._conn_lock:
            if detail is not None:
                # detail provided → update it along with scoring fields
                self._conn.execute(
                    f"""
                    UPDATE {NODES_TABLE}
                    SET confidence = ?, status = ?, detail = ?, meta = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        confidence,
                        status,
                        str(detail),
                        json.dumps(meta, sort_keys=True),
                        int(time.time()),
                        node_id,
                    ),
                )
            else:
                self._conn.execute(
                    f"""
                    UPDATE {NODES_TABLE}
                    SET confidence = ?, status = ?, meta = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        confidence,
                        status,
                        json.dumps(meta, sort_keys=True),
                        int(time.time()),
                        node_id,
                    ),
                )
            self._conn.commit()

    def update_node_status(self, node_id: str, *, status: str) -> None:
        with self._conn_lock:
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

        with self._conn_lock:
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

        result = sanitized.strip()

        # CJK Unicode ranges for Chinese character detection
        cjk_pattern = "[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]"
        if re.search(cjk_pattern, result):
            if not result.endswith("*"):
                result += "*"

        return result

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
            with self._conn_lock:
                rows = self._conn.execute(
                    f"""
                    SELECT n.*, bm25({NODES_FTS_TABLE}) AS fts_rank
                    FROM {NODES_FTS_TABLE}
                    JOIN {NODES_TABLE} n ON n.rowid = {NODES_FTS_TABLE}.rowid
                    WHERE {" AND ".join(where_parts)}
                    ORDER BY fts_rank ASC, n.updated_at DESC
                    LIMIT ?
                    """,
                    tuple(params),
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in rows]

    def get_by_source_kind(
        self, kinds: list[str], query: str = "", *, limit: int = 6
    ) -> list[dict[str, Any]]:
        """Returns active nodes of given source_kinds, optionally filtered by query keywords.

        Used by recall to provide explicit/manual source priority.
        """
        if not kinds:
            return []
        if query:
            terms = [t.strip() for t in query.lower().split() if t.strip()]
            like_parts = " OR ".join(["n.summary LIKE '%' || ? || '%'" for _ in terms])
            where = f"({like_parts}) AND n.status = ?"
            params: list[Any] = kinds + terms + [NodeStatus.ACTIVE.value]
        else:
            where = "n.status = ?"
            params = kinds + [NodeStatus.ACTIVE.value]

        sql = f"""
            SELECT n.* FROM {NODES_TABLE} n
            WHERE n.source_kind IN ({','.join(['?' for _ in kinds])})
              AND {where}
            ORDER BY n.validated_count DESC, n.updated_at DESC
            LIMIT ?
        """
        params.append(limit)
        try:
            with self._conn_lock:
                rows = self._conn.execute(sql, tuple(params)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in rows]

    def upsert_vector(self, *, node_id: str, content_hash: str, embedding: list[float]) -> None:
        packed = pack_embedding(embedding)
        now = int(time.time())
        with self._conn_lock:
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
        with self._conn_lock:
            row = self._conn.execute(
                f"SELECT node_id, content_hash, embedding, dims, updated_at FROM {VECTORS_TABLE} WHERE node_id = ?",
                (node_id,),
            ).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["embedding"] = unpack_embedding(payload["embedding"], int(payload["dims"]))
        return payload

    def list_vector_nodes(self, *, status: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        params: list[Any] = []
        if status:
            where_parts.append("n.status = ?")
            params.append(status)

        sql = (
            f"SELECT n.*, v.content_hash, v.embedding, v.dims, v.updated_at AS vector_updated_at "
            f"FROM {VECTORS_TABLE} v "
            f"JOIN {NODES_TABLE} n ON n.id = v.node_id"
        )
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        sql += " ORDER BY n.updated_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))

        with self._conn_lock:
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

        with self._conn_lock:
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
        with self._conn_lock:
            for node_id in node_ids:
                current = self._conn.execute(
                    f"SELECT * FROM {NODES_TABLE} WHERE id = ?",
                    (node_id,),
                ).fetchone()
                if not current:
                    continue
                meta = json.loads(current["meta"] or "{}")
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
        """
        Find 1-hop neighbours of seed nodes, excluding the seeds themselves.

        Uses two separate traversals (aligned with gm graphWalk):
          1. FROM-seed:  edge (seed → X), return X (X must not be a seed)
          2. TO-seed:    edge (X → seed), return X (X must not be a seed)

        When both endpoints are seeds (seed ↔ seed), neither direction produces
        a valid non-seed result — those edges are ignored as intended.
        """
        if not node_ids:
            return []

        sp = ", ".join("?" for _ in node_ids)
        limit_val = max(1, int(limit))
        status_cond = "AND n.status = ?" if active_only else ""
        status_param = [NodeStatus.ACTIVE.value] if active_only else []

        # Two sub-queries unioned:
        #   from-seed:  e.from_id IS a seed → return e.to_id (must NOT be seed)
        #   to-seed:    e.to_id   IS a seed → return e.from_id (must NOT be seed)
        sql = f"""
            SELECT DISTINCT n.*
            FROM (
                SELECT e.to_id AS nid
                FROM {EDGES_TABLE} e
                WHERE e.from_id IN ({sp})
                  AND e.to_id NOT IN ({sp})
                UNION
                SELECT e.from_id AS nid
                FROM {EDGES_TABLE} e
                WHERE e.to_id IN ({sp})
                  AND e.from_id NOT IN ({sp})
            )
            JOIN {NODES_TABLE} n ON n.id = nid
            {"WHERE n.status = ?" if active_only else ""}
            ORDER BY n.updated_at DESC
            LIMIT {limit_val}
            """
        # Params: [seeds × 4] + [status?]
        #   subq1: e.from_id IN(s) + e.to_id NOT IN(s) = 2N
        #   subq2: e.to_id IN(s) + e.from_id NOT IN(s) = 2N
        #   outer: n.status = ? (if active_only) = 0 or 1
        params = list(node_ids) * 4 + status_param
        with self._conn_lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def get_edges_for_nodes(self, node_ids: list[str]) -> list[dict[str, Any]]:
        """
        Fetch all edges where both endpoints are in node_ids.

        Mirrors gm graphWalk's edge fetch:
          SELECT * FROM gm_edges WHERE from_id IN (node_ids) AND to_id IN (node_ids)
        """
        if not node_ids:
            return []
        sp = ", ".join("?" for _ in node_ids)
        sql = f"""
            SELECT e.*
            FROM {EDGES_TABLE} e
            WHERE e.from_id IN ({sp})
              AND e.to_id   IN ({sp})
            ORDER BY e.created_at ASC
        """
        with self._conn_lock:
            rows = self._conn.execute(sql, tuple(node_ids) * 2).fetchall()
        return [dict(row) for row in rows]
