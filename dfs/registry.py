"""Persistent player registry.

Players (their eligible positions + name aliases) are stored forever, so once a
position is assigned it auto-resolves on future slates. Nothing about positions
is hardcoded — unknown players are surfaced to the UI to be assigned once.

Positions are one or more of the three roster groups {IF, OF, P}. Many batters
are eligible at both IF and OF; Ohtani-types are eligible at both IF and P.
"""

from __future__ import annotations

import json
import sqlite3

from .config import POSITION_GROUPS, normalize_positions


def _normalize(name: str) -> str:
    """Loose normalization for alias/name lookups."""
    return " ".join(name.strip().lower().split())


def get_positions(row: sqlite3.Row) -> list[str]:
    """Return a player row's eligible position groups as a list."""
    return json.loads(row["positions_json"] or "[]")


def get_player_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """Resolve a name against full_name and stored aliases (case-insensitive).

    Returns the player row or None if not in the registry yet.
    """
    target = _normalize(name)
    for row in conn.execute("SELECT * FROM players"):
        if _normalize(row["full_name"]) == target:
            return row
        aliases = json.loads(row["aliases_json"] or "[]")
        if any(_normalize(a) == target for a in aliases):
            return row
    return None


def find_unknown_players(conn: sqlite3.Connection, names: list[str]) -> list[str]:
    """Return the subset of names that do not resolve to a registry entry.

    De-duplicates while preserving first-seen order.
    """
    seen: set[str] = set()
    unknown: list[str] = []
    for name in names:
        key = _normalize(name)
        if key in seen:
            continue
        seen.add(key)
        if get_player_by_name(conn, name) is None:
            unknown.append(name)
    return unknown


def upsert_player(
    conn: sqlite3.Connection,
    full_name: str,
    positions=None,
    team: str | None = None,
) -> int:
    """Create or update a player; returns player_id.

    `positions` may be a string ("IF") or list (["IF", "OF"]); it's normalized
    to canonical IF/OF/P groups. For an existing player, new positions are
    UNION-merged with the stored set (so entering Ohtani as a pitcher adds P
    without dropping his IF eligibility). Use `assign_positions` to replace.
    """
    existing = get_player_by_name(conn, full_name)
    pos_list = normalize_positions(positions)

    if existing is None:
        cur = conn.execute(
            "INSERT INTO players (full_name, team, positions_json, aliases_json)"
            " VALUES (?, ?, ?, '[]')",
            (full_name.strip(), team, json.dumps(pos_list)),
        )
        conn.commit()
        return int(cur.lastrowid)

    current = set(get_positions(existing))
    merged = [g for g in POSITION_GROUPS if g in (current | set(pos_list))]
    new_team = team or existing["team"]
    conn.execute(
        "UPDATE players SET positions_json = ?, team = ? WHERE player_id = ?",
        (json.dumps(merged), new_team, existing["player_id"]),
    )
    conn.commit()
    return int(existing["player_id"])


def assign_positions(
    conn: sqlite3.Connection,
    full_name: str,
    positions,
    team: str | None = None,
) -> int:
    """Set a player's eligible positions exactly (REPLACE, not merge).

    This is what the UI assignment form calls — the user picks the full set, so
    deselecting a group removes it.
    """
    pos_list = normalize_positions(positions)
    existing = get_player_by_name(conn, full_name)
    if existing is None:
        return upsert_player(conn, full_name, pos_list, team)
    new_team = team or existing["team"]
    conn.execute(
        "UPDATE players SET positions_json = ?, team = ? WHERE player_id = ?",
        (json.dumps(pos_list), new_team, existing["player_id"]),
    )
    conn.commit()
    return int(existing["player_id"])


def add_alias(conn: sqlite3.Connection, player_id: int, alias: str) -> None:
    """Attach an alternate spelling to a player so it auto-resolves next time."""
    row = conn.execute(
        "SELECT aliases_json, full_name FROM players WHERE player_id = ?", (player_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"No player with id {player_id}")
    aliases = json.loads(row["aliases_json"] or "[]")
    if _normalize(alias) == _normalize(row["full_name"]):
        return
    if any(_normalize(a) == _normalize(alias) for a in aliases):
        return
    aliases.append(alias.strip())
    conn.execute(
        "UPDATE players SET aliases_json = ? WHERE player_id = ?",
        (json.dumps(aliases), player_id),
    )
    conn.commit()
