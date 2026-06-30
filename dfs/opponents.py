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
    return conn.execute(
        "SELECT * FROM opponents WHERE contests_played > 0"
        " ORDER BY contests_played DESC, name"
    ).fetchall()
