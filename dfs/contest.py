"""Contest scoring and persistence.

Given a saved slate's projections and a drafted board, this resolves each pick
to a projection, sums per-entry totals, and writes the contest + entries +
picks to the DB. It also produces the per-entry summary used by the UI boards
(floor, pitching/hitting split, DNP count).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from . import opponents as opponents_mod

HITTER_SLOTS = {"IF", "OF", "HT"}


def resolve_pick_projection(
    conn: sqlite3.Connection, slate_id: int, player_id: int, roster_slot: str | None
) -> tuple[float, str]:
    """Projection + source for one drafted player, using the *latest pull of the
    same date* as the contest's slate.

    Odds pulled early in the day are incomplete (a player's props post closer to
    game time). By resolving against the most recent slate with the same date, a
    fresh re-pull automatically fills in players that were missing — no need to
    re-draft. Falls back through same-date slates newest-first, then DNP -> 0.
    """
    row = conn.execute("SELECT date FROM slates WHERE slate_id=?", (slate_id,)).fetchone()
    if row is not None:
        slate_ids = [r["slate_id"] for r in conn.execute(
            "SELECT slate_id FROM slates WHERE date=? ORDER BY slate_id DESC", (row["date"],))]
        if slate_id not in slate_ids:
            slate_ids.insert(0, slate_id)
    else:
        slate_ids = [slate_id]

    for sid in slate_ids:
        proj, source = _resolve_in_slate(conn, sid, player_id, roster_slot)
        if source != "dnp":
            return proj, source
    return 0.0, "dnp"


def _resolve_in_slate(
    conn: sqlite3.Connection, slate_id: int, player_id: int, roster_slot: str | None
) -> tuple[float, str]:
    """Resolve a player's projection within a single slate.

    source ∈ {"pitcher", "batter", "batter_fallback", "dnp"}.
    - P slot: pitcher projection; else a batter projection (Ohtani-type); else DNP.
    - Hitter slot: batter projection; else (defensively) a pitcher projection; else DNP.
    """
    def batter() -> float | None:
        row = conn.execute(
            "SELECT proj_pts FROM batter_projections WHERE slate_id=? AND player_id=?",
            (slate_id, player_id),
        ).fetchone()
        return float(row["proj_pts"]) if row else None

    def pitcher() -> float | None:
        row = conn.execute(
            "SELECT proj_pts FROM pitcher_projections WHERE slate_id=? AND player_id=?",
            (slate_id, player_id),
        ).fetchone()
        return float(row["proj_pts"]) if row else None

    if roster_slot == "P":
        p = pitcher()
        if p is not None:
            return p, "pitcher"
        b = batter()
        if b is not None:
            return b, "batter_fallback"
        return 0.0, "dnp"

    b = batter()
    if b is not None:
        return b, "batter"
    p = pitcher()
    if p is not None:
        return p, "pitcher"
    return 0.0, "dnp"


def save_contest(
    conn: sqlite3.Connection,
    slate_id: int,
    entries: list[dict],
    site: str | None = None,
    format: str | None = None,
    my_draft_slot: int | None = None,
    buy_in: float | None = None,
    status: str = "active",
) -> int:
    """Persist a contest + its entries + picks; returns contest_id.

    `entries` is a list of dicts:
        {drafter_name, is_me, draft_slot,
         picks: [{overall_pick_number, round_number, slot_in_round,
                  player_id, roster_slot}]}
    Each pick's projection is resolved and stored; each entry's projected_total
    is the sum.
    """
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO contests (slate_id, site, format, my_draft_slot, status, buy_in, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (slate_id, site, format, my_draft_slot, status, buy_in, now),
    )
    contest_id = int(cur.lastrowid)

    for e in entries:
        ecur = conn.execute(
            "INSERT INTO contest_entries (contest_id, drafter_name, is_me, draft_slot, projected_total)"
            " VALUES (?, ?, ?, ?, ?)",
            (contest_id, e.get("drafter_name"), int(e.get("is_me", 0)), e.get("draft_slot"), 0.0),
        )
        entry_id = int(ecur.lastrowid)
        total = 0.0
        for p in e.get("picks", []):
            proj, source = resolve_pick_projection(conn, slate_id, p["player_id"], p.get("roster_slot"))
            conn.execute(
                "INSERT INTO draft_picks"
                " (contest_id, entry_id, overall_pick_number, round_number, slot_in_round,"
                "  player_id, player_projection, proj_source, roster_slot)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    contest_id, entry_id, p["overall_pick_number"], p.get("round_number"),
                    p.get("slot_in_round"), p["player_id"], round(proj, 2), source,
                    p.get("roster_slot"),
                ),
            )
            total += proj
        conn.execute(
            "UPDATE contest_entries SET projected_total=? WHERE entry_id=?",
            (round(total, 2), entry_id),
        )

    conn.commit()
    return contest_id


def summarize_entry(picks: list[dict]) -> dict:
    """Floor, pitching/hitting split, and DNP count for one entry's picks.

    Each pick dict needs `player_projection`, `roster_slot`, and `source`.
    """
    if not picks:
        return {"floor": 0.0, "pitching": 0.0, "hitting": 0.0, "dnp": 0}
    projs = [float(p["player_projection"]) for p in picks]
    pitching = sum(float(p["player_projection"]) for p in picks if p.get("roster_slot") == "P"
                   and p.get("source") != "batter_fallback")
    hitting = sum(float(p["player_projection"]) for p in picks
                  if p.get("roster_slot") in HITTER_SLOTS or p.get("source") == "batter_fallback")
    dnp = sum(1 for p in picks if p.get("source") == "dnp")
    return {
        "floor": round(min(projs), 2),
        "pitching": round(pitching, 2),
        "hitting": round(hitting, 2),
        "dnp": dnp,
    }


def load_contest(conn: sqlite3.Connection, contest_id: int) -> dict:
    """Load a contest with its entries, picks (with live source flags), and
    per-entry summaries, plus which entry is the projected leader."""
    contest = conn.execute("SELECT * FROM contests WHERE contest_id=?", (contest_id,)).fetchone()
    if contest is None:
        raise ValueError(f"No contest {contest_id}")
    slate_id = contest["slate_id"]

    entries = []
    erows = conn.execute(
        "SELECT * FROM contest_entries WHERE contest_id=? ORDER BY draft_slot, entry_id",
        (contest_id,),
    ).fetchall()
    for e in erows:
        prows = conn.execute(
            "SELECT dp.*, pl.full_name FROM draft_picks dp"
            " JOIN players pl ON pl.player_id = dp.player_id"
            " WHERE dp.entry_id=? ORDER BY dp.overall_pick_number",
            (e["entry_id"],),
        ).fetchall()
        picks = []
        for p in prows:
            override = p["proj_override"]
            if override is not None:
                # Manual override wins and is sticky across refreshes.
                proj, source = float(override), "manual"
            elif p["proj_source"] is not None:
                # Stored value — set by save/refresh. NOT re-resolved live, so a
                # player whose game started (props gone) keeps their projection.
                proj, source = float(p["player_projection"] or 0.0), p["proj_source"]
            else:
                # Legacy pick saved before proj_source existed — resolve once.
                proj, source = resolve_pick_projection(conn, slate_id, p["player_id"], p["roster_slot"])
            picks.append({
                "pick_id": p["pick_id"],
                "overall_pick_number": p["overall_pick_number"],
                "round_number": p["round_number"],
                "player_id": p["player_id"],
                "full_name": p["full_name"],
                "player_projection": round(proj, 2),
                "roster_slot": p["roster_slot"],
                "source": source,
                "overridden": override is not None,
            })
        live_total = round(sum(pk["player_projection"] for pk in picks), 2)
        entries.append({
            "entry_id": e["entry_id"],
            "drafter_name": e["drafter_name"],
            "is_me": bool(e["is_me"]),
            "draft_slot": e["draft_slot"],
            "projected_total": live_total,
            "actual_total": e["actual_total"],
            "picks": picks,
            "summary": summarize_entry(picks),
        })

    leader_id = None
    if entries:
        leader_id = max(entries, key=lambda x: (x["projected_total"] or 0.0))["entry_id"]

    return {"contest": dict(contest), "entries": entries, "leader_entry_id": leader_id}


def slate_player_options(conn: sqlite3.Connection, slate_id: int) -> list[tuple[int, str, str]]:
    """Players that have a projection in a slate: (player_id, name, kind)."""
    out: list[tuple[int, str, str]] = []
    for r in conn.execute(
        "SELECT bp.player_id, pl.full_name FROM batter_projections bp"
        " JOIN players pl ON pl.player_id=bp.player_id WHERE bp.slate_id=?"
        " ORDER BY bp.proj_pts DESC",
        (slate_id,),
    ):
        out.append((r["player_id"], r["full_name"], "batter"))
    for r in conn.execute(
        "SELECT pp.player_id, pl.full_name FROM pitcher_projections pp"
        " JOIN players pl ON pl.player_id=pp.player_id WHERE pp.slate_id=?"
        " ORDER BY pp.proj_pts DESC",
        (slate_id,),
    ):
        out.append((r["player_id"], r["full_name"], "pitcher"))
    return out


def substitute_player(
    conn: sqlite3.Connection,
    contest_id: int,
    entry_id: int,
    pick_id: int,
    in_player_id: int,
    reason: str | None = None,
) -> float:
    """Swap a drafted player on one pick; recompute the entry total, log it.

    Returns the projection delta (new - old).
    """
    pick = conn.execute(
        "SELECT * FROM draft_picks WHERE pick_id=? AND entry_id=? AND contest_id=?",
        (pick_id, entry_id, contest_id),
    ).fetchone()
    if pick is None:
        raise ValueError("pick not found for this entry/contest")
    contest = conn.execute("SELECT slate_id FROM contests WHERE contest_id=?", (contest_id,)).fetchone()
    slate_id = contest["slate_id"]

    out_player_id = pick["player_id"]
    old_proj = float(pick["proj_override"] if pick["proj_override"] is not None
                     else (pick["player_projection"] or 0.0))
    new_proj, src = resolve_pick_projection(conn, slate_id, in_player_id, pick["roster_slot"])
    new_proj = round(new_proj, 2)
    delta = round(new_proj - old_proj, 2)

    conn.execute(
        "UPDATE draft_picks SET player_id=?, player_projection=?, proj_source=?,"
        " proj_override=NULL WHERE pick_id=?",
        (in_player_id, new_proj, src, pick_id),
    )
    _recompute_entry_total(conn, entry_id)
    conn.execute(
        "INSERT INTO substitutions (contest_id, entry_id, out_player_id, in_player_id, reason, delta)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (contest_id, entry_id, out_player_id, in_player_id, reason, delta),
    )
    conn.commit()
    return delta


def _recompute_entry_total(conn: sqlite3.Connection, entry_id: int) -> None:
    """Set an entry's projected_total to the sum of effective pick projections
    (manual override where present, else the stored projection)."""
    total = conn.execute(
        "SELECT COALESCE(SUM(COALESCE(proj_override, player_projection)), 0) AS t"
        " FROM draft_picks WHERE entry_id=?",
        (entry_id,),
    ).fetchone()["t"]
    conn.execute(
        "UPDATE contest_entries SET projected_total=? WHERE entry_id=?",
        (round(float(total), 2), entry_id),
    )
    conn.commit()


def refresh_contest_projections(conn: sqlite3.Connection, contest_id: int) -> dict:
    """Re-resolve every pick from the latest same-date slate pull.

    Picks with a manual override are left alone. For the rest: if a fresh
    projection exists it's stored; if the player has dropped out of the odds feed
    (game started) BUT already had a non-zero projection, the old value is KEPT
    (never zeroed). Returns {updated, kept, contests_entries}.
    """
    contest = conn.execute("SELECT slate_id FROM contests WHERE contest_id=?", (contest_id,)).fetchone()
    if contest is None:
        raise ValueError(f"No contest {contest_id}")
    slate_id = contest["slate_id"]

    picks = conn.execute(
        "SELECT * FROM draft_picks WHERE contest_id=?", (contest_id,)
    ).fetchall()
    updated = kept = 0
    entry_ids = set()
    for p in picks:
        entry_ids.add(p["entry_id"])
        if p["proj_override"] is not None:
            continue  # sticky manual value
        proj, source = resolve_pick_projection(conn, slate_id, p["player_id"], p["roster_slot"])
        if source != "dnp":
            conn.execute(
                "UPDATE draft_picks SET player_projection=?, proj_source=? WHERE pick_id=?",
                (round(proj, 2), source, p["pick_id"]),
            )
            updated += 1
        elif float(p["player_projection"] or 0.0) > 0:
            kept += 1  # game started / props gone — keep last-known, don't zero
        else:
            conn.execute(
                "UPDATE draft_picks SET player_projection=0, proj_source='dnp' WHERE pick_id=?",
                (p["pick_id"],),
            )
    for eid in entry_ids:
        _recompute_entry_total(conn, eid)
    conn.commit()
    return {"updated": updated, "kept": kept, "entries": len(entry_ids)}


def set_pick_override(conn: sqlite3.Connection, pick_id: int, value: float | None) -> None:
    """Manually set (or clear, with None) a pick's projection. Overrides are
    sticky — they survive `refresh_contest_projections`."""
    row = conn.execute("SELECT entry_id FROM draft_picks WHERE pick_id=?", (pick_id,)).fetchone()
    if row is None:
        raise ValueError(f"No pick {pick_id}")
    conn.execute(
        "UPDATE draft_picks SET proj_override=? WHERE pick_id=?",
        (None if value is None else round(float(value), 2), pick_id),
    )
    _recompute_entry_total(conn, row["entry_id"])


def set_pick_player(conn: sqlite3.Connection, pick_id: int, player_id: int) -> None:
    """Repoint a pick at a different registry player (fix an OCR/name mistake)
    and re-resolve its projection. Any manual override is cleared."""
    pick = conn.execute("SELECT * FROM draft_picks WHERE pick_id=?", (pick_id,)).fetchone()
    if pick is None:
        raise ValueError(f"No pick {pick_id}")
    contest = conn.execute(
        "SELECT slate_id FROM contests WHERE contest_id=?", (pick["contest_id"],)
    ).fetchone()
    proj, source = resolve_pick_projection(conn, contest["slate_id"], player_id, pick["roster_slot"])
    conn.execute(
        "UPDATE draft_picks SET player_id=?, player_projection=?, proj_source=?,"
        " proj_override=NULL WHERE pick_id=?",
        (player_id, round(proj, 2), source, pick_id),
    )
    _recompute_entry_total(conn, pick["entry_id"])


def rename_entry_drafter(conn: sqlite3.Connection, entry_id: int, new_name: str) -> None:
    """Rename the drafter on one contest entry."""
    conn.execute(
        "UPDATE contest_entries SET drafter_name=? WHERE entry_id=?",
        (new_name.strip(), entry_id),
    )
    conn.commit()


def settle_contest(
    conn: sqlite3.Connection,
    contest_id: int,
    actuals: dict[int, float],
    result: str | None = None,
    payout: float | None = None,
) -> dict:
    """Record actual scores, rank finishes, mark completed, update opponents.

    `actuals` maps entry_id -> actual_total. Finish places use standard
    competition ranking (ties share a place). If `result` is None it's derived
    from my finish (1st = win). Re-settling is safe (opponents recompute from
    scratch).
    """
    entries = conn.execute(
        "SELECT * FROM contest_entries WHERE contest_id=?", (contest_id,)
    ).fetchall()

    for e in entries:
        score = actuals.get(e["entry_id"])
        if score is not None:
            conn.execute(
                "UPDATE contest_entries SET actual_total=? WHERE entry_id=?",
                (float(score), e["entry_id"]),
            )

    scored = sorted(
        ((eid, s) for eid, s in ((e["entry_id"], actuals.get(e["entry_id"])) for e in entries) if s is not None),
        key=lambda x: x[1],
        reverse=True,
    )
    place = 0
    prev = None
    for i, (eid, score) in enumerate(scored):
        if score != prev:
            place = i + 1
            prev = score
        conn.execute("UPDATE contest_entries SET finish_place=? WHERE entry_id=?", (place, eid))

    me = next((e for e in entries if e["is_me"]), None)
    my_score = actuals.get(me["entry_id"]) if me else None
    my_finish = None
    if me:
        row = conn.execute(
            "SELECT finish_place FROM contest_entries WHERE entry_id=?", (me["entry_id"],)
        ).fetchone()
        my_finish = row["finish_place"] if row else None
    if result is None and my_finish is not None:
        result = "win" if my_finish == 1 else "loss"

    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE contests SET status='completed', result=?, finish_place=?, my_actual_score=?,"
        " payout=?, completed_at=? WHERE contest_id=?",
        (result, my_finish, my_score, payout, now, contest_id),
    )
    conn.commit()

    opponents_mod.recompute_from_contests(conn)
    # Refresh opponent draft tendencies (Phase 4). Imported lazily to avoid a
    # heavier import on the common scoring path.
    from . import tendencies as tendencies_mod
    tendencies_mod.compute_all_tendencies(conn)
    return {"result": result, "my_finish": my_finish, "my_score": my_score}


def list_contests(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    if status:
        return conn.execute(
            "SELECT c.*, s.date AS slate_date FROM contests c"
            " LEFT JOIN slates s ON s.slate_id=c.slate_id"
            " WHERE c.status=? ORDER BY c.contest_id DESC",
            (status,),
        ).fetchall()
    return conn.execute(
        "SELECT c.*, s.date AS slate_date FROM contests c"
        " LEFT JOIN slates s ON s.slate_id=c.slate_id ORDER BY c.contest_id DESC"
    ).fetchall()
