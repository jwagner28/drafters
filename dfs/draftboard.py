"""Draft-board data loaders (read-only).

Builds the three position lists (IF / OF / P) that the temporary Draft Board
page renders. This module only *reads* projections — the "taken" marks live in
Streamlit session state and are never persisted.
"""

from __future__ import annotations

import json
import re
import sqlite3

import pandas as pd

from . import slate as slate_mod
from .config import normalize_positions

GROUPS = ["IF", "OF", "P"]


def _sorted(rows: list[dict]) -> list[dict]:
    """Sort a list of {Team, Name, Proj} records by Proj descending."""
    return sorted(rows, key=lambda r: r["Proj"], reverse=True)


def load_board_from_slate(conn: sqlite3.Connection, slate_id: int) -> tuple[dict[str, list[dict]], list[str]]:
    """Build the IF/OF/P board from a saved slate.

    A batter eligible at both IF and OF appears in both lists; an Ohtani-type
    (IF + a manual pitcher projection) appears in both IF and P. Returns the
    board plus a list of any batters with no IF/OF assignment (hidden).
    """
    board: dict[str, list[dict]] = {g: [] for g in GROUPS}
    unassigned: list[str] = []

    for r in slate_mod.load_slate_projections(conn, slate_id):
        positions = json.loads(r["positions_json"] or "[]")
        rec = {"Team": r["team"] or "", "Name": r["full_name"], "Proj": round(float(r["proj_pts"]), 2)}
        placed = False
        if "IF" in positions:
            board["IF"].append(dict(rec))
            placed = True
        if "OF" in positions:
            board["OF"].append(dict(rec))
            placed = True
        if not placed:
            unassigned.append(r["full_name"])

    for row in conn.execute(
        "SELECT pp.proj_pts, pl.full_name, pl.team FROM pitcher_projections pp "
        "JOIN players pl ON pl.player_id = pp.player_id WHERE pp.slate_id = ?",
        (slate_id,),
    ):
        board["P"].append(
            {"Team": row["team"] or "", "Name": row["full_name"], "Proj": round(float(row["proj_pts"]), 2)}
        )

    return {g: _sorted(board[g]) for g in GROUPS}, unassigned


def load_board_from_csv(raw: pd.DataFrame) -> dict[str, list[dict]]:
    """Build the board from a tidy CSV.

    Columns (case-insensitive): name (or player), position/pos/group (IF/OF/P,
    or granular like SS/CF which get folded), proj (or proj_pts), optional team.
    A single row may list multiple positions ("IF/OF"); it lands in each list.
    """
    cols = {str(c).lower().strip(): c for c in raw.columns}

    def pick(*candidates: str) -> str | None:
        for c in candidates:
            if c in cols:
                return cols[c]
        return None

    name_col = pick("name", "player", "full_name")
    proj_col = pick("proj", "proj_pts", "projection", "points")
    pos_col = pick("pos", "position", "positions", "group", "slot")
    team_col = pick("team", "tm")
    if name_col is None or proj_col is None or pos_col is None:
        raise ValueError(
            "CSV needs at least 'name', 'position' (IF/OF/P) and 'proj' columns."
        )

    board: dict[str, list[dict]] = {g: [] for g in GROUPS}
    for _, row in raw.iterrows():
        name = str(row[name_col]).strip()
        if not name or name.lower() == "nan":
            continue
        try:
            proj = round(float(row[proj_col]), 2)
        except (TypeError, ValueError):
            continue
        tokens = re.split(r"[^A-Za-z]+", str(row[pos_col]))
        groups = normalize_positions(tokens)
        team = ""
        if team_col is not None:
            team = str(row[team_col]).strip()
            if team.lower() == "nan":
                team = ""
        rec = {"Team": team, "Name": name, "Proj": proj}
        for g in groups:
            board[g].append(dict(rec))

    return {g: _sorted(board[g]) for g in GROUPS}
