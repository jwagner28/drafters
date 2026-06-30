"""Read-side aggregations for the History & Stats page.

Everything is derived from completed contests (and the opponents table), so
there's no separate stats storage to keep in sync.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from statistics import mean


def my_stats(conn: sqlite3.Connection) -> dict:
    """Overall record, win rate, ROI, best/worst, current streak, avg finish."""
    contests = conn.execute(
        "SELECT * FROM contests WHERE status='completed' ORDER BY completed_at, contest_id"
    ).fetchall()
    n = len(contests)
    if n == 0:
        return {"n": 0}

    wins = sum(1 for c in contests if c["result"] == "win")
    losses = sum(1 for c in contests if c["result"] == "loss")
    buyin = sum((c["buy_in"] or 0.0) for c in contests)
    payout = sum((c["payout"] or 0.0) for c in contests)
    scores = [c["my_actual_score"] for c in contests if c["my_actual_score"] is not None]
    finishes = [c["finish_place"] for c in contests if c["finish_place"]]

    # Current streak from the most recent decided contests backward.
    streak = 0
    streak_type = None
    for c in reversed(contests):
        if c["result"] not in ("win", "loss"):
            continue
        if streak_type is None:
            streak_type = c["result"]
            streak = 1
        elif c["result"] == streak_type:
            streak += 1
        else:
            break

    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n if n else None,
        "buy_in_total": round(buyin, 2),
        "payout_total": round(payout, 2),
        "profit": round(payout - buyin, 2),
        "roi": (payout - buyin) / buyin if buyin > 0 else None,
        "best": max(scores) if scores else None,
        "worst": min(scores) if scores else None,
        "avg_finish": round(mean(finishes), 2) if finishes else None,
        "streak": streak,
        "streak_type": streak_type,
    }


def _records_by(conn: sqlite3.Connection, field: str) -> list[dict]:
    rows = conn.execute(
        f"SELECT {field} AS k, result, buy_in, payout FROM contests WHERE status='completed'"
    ).fetchall()
    g: dict = defaultdict(lambda: {"n": 0, "wins": 0, "buyin": 0.0, "payout": 0.0})
    for r in rows:
        d = g[r["k"]]
        d["n"] += 1
        d["wins"] += 1 if r["result"] == "win" else 0
        d["buyin"] += r["buy_in"] or 0.0
        d["payout"] += r["payout"] or 0.0
    out = []
    for k, d in g.items():
        out.append({
            "key": k if k is not None else "—",
            "n": d["n"],
            "wins": d["wins"],
            "losses": d["n"] - d["wins"],
            "win_rate": d["wins"] / d["n"] if d["n"] else None,
            "roi": (d["payout"] - d["buyin"]) / d["buyin"] if d["buyin"] > 0 else None,
        })
    return sorted(out, key=lambda x: str(x["key"]))


def records_by_site(conn: sqlite3.Connection) -> list[dict]:
    return _records_by(conn, "site")


def records_by_slot(conn: sqlite3.Connection) -> list[dict]:
    return _records_by(conn, "my_draft_slot")


def h2h_ledger(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT name, h2h_wins, h2h_losses, contests_played, avg_actual_score"
        " FROM opponents WHERE contests_played > 0 ORDER BY contests_played DESC, name"
    ).fetchall()
    return [dict(r) for r in rows]


def calibration_series(conn: sqlite3.Connection, is_me: bool = True) -> list[dict]:
    """Per-entry (actual - projected) over time. Positive => model ran cold
    (under-projected). For me (is_me=True) or all opponents (is_me=False)."""
    rows = conn.execute(
        """
        SELECT c.contest_id, c.completed_at, ce.drafter_name,
               ce.projected_total, ce.actual_total
        FROM contest_entries ce JOIN contests c ON c.contest_id = ce.contest_id
        WHERE c.status='completed' AND ce.is_me=?
          AND ce.actual_total IS NOT NULL AND ce.projected_total IS NOT NULL
        ORDER BY c.completed_at, c.contest_id
        """,
        (1 if is_me else 0,),
    ).fetchall()
    return [
        {
            "contest_id": r["contest_id"],
            "date": r["completed_at"],
            "label": r["drafter_name"],
            "projected": r["projected_total"],
            "actual": r["actual_total"],
            "delta": round((r["actual_total"] or 0) - (r["projected_total"] or 0), 2),
        }
        for r in rows
    ]


def distinct_sites(conn: sqlite3.Connection) -> list[str]:
    return [r["site"] for r in conn.execute(
        "SELECT DISTINCT site FROM contests WHERE site IS NOT NULL ORDER BY site"
    )]


def distinct_slots(conn: sqlite3.Connection) -> list[int]:
    return [r["my_draft_slot"] for r in conn.execute(
        "SELECT DISTINCT my_draft_slot FROM contests WHERE my_draft_slot IS NOT NULL ORDER BY my_draft_slot"
    )]


def distinct_opponents(conn: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in conn.execute("SELECT name FROM opponents ORDER BY name")]


def history(
    conn: sqlite3.Connection,
    site: str | None = None,
    slot: int | None = None,
    opponent: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
) -> list[sqlite3.Row]:
    """Filterable contest history (by site, draft slot, opponent, slate date)."""
    sql = (
        "SELECT DISTINCT c.contest_id, c.site, c.format, c.my_draft_slot, c.status, c.result,"
        " c.finish_place, c.my_actual_score, c.buy_in, c.payout, c.created_at, c.completed_at,"
        " s.date AS slate_date "
        "FROM contests c LEFT JOIN slates s ON s.slate_id=c.slate_id "
    )
    clauses: list[str] = []
    params: list = []
    if opponent:
        sql += "JOIN contest_entries ce ON ce.contest_id=c.contest_id AND ce.is_me=0 "
        clauses.append("ce.drafter_name=?")
        params.append(opponent)
    if site:
        clauses.append("c.site=?")
        params.append(site)
    if slot is not None:
        clauses.append("c.my_draft_slot=?")
        params.append(slot)
    if status:
        clauses.append("c.status=?")
        params.append(status)
    if date_from:
        clauses.append("s.date>=?")
        params.append(date_from)
    if date_to:
        clauses.append("s.date<=?")
        params.append(date_to)
    if clauses:
        sql += "WHERE " + " AND ".join(clauses) + " "
    sql += "ORDER BY c.contest_id DESC"
    return conn.execute(sql, params).fetchall()
