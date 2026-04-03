import sqlite3

from agent.sparkgraph.db import (
    EVIDENCE_TABLE,
    EDGES_TABLE,
    MIGRATIONS_TABLE,
    NODES_FTS_TABLE,
    NODES_TABLE,
    SCHEMA_VERSION,
    VECTORS_TABLE,
    connect_db,
    initialize_schema,
)


def test_initialize_schema_creates_required_tables(tmp_path):
    conn = connect_db(tmp_path / "sparkgraph" / "default.db")
    initialize_schema(conn)

    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }
    assert MIGRATIONS_TABLE in tables
    assert NODES_TABLE in tables
    assert EDGES_TABLE in tables
    assert EVIDENCE_TABLE in tables
    assert VECTORS_TABLE in tables
    assert NODES_FTS_TABLE in tables


def test_initialize_schema_is_idempotent(tmp_path):
    conn = connect_db(tmp_path / "sparkgraph" / "default.db")
    initialize_schema(conn)
    initialize_schema(conn)

    versions = conn.execute(
        f"SELECT version FROM {MIGRATIONS_TABLE}"
    ).fetchall()
    assert [row["version"] for row in versions] == [SCHEMA_VERSION]


def test_schema_enforces_node_uniqueness_by_canonical_key_and_type(tmp_path):
    conn = connect_db(tmp_path / "sparkgraph" / "default.db")
    initialize_schema(conn)

    conn.execute(
        f"""
        INSERT INTO {NODES_TABLE} (
            id, type, summary, detail, status, confidence,
            source_kind, canonical_key, meta, created_at, updated_at
        ) VALUES (?, ?, ?, '', 'active', 0, 'manual', ?, '{{}}', 1, 1)
        """,
        ("node-1", "FACT", "A", "canon"),
    )
    conn.commit()

    try:
        conn.execute(
            f"""
            INSERT INTO {NODES_TABLE} (
                id, type, summary, detail, status, confidence,
                source_kind, canonical_key, meta, created_at, updated_at
            ) VALUES (?, ?, ?, '', 'active', 0, 'manual', ?, '{{}}', 1, 1)
            """,
            ("node-2", "FACT", "B", "canon"),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("Expected canonical/type uniqueness to be enforced")


def test_schema_rejects_invalid_node_enum_values(tmp_path):
    conn = connect_db(tmp_path / "sparkgraph" / "default.db")
    initialize_schema(conn)

    try:
        conn.execute(
            f"""
            INSERT INTO {NODES_TABLE} (
                id, type, summary, detail, status, confidence,
                source_kind, canonical_key, meta, created_at, updated_at
            ) VALUES (?, ?, ?, '', ?, 0, ?, ?, '{{}}', 1, 1)
            """,
            ("node-1", "RULE", "bad", "archived", "tool", "canon"),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("Expected enum CHECK constraints to reject invalid node fields")


def test_initialize_schema_backfills_last_recalled_at_and_validated_count_for_legacy_db(tmp_path):
    """v4→v5 migration: adds last_recalled_at, validated_count; drops stability, reuse_score."""
    conn = connect_db(tmp_path / "sparkgraph" / "default.db")
    # Simulate old v3/v4 schema (no last_recalled_at, validated_count; has stability, reuse_score)
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
            stability REAL NOT NULL DEFAULT 0,
            reuse_score REAL NOT NULL DEFAULT 0,
            source_kind TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            meta TEXT NOT NULL DEFAULT '{{}}',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        """
    )
    conn.commit()

    initialize_schema(conn)

    columns = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({NODES_TABLE})").fetchall()
    }
    assert "last_recalled_at" in columns
    assert "validated_count" in columns
    assert "stability" not in columns
    assert "reuse_score" not in columns
