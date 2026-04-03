"""Tests for SparkGraph DB migrations and FTS rebuild."""
import pytest
import sqlite3
from pathlib import Path
from agent.sparkgraph.db import (
    NODES_TABLE,
    NODES_FTS_TABLE,
    _rebuild_fts_table,
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
