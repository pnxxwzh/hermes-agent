"""Tests for SparkGraph DB migrations and FTS rebuild."""
import pytest
import sqlite3
from pathlib import Path
from agent.sparkgraph.db import (
    NODES_TABLE,
    NODES_FTS_TABLE,
    _rebuild_fts_table,
    initialize_schema,
)


def test_rebuild_fts_table_populates_fts_from_nodes(tmp_path):
    """_rebuild_fts_table recreates FTS index from sg_nodes content."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))

    # Create minimal schema
    conn.execute(f"CREATE TABLE {NODES_TABLE} (id, summary, detail, status)")
    conn.execute(f"INSERT INTO {NODES_TABLE} VALUES ('n1', 'proxy pac script', 'detail1', 'active')")
    conn.execute(f"INSERT INTO {NODES_TABLE} VALUES ('n2', 'docker compose', 'detail2', 'active')")

    # No FTS table yet
    with pytest.raises(sqlite3.OperationalError):
        conn.execute(f"SELECT * FROM {NODES_FTS_TABLE}").fetchall()

    # Rebuild FTS
    _rebuild_fts_table(conn)

    # Now FTS should work and find content
    results = conn.execute(
        f"SELECT rowid, summary, detail FROM {NODES_FTS_TABLE} WHERE summary MATCH 'proxy'"
    ).fetchall()
    assert len(results) == 1
    assert results[0][1] == "proxy pac script"

    conn.close()


def test_initialize_schema_recreates_table_when_drop_column_not_supported():
    """Fallback migration path should work on SQLite builds without DROP COLUMN."""

    class _ConnProxy:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args, **kwargs):
            if "ALTER TABLE" in sql and "DROP COLUMN" in sql:
                raise sqlite3.OperationalError("DROP COLUMN not supported")
            return self._conn.execute(sql, *args, **kwargs)

        def executescript(self, *args, **kwargs):
            return self._conn.executescript(*args, **kwargs)

        def commit(self):
            return self._conn.commit()

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        f"""
        CREATE TABLE {NODES_TABLE} (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            summary TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0,
            source_kind TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            meta TEXT NOT NULL DEFAULT '{{}}',
            source_sessions TEXT NOT NULL DEFAULT '[]',
            default_inject INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            last_recalled_at INTEGER NOT NULL DEFAULT 0,
            validated_count INTEGER NOT NULL DEFAULT 0,
            stability REAL,
            reuse_score REAL
        )
        """
    )

    initialize_schema(_ConnProxy(conn))

    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({NODES_TABLE})").fetchall()}
    assert "stability" not in cols
    assert "reuse_score" not in cols
