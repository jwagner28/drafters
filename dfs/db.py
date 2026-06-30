"""SQLite database: connection helper and schema creation.

Everything lives in a single SQLite file so the whole app state is one easy
backup. The full schema from the build brief is created up front (even tables
that later phases use) so migrations stay simple.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

# Default location of the single SQLite file (repo-local ./data/dfs.db).
# db.py lives at <repo>/dfs/db.py, so parents[1] is the repo root.
# Override with the DFS_DB_PATH environment variable to relocate it.
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "dfs.db"


def effective_db_path() -> Path:
    """The DB path actually used by default (env override, else DEFAULT_DB_PATH)."""
    env = os.environ.get("DFS_DB_PATH")
    return Path(env) if env else DEFAULT_DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    player_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name      TEXT NOT NULL UNIQUE,
    team           TEXT,
    -- One or more eligible groups from {IF, OF, P}, stored as a JSON array.
    -- A player can be IF+OF (flex bat) or IF+P (Ohtani-type).
    positions_json TEXT NOT NULL DEFAULT '[]',
    aliases_json   TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS slates (
    slate_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,
    notes      TEXT
);

CREATE TABLE IF NOT EXISTS batter_projections (
    slate_id     INTEGER NOT NULL REFERENCES slates(slate_id) ON DELETE CASCADE,
    player_id    INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
    proj_pts     REAL NOT NULL,
    e_r          REAL,
    e_1b         REAL,
    e_2b         REAL,
    e_3b         REAL,
    e_hr         REAL,
    e_rbi        REAL,
    e_sb         REAL,
    game         TEXT,
    game_time_et TEXT,
    flags_json   TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (slate_id, player_id)
);

CREATE TABLE IF NOT EXISTS pitcher_projections (
    slate_id   INTEGER NOT NULL REFERENCES slates(slate_id) ON DELETE CASCADE,
    player_id  INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
    proj_pts   REAL NOT NULL,
    PRIMARY KEY (slate_id, player_id)
);

CREATE TABLE IF NOT EXISTS contests (
    contest_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    slate_id        INTEGER REFERENCES slates(slate_id),
    site            TEXT,
    format          TEXT,
    my_draft_slot   INTEGER,
    status          TEXT,
    result          TEXT,
    finish_place    INTEGER,
    my_actual_score REAL,
    buy_in          REAL,
    payout          REAL,
    created_at      TEXT,
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS contest_entries (
    entry_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    contest_id      INTEGER NOT NULL REFERENCES contests(contest_id) ON DELETE CASCADE,
    drafter_name    TEXT,
    is_me           INTEGER NOT NULL DEFAULT 0,
    draft_slot      INTEGER,
    projected_total REAL,
    actual_total    REAL,
    finish_place    INTEGER
);

CREATE TABLE IF NOT EXISTS draft_picks (
    pick_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    contest_id          INTEGER NOT NULL REFERENCES contests(contest_id) ON DELETE CASCADE,
    entry_id            INTEGER NOT NULL REFERENCES contest_entries(entry_id) ON DELETE CASCADE,
    overall_pick_number INTEGER NOT NULL,
    round_number        INTEGER,
    slot_in_round       INTEGER,
    player_id           INTEGER REFERENCES players(player_id),
    player_projection   REAL,
    roster_slot         TEXT
);

CREATE TABLE IF NOT EXISTS opponents (
    opponent_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    h2h_wins        INTEGER NOT NULL DEFAULT 0,
    h2h_losses      INTEGER NOT NULL DEFAULT 0,
    contests_played INTEGER NOT NULL DEFAULT 0,
    avg_actual_score REAL,
    tendencies_json TEXT NOT NULL DEFAULT '{}',
    last_updated    TEXT
);

CREATE TABLE IF NOT EXISTS substitutions (
    contest_id    INTEGER NOT NULL REFERENCES contests(contest_id) ON DELETE CASCADE,
    entry_id      INTEGER NOT NULL REFERENCES contest_entries(entry_id) ON DELETE CASCADE,
    out_player_id INTEGER REFERENCES players(player_id),
    in_player_id  INTEGER REFERENCES players(player_id),
    reason        TEXT,
    delta         REAL
);

CREATE INDEX IF NOT EXISTS idx_draft_picks_contest ON draft_picks(contest_id, overall_pick_number);
CREATE INDEX IF NOT EXISTS idx_entries_contest ON contest_entries(contest_id);
CREATE INDEX IF NOT EXISTS idx_batproj_slate ON batter_projections(slate_id);
"""


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection with sane defaults and ensure the schema exists.

    With no argument, uses `effective_db_path()` (DFS_DB_PATH env var or the
    repo-local default).
    """
    path = Path(db_path) if db_path is not None else effective_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: Streamlit caches one connection across its worker
    # threads. Safe here — single local user, and writes are short/serialized.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables/indexes if they do not already exist, then migrate."""
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Lightweight, idempotent migrations for DBs created by older versions.

    Currently: the original schema stored a single `primary_position` /
    `position_group` per player; players now hold a set of eligible groups in
    `positions_json`. If an old DB is opened, add the column and backfill it
    from the legacy `position_group` so existing assignments survive.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(players)")}
    if "positions_json" not in cols:
        conn.execute(
            "ALTER TABLE players ADD COLUMN positions_json TEXT NOT NULL DEFAULT '[]'"
        )
        if "position_group" in cols:
            for row in conn.execute(
                "SELECT player_id, position_group FROM players"
            ).fetchall():
                grp = row["position_group"]
                positions = [grp] if grp in ("IF", "OF", "P") else []
                conn.execute(
                    "UPDATE players SET positions_json = ? WHERE player_id = ?",
                    (json.dumps(positions), row["player_id"]),
                )
        conn.commit()
