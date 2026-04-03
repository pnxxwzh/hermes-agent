"""SparkGraph database helpers and schema bootstrap."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from agent.sparkgraph.config import SparkGraphConfig, resolve_sparkgraph_db_path, sparkgraph_home

SCHEMA_VERSION = 5

MIGRATIONS_TABLE = "_migrations"
NODES_TABLE = "sg_nodes"
EDGES_TABLE = "sg_edges"
EVIDENCE_TABLE = "sg_evidence"
VECTORS_TABLE = "sg_vectors"
NODES_FTS_TABLE = "sg_nodes_fts"

NODE_TYPES = ("FACT", "PREFERENCE", "ISSUE", "RESOURCE", "DECISION")
NODE_STATUSES = ("active", "deprecated")
EDGE_TYPES = ("RELATED_TO", "SOLVES", "DEPENDS_ON", "CONFLICTS_WITH", "DERIVED_FROM", "APPLIES_TO")
SOURCE_KINDS = ("auto", "explicit", "manual", "reflection", "review", "flush", "shadow")


def ensure_sparkgraph_dir(hermes_home: Path | None = None) -> Path:
    """Ensure the profile-scoped SparkGraph directory exists."""
    home = sparkgraph_home(hermes_home)
    home.mkdir(parents=True, exist_ok=True)
    return home


def ensure_db_parent(config: SparkGraphConfig) -> Path:
    """Ensure the configured database parent directory exists."""
    parent = config.db_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def default_db_path(hermes_home: Path | None = None) -> Path:
    """Return the default SparkGraph database path for a profile."""
    return resolve_sparkgraph_db_path("", hermes_home=hermes_home)


def connect_db(db_path: Path) -> sqlite3.Connection:
    """Open a SparkGraph sqlite database connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # SparkGraph writes can happen from the main agent loop as well as the
    # background review thread. Allow the same connection object to be used
    # across threads; SQLite serializes access internally for this workload.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Create the minimal SparkGraph schema if needed."""
    node_types_sql = ", ".join(f"'{value}'" for value in NODE_TYPES)
    node_statuses_sql = ", ".join(f"'{value}'" for value in NODE_STATUSES)
    edge_types_sql = ", ".join(f"'{value}'" for value in EDGE_TYPES)
    source_kinds_sql = ", ".join(f"'{value}'" for value in SOURCE_KINDS)
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (
            version INTEGER PRIMARY KEY,
            applied_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS {NODES_TABLE} (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            summary TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0,
            source_kind TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            meta TEXT NOT NULL DEFAULT '{{}}',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            last_recalled_at INTEGER NOT NULL DEFAULT 0,
            CHECK(type IN ({node_types_sql})),
            CHECK(status IN ({node_statuses_sql})),
            CHECK(source_kind IN ({source_kinds_sql}))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_sg_nodes_canonical_type
        ON {NODES_TABLE}(canonical_key, type);
        CREATE INDEX IF NOT EXISTS ix_sg_nodes_status_type
        ON {NODES_TABLE}(status, type);
        CREATE INDEX IF NOT EXISTS ix_sg_nodes_updated_at
        ON {NODES_TABLE}(updated_at);

        CREATE TABLE IF NOT EXISTS {EDGES_TABLE} (
            id TEXT PRIMARY KEY,
            from_id TEXT NOT NULL,
            to_id TEXT NOT NULL,
            type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0,
            meta TEXT NOT NULL DEFAULT '{{}}',
            created_at INTEGER NOT NULL,
            FOREIGN KEY(from_id) REFERENCES {NODES_TABLE}(id) ON DELETE CASCADE,
            FOREIGN KEY(to_id) REFERENCES {NODES_TABLE}(id) ON DELETE CASCADE,
            CHECK(from_id <> to_id),
            CHECK(type IN ({edge_types_sql}))
        );

        CREATE INDEX IF NOT EXISTS ix_sg_edges_from_id ON {EDGES_TABLE}(from_id);
        CREATE INDEX IF NOT EXISTS ix_sg_edges_to_id ON {EDGES_TABLE}(to_id);
        CREATE INDEX IF NOT EXISTS ix_sg_edges_type ON {EDGES_TABLE}(type);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_sg_edges_unique
        ON {EDGES_TABLE}(from_id, to_id, type);

        CREATE TABLE IF NOT EXISTS {EVIDENCE_TABLE} (
            id TEXT PRIMARY KEY,
            node_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            source_hash TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(node_id) REFERENCES {NODES_TABLE}(id) ON DELETE CASCADE,
            CHECK(source_kind IN ({source_kinds_sql}))
        );

        CREATE INDEX IF NOT EXISTS ix_sg_evidence_node_id
        ON {EVIDENCE_TABLE}(node_id);
        CREATE INDEX IF NOT EXISTS ix_sg_evidence_session_turn
        ON {EVIDENCE_TABLE}(session_id, turn_index);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_sg_evidence_unique
        ON {EVIDENCE_TABLE}(node_id, session_id, turn_index, source_hash);

        CREATE TABLE IF NOT EXISTS {VECTORS_TABLE} (
            node_id TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            embedding BLOB NOT NULL,
            dims INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY(node_id) REFERENCES {NODES_TABLE}(id) ON DELETE CASCADE
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS {NODES_FTS_TABLE}
        USING fts5(summary, detail, content='{NODES_TABLE}', content_rowid='rowid');

        CREATE TRIGGER IF NOT EXISTS sg_nodes_ai AFTER INSERT ON {NODES_TABLE} BEGIN
            INSERT INTO {NODES_FTS_TABLE}(rowid, summary, detail)
            VALUES (new.rowid, new.summary, new.detail);
        END;

        CREATE TRIGGER IF NOT EXISTS sg_nodes_ad AFTER DELETE ON {NODES_TABLE} BEGIN
            INSERT INTO {NODES_FTS_TABLE}({NODES_FTS_TABLE}, rowid, summary, detail)
            VALUES ('delete', old.rowid, old.summary, old.detail);
        END;

        CREATE TRIGGER IF NOT EXISTS sg_nodes_au AFTER UPDATE ON {NODES_TABLE} BEGIN
            INSERT INTO {NODES_FTS_TABLE}({NODES_FTS_TABLE}, rowid, summary, detail)
            VALUES ('delete', old.rowid, old.summary, old.detail);
            INSERT INTO {NODES_FTS_TABLE}(rowid, summary, detail)
            VALUES (new.rowid, new.summary, new.detail);
        END;
        """
    )

    node_columns = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({NODES_TABLE})").fetchall()
    }
    if "last_recalled_at" not in node_columns:
        conn.execute(
            f"ALTER TABLE {NODES_TABLE} ADD COLUMN last_recalled_at INTEGER NOT NULL DEFAULT 0"
        )
    if "validated_count" not in node_columns:
        conn.execute(
            f"ALTER TABLE {NODES_TABLE} ADD COLUMN validated_count INTEGER NOT NULL DEFAULT 0"
        )

    # ── Migration v5: drop stability and reuse_score (no longer used) ─────────
    for col in ("stability", "reuse_score"):
        if col in node_columns:
            try:
                conn.execute(f"ALTER TABLE {NODES_TABLE} DROP COLUMN {col}")
            except Exception:
                # SQLite >= 3.35.0 required for DROP COLUMN; fallback: recreate table
                _recreate_table_drop_column(conn, NODES_TABLE, col)

    def _recreate_table_drop_column(conn: sqlite3.Connection, table: str, drop_col: str) -> None:
        """Fallback: recreate table without drop_col (SQLite < 3.35.0)."""
        col_info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        cols = [r["name"] for r in col_info if r["name"] != drop_col]
        col_list = ", ".join(cols)
        conn.execute(f"CREATE TABLE {table}_new AS SELECT {col_list} FROM {table}")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS ux_sg_nodes_canonical_type ON {NODES_TABLE}(canonical_key, type)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS ix_sg_nodes_status_type ON {NODES_TABLE}(status, type)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS ix_sg_nodes_updated_at ON {NODES_TABLE}(updated_at)")

    # ── Migration v3: add SOLVES to sg_edges CHECK constraint ──────────────────
    # SQLite CHECK constraints cannot be altered in-place.
    # Recreate sg_edges with updated CHECK, preserving all data and indexes.
    try:
        current_edge_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (EDGES_TABLE,),
        ).fetchone()
        if current_edge_sql and "SOLVES" not in current_edge_sql[0]:
            conn.execute(f"ALTER TABLE {EDGES_TABLE} RENAME TO {EDGES_TABLE}_old")
            conn.execute(
                f"""
                CREATE TABLE {EDGES_TABLE} (
                    id TEXT PRIMARY KEY,
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 0,
                    meta TEXT NOT NULL DEFAULT '{{}}',
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(from_id) REFERENCES {NODES_TABLE}(id) ON DELETE CASCADE,
                    FOREIGN KEY(to_id) REFERENCES {NODES_TABLE}(id) ON DELETE CASCADE,
                    CHECK(from_id <> to_id),
                    CHECK(type IN ({edge_types_sql}))
                )
                """
            )
            conn.execute(
                f"INSERT INTO {EDGES_TABLE}(id, from_id, to_id, type, weight, meta, created_at) "
                f"SELECT id, from_id, to_id, type, weight, meta, created_at FROM {EDGES_TABLE}_old"
            )
            conn.execute(f"DROP TABLE {EDGES_TABLE}_old")
            # Recreate indexes (SQLite doesn't persist index definitions in sqlite_master after RENAME)
            conn.execute(f"CREATE INDEX IF NOT EXISTS ix_sg_edges_from_id ON {EDGES_TABLE}(from_id)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS ix_sg_edges_to_id ON {EDGES_TABLE}(to_id)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS ix_sg_edges_type ON {EDGES_TABLE}(type)")
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS ux_sg_edges_unique "
                f"ON {EDGES_TABLE}(from_id, to_id, type)"
            )
    except Exception:
        # Migration is best-effort; if it fails the old table still works
        pass
    # ─────────────────────────────────────────────────────────────────────────

    conn.execute(
        f"""
        INSERT OR IGNORE INTO {MIGRATIONS_TABLE}(version, applied_at)
        VALUES (?, ?)
        """,
        (SCHEMA_VERSION, int(time.time())),
    )
    conn.commit()
