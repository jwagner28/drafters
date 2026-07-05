"""Slate persistence: write projections to the DB and resolve positions.

A "slate" is one day's set of games/props. Saving a slate creates registry
rows for every projected player (so each has a stable player_id) and records
which ones still need a position assigned.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date as date_cls

from . import registry
from .projections import BatterProjection


def create_slate(conn: sqlite3.Connection, date: str | None = None, notes: str | None = None) -> int:
    """Create a slate row and return its id."""
    date = date or date_cls.today().isoformat()
    cur = conn.execute("INSERT INTO slates (date, notes) VALUES (?, ?)", (date, notes))
    conn.commit()
    return int(cur.lastrowid)


def get_daily_slate(conn: sqlite3.Connection, date: str) -> int | None:
    """The slate id for a date if one exists (most recent), else None. No create."""
    row = conn.execute(
        "SELECT slate_id FROM slates WHERE date=? ORDER BY slate_id DESC LIMIT 1", (date,)
    ).fetchone()
    return int(row["slate_id"]) if row is not None else None


def get_or_create_daily_slate(
    conn: sqlite3.Connection, date: str | None = None, notes: str | None = None
) -> int:
    """Return THE slate for a date, creating it once if needed.

    Enforces "one projections page per day": all pulls for a date merge into the
    same slate rather than spawning a new one each time. If several slates already
    exist for the date (from older versions), the most recent is reused.
    """
    date = date or date_cls.today().isoformat()
    row = conn.execute(
        "SELECT slate_id FROM slates WHERE date=? ORDER BY slate_id DESC LIMIT 1", (date,)
    ).fetchone()
    if row is not None:
        return int(row["slate_id"])
    return create_slate(conn, date, notes)


def ensure_players(conn: sqlite3.Connection, names: list[str]) -> dict[str, int]:
    """Make sure every name has a registry row; return name -> player_id.

    Players new to the registry are created with no position (they show up in
    the assignment queue until the user assigns one).
    """
    ids: dict[str, int] = {}
    for name in names:
        existing = registry.get_player_by_name(conn, name)
        if existing is not None:
            ids[name] = int(existing["player_id"])
        else:
            ids[name] = registry.upsert_player(conn, name)
    return ids


def players_needing_position(conn: sqlite3.Connection, names: list[str]) -> list[str]:
    """Names (from this slate) whose registry entry has no positions yet."""
    pending: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        row = registry.get_player_by_name(conn, name)
        if row is None or not registry.get_positions(row):
            pending.append(name)
    return pending


def save_batter_projections(
    conn: sqlite3.Connection,
    slate_id: int,
    projections: list[BatterProjection],
) -> dict[str, int]:
    """Persist batter projections for a slate. Returns name -> player_id.

    Replaces any existing batter projections for this slate (idempotent re-save).
    """
    names = [p.player for p in projections]
    ids = ensure_players(conn, names)

    conn.execute("DELETE FROM batter_projections WHERE slate_id = ?", (slate_id,))
    for p in projections:
        pid = ids[p.player]
        conn.execute(
            """
            INSERT INTO batter_projections
                (slate_id, player_id, proj_pts, e_r, e_1b, e_2b, e_3b, e_hr, e_rbi, e_sb, e_bb,
                 game, game_time_et, flags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slate_id, pid, p.proj_pts, p.e_r, p.e_1b, p.e_2b, p.e_3b, p.e_hr,
                p.e_rbi, p.e_sb, getattr(p, "e_bb", None), p.game, p.game_time,
                json.dumps(p.flags),
            ),
        )
    conn.commit()
    return ids


def merge_batter_projections(
    conn: sqlite3.Connection,
    slate_id: int,
    projections: list[BatterProjection],
) -> dict[str, int]:
    """Upsert batter projections WITHOUT deleting players missing from this pull.

    Used by the "Update batter props" button: refresh the players that still have
    props, but leave players whose game already started (and so dropped out of the
    odds feed) at their last-known projection. Returns name -> player_id.
    """
    names = [p.player for p in projections]
    ids = ensure_players(conn, names)
    for p in projections:
        pid = ids[p.player]
        conn.execute(
            """
            INSERT OR REPLACE INTO batter_projections
                (slate_id, player_id, proj_pts, e_r, e_1b, e_2b, e_3b, e_hr, e_rbi, e_sb, e_bb,
                 game, game_time_et, flags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slate_id, pid, p.proj_pts, p.e_r, p.e_1b, p.e_2b, p.e_3b, p.e_hr,
                p.e_rbi, p.e_sb, getattr(p, "e_bb", None), p.game, p.game_time,
                json.dumps(p.flags),
            ),
        )
    conn.commit()
    return ids


def slate_counts(conn: sqlite3.Connection, slate_id: int) -> dict[str, int]:
    """How many batters / pitchers are stored for a slate."""
    nb = conn.execute(
        "SELECT COUNT(*) AS n FROM batter_projections WHERE slate_id=?", (slate_id,)
    ).fetchone()["n"]
    np_ = conn.execute(
        "SELECT COUNT(*) AS n FROM pitcher_projections WHERE slate_id=?", (slate_id,)
    ).fetchone()["n"]
    return {"batters": int(nb), "pitchers": int(np_)}


def save_pitcher_projection(
    conn: sqlite3.Connection,
    slate_id: int,
    name: str,
    proj_pts: float,
    team: str | None = None,
) -> int:
    """Store a manually-entered pitcher projection. Returns player_id."""
    # Union-merge "P" so an Ohtani-type keeps any existing IF/OF eligibility.
    pid = registry.upsert_player(conn, name, positions="P", team=team)
    conn.execute(
        "INSERT OR REPLACE INTO pitcher_projections (slate_id, player_id, proj_pts) VALUES (?, ?, ?)",
        (slate_id, pid, proj_pts),
    )
    conn.commit()
    return pid


def load_slate_projections(conn: sqlite3.Connection, slate_id: int) -> list[sqlite3.Row]:
    """Load batter projections (joined with player info) for a slate."""
    return conn.execute(
        """
        SELECT bp.*, pl.full_name, pl.team, pl.positions_json
        FROM batter_projections bp
        JOIN players pl ON pl.player_id = bp.player_id
        WHERE bp.slate_id = ?
        ORDER BY bp.proj_pts DESC
        """,
        (slate_id,),
    ).fetchall()


def list_slates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM slates ORDER BY slate_id DESC").fetchall()
