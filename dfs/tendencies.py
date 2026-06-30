"""Opponent tendency extraction.

For each opponent, from their draft picks across all contests, we reconstruct
the pool that was available at each of their picks (possible because every pick
stores its `overall_pick_number`) and compute:

* Positional profile by round bucket (share of P / IF / OF / HT).
* Pitcher timing (pitchers per draft, average round of the first pitcher).
* Value adherence (avg rank of the player taken among all available — and within
  position; 1 = best available).
* Player affinity (drafted / available, shrunk toward the league base rate).
* Team affinity (team draft share vs the league share).
* Stacking coefficient (P(take a same-team player | already has one) ÷ baseline).

Recent contests are weighted more (exponential time decay). Results are stored
as JSON on each opponent row and recomputed when a contest is settled.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

from . import opponents as opponents_mod

ROUND_BUCKETS = [
    ("R1-2", 1, 2),
    ("R3-5", 3, 5),
    ("R6-10", 6, 10),
    ("R11-16", 11, 16),
    ("R17+", 17, 10_000),
]
SLOT_CATS = ["P", "IF", "OF", "HT"]

DECAY_LAMBDA = math.log(2) / 30.0  # weight halves roughly every 30 days
SHRINK_K = 5.0                      # affinity shrinkage toward the base rate
MIN_AVAIL = 2                       # min availability before a player affinity counts


def _bucket(round_number: int) -> str:
    for label, lo, hi in ROUND_BUCKETS:
        if lo <= round_number <= hi:
            return label
    return ROUND_BUCKETS[-1][0]


def _slate_player_values(conn: sqlite3.Connection, slate_id: int) -> dict[int, dict]:
    """player_id -> {value, group, team, name, pitch_value?} for a slate.

    Hitters take their batter projection and registry IF/OF group; pure pitchers
    take their pitcher projection and group 'P'. A two-way player keeps a hitter
    primary plus a `pitch_value` used when he's drafted into a P slot.
    """
    vals: dict[int, dict] = {}
    for r in conn.execute(
        "SELECT bp.player_id, bp.proj_pts, pl.positions_json, pl.team, pl.full_name "
        "FROM batter_projections bp JOIN players pl ON pl.player_id=bp.player_id "
        "WHERE bp.slate_id=?",
        (slate_id,),
    ):
        groups = json.loads(r["positions_json"] or "[]")
        group = next((g for g in groups if g in ("IF", "OF")), "IF")
        vals[r["player_id"]] = {
            "value": float(r["proj_pts"]), "group": group,
            "team": r["team"], "name": r["full_name"],
        }
    for r in conn.execute(
        "SELECT pp.player_id, pp.proj_pts, pl.team, pl.full_name "
        "FROM pitcher_projections pp JOIN players pl ON pl.player_id=pp.player_id "
        "WHERE pp.slate_id=?",
        (slate_id,),
    ):
        pid = r["player_id"]
        if pid in vals:
            vals[pid]["pitch_value"] = float(r["proj_pts"])
        else:
            vals[pid] = {
                "value": float(r["proj_pts"]), "group": "P",
                "team": r["team"], "name": r["full_name"],
            }
    return vals


def _contest_weight(created_at: str | None) -> float:
    if not created_at:
        return 1.0
    try:
        when = datetime.fromisoformat(created_at)
    except ValueError:
        return 1.0
    days = max((datetime.now() - when).days, 0)
    return math.exp(-DECAY_LAMBDA * days)


def _walk_contest(conn: sqlite3.Connection, contest_id: int, slate_id: int, weight: float,
                  league: dict, opp_acc: dict) -> None:
    """Simulate one contest in pick order, updating league + per-opponent stats."""
    vals = _slate_player_values(conn, slate_id)
    picks = conn.execute(
        "SELECT dp.*, ce.drafter_name, ce.is_me FROM draft_picks dp "
        "JOIN contest_entries ce ON ce.entry_id=dp.entry_id "
        "WHERE dp.contest_id=? ORDER BY dp.overall_pick_number",
        (contest_id,),
    ).fetchall()

    drafted: set[int] = set()
    entry_teams: dict[int, Counter] = defaultdict(Counter)
    first_pitch_round: dict[str, int] = {}

    for p in picks:
        pid = p["player_id"]
        info = vals.get(pid)
        slot = p["roster_slot"] or ""
        # Value + group for the chosen player.
        if slot == "P" and info and info.get("pitch_value") is not None:
            pv, pg = info["pitch_value"], "P"
        elif info:
            pv, pg = info["value"], info["group"]
        else:
            pv, pg = None, None

        available = [(q, v) for q, v in vals.items() if q not in drafted]
        team = info["team"] if info else None
        eid = p["entry_id"]
        had_teams = {t for t, c in entry_teams[eid].items() if c > 0}
        avail_same_team = sum(1 for _q, v in available if v["team"] in had_teams) if had_teams else 0
        picked_same = bool(team and team in had_teams)

        # League base-rate accumulators (all drafters, incl. me).
        for q, _v in available:
            league["avail"][q] += weight
        if pid in vals:
            league["drafted"][pid] += weight
        if team:
            league["team_picks"][team] += weight
        league["total_picks"] += weight

        if not p["is_me"]:
            name = (p["drafter_name"] or "").strip()
            if name:
                acc = opp_acc.setdefault(name, _new_opp_acc())
                acc["contests"].add(contest_id)
                acc["n_picks"] += weight

                # Positional profile by round bucket (HT counts as its own cat).
                cat = slot if slot in SLOT_CATS else (pg if pg in SLOT_CATS else None)
                if cat:
                    b = acc["buckets"][_bucket(p["round_number"])]
                    b[cat] += weight
                    b["n"] += weight

                # Pitcher timing.
                if slot == "P":
                    acc["pitch_count"] += weight
                    fp = first_pitch_round.get(name)
                    if fp is None or p["round_number"] < fp:
                        first_pitch_round[name] = p["round_number"]

                # Value adherence.
                if pv is not None:
                    rank_overall = 1 + sum(1 for _q, v in available if v["value"] > pv)
                    same = [v for _q, v in available if v["group"] == pg]
                    rank_pos = 1 + sum(1 for v in same if v["value"] > pv)
                    acc["value_overall"] += weight * rank_overall
                    acc["value_overall_pct"] += weight * (rank_overall / max(len(available), 1))
                    acc["value_pos"] += weight * rank_pos
                    acc["value_pos_pct"] += weight * (rank_pos / max(len(same), 1))
                    acc["value_n"] += weight

                # Player / team affinity.
                for q, _v in available:
                    acc["player_avail"][q] += weight
                if pid in vals:
                    acc["player_drafted"][pid] += weight
                    acc["names"][pid] = vals[pid]["name"]
                if team:
                    acc["team_picks"][team] += weight

                # Stacking.
                if had_teams:
                    acc["stack_opps"] += weight
                    acc["stack_baseline"] += weight * (avail_same_team / max(len(available), 1))
                    if picked_same:
                        acc["stack_hits"] += weight

        drafted.add(pid)
        if team:
            entry_teams[eid][team] += 1

    # Record each opponent's first-pitcher round for this contest.
    for name, rnd in first_pitch_round.items():
        opp_acc[name]["first_pitch_rounds"].append((rnd, weight))


def _new_opp_acc() -> dict:
    return {
        "contests": set(),
        "n_picks": 0.0,
        "buckets": {label: {c: 0.0 for c in SLOT_CATS} | {"n": 0.0}
                    for label, _lo, _hi in ROUND_BUCKETS},
        "pitch_count": 0.0,
        "first_pitch_rounds": [],
        "value_overall": 0.0, "value_overall_pct": 0.0,
        "value_pos": 0.0, "value_pos_pct": 0.0, "value_n": 0.0,
        "player_avail": Counter(), "player_drafted": Counter(), "names": {},
        "team_picks": Counter(),
        "stack_opps": 0.0, "stack_baseline": 0.0, "stack_hits": 0.0,
    }


def _finalize(acc: dict, league: dict) -> dict:
    n_contests = len(acc["contests"])
    out: dict = {
        "n_contests": n_contests,
        "n_picks": round(acc["n_picks"], 2),
        "last_computed": datetime.now().isoformat(timespec="seconds"),
        "round_buckets": {},
    }
    for label, b in acc["buckets"].items():
        n = b["n"]
        if n > 0:
            out["round_buckets"][label] = {c: round(b[c] / n, 3) for c in SLOT_CATS} | {"n": round(n, 2)}

    fp = acc["first_pitch_rounds"]
    out["pitchers"] = {
        "per_draft": round(acc["pitch_count"] / n_contests, 2) if n_contests else 0.0,
        "first_pitcher_avg_round": (
            round(sum(r * w for r, w in fp) / sum(w for _r, w in fp), 1) if fp else None
        ),
    }

    if acc["value_n"] > 0:
        out["value"] = {
            "avg_overall_rank": round(acc["value_overall"] / acc["value_n"], 1),
            "avg_overall_pct": round(acc["value_overall_pct"] / acc["value_n"], 3),
            "avg_pos_rank": round(acc["value_pos"] / acc["value_n"], 1),
            "avg_pos_pct": round(acc["value_pos_pct"] / acc["value_n"], 3),
            "n": round(acc["value_n"], 2),
        }
    else:
        out["value"] = None

    # Player affinity, shrunk toward the league base rate.
    affinities = []
    for pid, avail in acc["player_avail"].items():
        if avail < MIN_AVAIL:
            continue
        drafted = acc["player_drafted"].get(pid, 0.0)
        base = (league["drafted"].get(pid, 0.0) / league["avail"][pid]) if league["avail"].get(pid) else 0.0
        affinity = (drafted + SHRINK_K * base) / (avail + SHRINK_K)
        affinities.append({
            "player_id": pid, "name": acc["names"].get(pid, str(pid)),
            "drafted": round(drafted, 2), "available": round(avail, 2),
            "affinity": round(affinity, 3), "base_rate": round(base, 3),
        })
    affinities.sort(key=lambda x: x["affinity"], reverse=True)
    out["player_affinity"] = affinities[:8]

    # Team affinity: share vs league share.
    teams = []
    total = acc["n_picks"]
    league_total = league["total_picks"]
    for team, cnt in acc["team_picks"].items():
        share = cnt / total if total else 0.0
        league_share = (league["team_picks"].get(team, 0.0) / league_total) if league_total else 0.0
        ratio = (share / league_share) if league_share > 0 else None
        teams.append({
            "team": team, "picks": round(cnt, 2), "share": round(share, 3),
            "league_share": round(league_share, 3),
            "ratio": round(ratio, 2) if ratio is not None else None,
        })
    teams.sort(key=lambda x: (x["ratio"] if x["ratio"] is not None else 0, x["picks"]), reverse=True)
    out["team_affinity"] = teams[:8]

    # Stacking coefficient.
    if acc["stack_opps"] > 0:
        observed = acc["stack_hits"] / acc["stack_opps"]
        baseline = acc["stack_baseline"] / acc["stack_opps"]
        out["stacking"] = {
            "coefficient": round(observed / baseline, 2) if baseline > 0 else None,
            "observed_rate": round(observed, 3),
            "baseline_rate": round(baseline, 3),
            "opportunities": round(acc["stack_opps"], 2),
        }
    else:
        out["stacking"] = None

    return out


def compute_all_tendencies(conn: sqlite3.Connection) -> int:
    """Recompute tendencies for every opponent from all contests with picks.

    Returns the number of opponents updated.
    """
    contests = conn.execute(
        "SELECT DISTINCT c.contest_id, c.slate_id, c.created_at FROM contests c "
        "JOIN draft_picks dp ON dp.contest_id=c.contest_id"
    ).fetchall()

    league = {"avail": Counter(), "drafted": Counter(), "team_picks": Counter(), "total_picks": 0.0}
    opp_acc: dict[str, dict] = {}
    for c in contests:
        if c["slate_id"] is None:
            continue
        weight = _contest_weight(c["created_at"])
        _walk_contest(conn, c["contest_id"], c["slate_id"], weight, league, opp_acc)

    updated = 0
    for name, acc in opp_acc.items():
        opp = opponents_mod.get_or_create_opponent(conn, name)
        tendencies = _finalize(acc, league)
        conn.execute(
            "UPDATE opponents SET tendencies_json=?, last_updated=? WHERE opponent_id=?",
            (json.dumps(tendencies), tendencies["last_computed"], opp["opponent_id"]),
        )
        updated += 1
    conn.commit()
    return updated


def load_tendencies(conn: sqlite3.Connection, name: str) -> dict | None:
    row = conn.execute("SELECT tendencies_json FROM opponents WHERE name=?", (name,)).fetchone()
    if row is None or not row["tendencies_json"]:
        return None
    data = json.loads(row["tendencies_json"])
    return data or None
