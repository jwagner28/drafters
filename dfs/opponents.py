"""Opponent tracking.

Opponents are keyed by drafter name. Their head-to-head record (from *your*
perspective), games played, and average actual score are recomputed from all
completed contests whenever a contest is settled — recomputing from scratch
keeps it idempotent (re-settling never double-counts). Tendency analysis
(tendencies_json) is added in Phase 4 and is preserved here.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime


def get_or_create_opponent(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM opponents WHERE name=?", (name,)).fetchone()
    if row is not None:
        return row
    conn.execute("INSERT INTO opponents (name) VALUES (?)", (name,))
    conn.commit()
    return conn.execute("SELECT * FROM opponents WHERE name=?", (name,)).fetchone()


def recompute_from_contests(conn: sqlite3.Connection) -> None:
    """Rebuild h2h_wins/h2h_losses/contests_played/avg_actual_score for every
    opponent from completed contests. h2h is from *my* perspective (a win =
    I outscored them in that contest)."""
    rows = conn.execute(
        """
        SELECT ce.contest_id, ce.drafter_name, ce.is_me, ce.actual_total
        FROM contest_entries ce
        JOIN contests c ON c.contest_id = ce.contest_id
        WHERE c.status = 'completed'
        """
    ).fetchall()

    by_contest: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_contest[r["contest_id"]].append(r)

    agg: dict[str, dict] = {}
    for _cid, entries in by_contest.items():
        me = next((e for e in entries if e["is_me"]), None)
        my_score = me["actual_total"] if me else None
        for e in entries:
            if e["is_me"]:
                continue
            name = (e["drafter_name"] or "").strip()
            if not name or e["actual_total"] is None:
                continue
            a = agg.setdefault(name, {"count": 0, "sum": 0.0, "wins": 0, "losses": 0})
            a["count"] += 1
            a["sum"] += float(e["actual_total"])
            if my_score is not None:
                if my_score > e["actual_total"]:
                    a["wins"] += 1
                elif my_score < e["actual_total"]:
                    a["losses"] += 1

    now = datetime.now().isoformat(timespec="seconds")
    for name, a in agg.items():
        opp = get_or_create_opponent(conn, name)
        avg = a["sum"] / a["count"] if a["count"] else None
        conn.execute(
            "UPDATE opponents SET h2h_wins=?, h2h_losses=?, contests_played=?,"
            " avg_actual_score=?, last_updated=? WHERE opponent_id=?",
            (a["wins"], a["losses"], a["count"], avg, now, opp["opponent_id"]),
        )
    conn.commit()


def list_opponents(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Opponents with any activity — drafted contests OR manually-entered totals."""
    return conn.execute(
        "SELECT * FROM opponents"
        " WHERE contests_played > 0 OR manual_games IS NOT NULL OR manual_winnings IS NOT NULL"
        " ORDER BY contests_played DESC, name"
    ).fetchall()


# ---------------------------------------------------------------------------
# Manually-tracked totals + dated match history
# ---------------------------------------------------------------------------
def add_opponent(
    conn: sqlite3.Connection, name: str,
    winnings: float | None = None, games: int | None = None,
) -> sqlite3.Row:
    """Add a brand-new opponent (perhaps not yet drafted against) and optionally
    record their lifetime totals."""
    opp = get_or_create_opponent(conn, name.strip())
    if winnings is not None or games is not None:
        set_opponent_totals(conn, name.strip(), winnings, games)
    return get_or_create_opponent(conn, name.strip())


def set_opponent_totals(
    conn: sqlite3.Connection, name: str,
    winnings: float | None = None, games: int | None = None,
) -> None:
    """Set an opponent's user-entered lifetime winnings / games played."""
    opp = get_or_create_opponent(conn, name.strip())
    conn.execute(
        "UPDATE opponents SET manual_winnings=?, manual_games=? WHERE opponent_id=?",
        (winnings, games, opp["opponent_id"]),
    )
    conn.commit()


def rename_opponent(conn: sqlite3.Connection, old_name: str, new_name: str) -> None:
    """Rename an opponent, fixing the name everywhere it's referenced.

    If the new name already exists, the two are merged: contest entries and
    history ranges move onto the surviving row and the duplicate is removed.
    """
    old_name, new_name = old_name.strip(), new_name.strip()
    if not new_name or old_name == new_name:
        return
    old = conn.execute("SELECT * FROM opponents WHERE name=?", (old_name,)).fetchone()
    if old is None:
        return
    conn.execute("UPDATE contest_entries SET drafter_name=? WHERE drafter_name=?", (new_name, old_name))

    existing = conn.execute("SELECT * FROM opponents WHERE name=?", (new_name,)).fetchone()
    if existing is None:
        conn.execute("UPDATE opponents SET name=? WHERE opponent_id=?", (new_name, old["opponent_id"]))
        conn.commit()
    else:
        # Merge into the existing row: move history, keep its manual totals unless
        # empty (then adopt the old row's).
        conn.execute("UPDATE opponent_history SET opponent_id=? WHERE opponent_id=?",
                     (existing["opponent_id"], old["opponent_id"]))
        if existing["manual_winnings"] is None and existing["manual_games"] is None:
            conn.execute("UPDATE opponents SET manual_winnings=?, manual_games=? WHERE opponent_id=?",
                         (old["manual_winnings"], old["manual_games"], existing["opponent_id"]))
        conn.execute("DELETE FROM opponents WHERE opponent_id=?", (old["opponent_id"],))
        conn.commit()
    recompute_from_contests(conn)


def add_history_range(
    conn: sqlite3.Connection, name: str, start_date: str | None, end_date: str | None,
    wins: int, losses: int, winnings: float, note: str | None = None,
) -> int:
    """Record an opponent's record + winnings over a date range."""
    opp = get_or_create_opponent(conn, name.strip())
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO opponent_history"
        " (opponent_id, start_date, end_date, wins, losses, winnings, note, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (opp["opponent_id"], start_date, end_date, int(wins), int(losses),
         float(winnings), note, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_history_ranges(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    """An opponent's dated history ranges, newest first."""
    return conn.execute(
        "SELECT h.* FROM opponent_history h"
        " JOIN opponents o ON o.opponent_id = h.opponent_id"
        " WHERE o.name=?"
        " ORDER BY COALESCE(h.end_date, h.start_date) DESC, h.history_id DESC",
        (name.strip(),),
    ).fetchall()


def delete_history_range(conn: sqlite3.Connection, history_id: int) -> None:
    conn.execute("DELETE FROM opponent_history WHERE history_id=?", (history_id,))
    conn.commit()


def aggregate_history(conn: sqlite3.Connection, name: str) -> dict:
    """Totals summed across an opponent's dated history ranges."""
    row = conn.execute(
        "SELECT COALESCE(SUM(h.wins),0) AS w, COALESCE(SUM(h.losses),0) AS l,"
        " COALESCE(SUM(h.winnings),0) AS win, COUNT(*) AS n"
        " FROM opponent_history h JOIN opponents o ON o.opponent_id=h.opponent_id"
        " WHERE o.name=?",
        (name.strip(),),
    ).fetchone()
    return {"wins": int(row["w"]), "losses": int(row["l"]),
            "winnings": float(row["win"]), "ranges": int(row["n"])}
