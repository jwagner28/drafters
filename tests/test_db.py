"""Tests for DB schema creation and the legacy-schema migration."""

import json
import sqlite3

from dfs import registry
from dfs.db import connect, init_schema


def test_fresh_db_has_positions_json(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(players)")}
    assert "positions_json" in cols
    conn.close()


def test_migrates_legacy_single_position_schema(tmp_path):
    # Build a DB that looks like the original schema (no positions_json).
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.execute(
        """
        CREATE TABLE players (
            player_id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL UNIQUE,
            team TEXT,
            primary_position TEXT,
            position_group TEXT,
            aliases_json TEXT NOT NULL DEFAULT '[]'
        )
        """
    )
    raw.execute(
        "INSERT INTO players (full_name, team, primary_position, position_group)"
        " VALUES ('Old Timer', 'NYY', 'RF', 'OF')"
    )
    raw.commit()
    raw.close()

    # Opening with the current code should add + backfill positions_json.
    conn = connect(path)
    row = registry.get_player_by_name(conn, "Old Timer")
    assert registry.get_positions(row) == ["OF"]
    # And the registry still works on the migrated DB.
    registry.assign_positions(conn, "Old Timer", ["OF", "IF"])
    row = registry.get_player_by_name(conn, "Old Timer")
    assert registry.get_positions(row) == ["IF", "OF"]
    conn.close()


def test_init_schema_is_idempotent(tmp_path):
    conn = connect(tmp_path / "idem.db")
    init_schema(conn)  # second call should not raise
    init_schema(conn)
    conn.close()
