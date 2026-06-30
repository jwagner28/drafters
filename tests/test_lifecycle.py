"""Tests for Phase 3: substitutions, settling, opponents, and stats."""

from pathlib import Path

import pandas as pd
import pytest

from dfs import contest, draft, opponents, registry, stats
from dfs import slate as slate_mod
from dfs.db import connect
from dfs.projections import compute_projections

SAMPLE_CSV = Path(__file__).resolve().parents[1] / "sample_data" / "sample_props.csv"


def _build(tmp_path):
    conn = connect(tmp_path / "life.db")
    projs = compute_projections(pd.read_csv(SAMPLE_CSV))
    slate_id = slate_mod.create_slate(conn, "2026-06-29")
    slate_mod.save_batter_projections(conn, slate_id, projs)
    slate_mod.save_pitcher_projection(conn, slate_id, "Gerrit Cole", 18.5)

    def pid(n):
        return registry.get_player_by_name(conn, n)["player_id"]

    grid = {(c.round_number, c.seat): c for c in draft.generate_grid(2, 1)}

    def cell(r, s, name, slot):
        c = grid[(r, s)]
        return {"overall_pick_number": c.overall_pick_number, "round_number": c.round_number,
                "slot_in_round": c.slot_in_round, "player_id": pid(name), "roster_slot": slot}

    entries = [
        {"drafter_name": "Me", "is_me": 1, "draft_slot": 1, "picks": [cell(1, 1, "Aaron Judge", "OF")]},
        {"drafter_name": "Rival", "is_me": 0, "draft_slot": 2, "picks": [cell(1, 2, "Freddie Freeman", "OF")]},
    ]
    contest_id = contest.save_contest(conn, slate_id, entries, site="Underdog", my_draft_slot=1, buy_in=5.0)
    return conn, slate_id, contest_id


def test_substitution_updates_total_and_logs(tmp_path):
    conn, slate_id, contest_id = _build(tmp_path)
    data = contest.load_contest(conn, contest_id)
    me = next(e for e in data["entries"] if e["is_me"])
    pick = me["picks"][0]
    assert pick["full_name"] == "Aaron Judge"
    before = me["projected_total"]

    new_pid = registry.get_player_by_name(conn, "Bobby Witt Jr.")["player_id"]
    pick_id = conn.execute(
        "SELECT pick_id FROM draft_picks WHERE entry_id=?", (me["entry_id"],)
    ).fetchone()["pick_id"]
    delta = contest.substitute_player(
        conn, contest_id, me["entry_id"], pick_id, new_pid, reason="late scratch"
    )

    data2 = contest.load_contest(conn, contest_id)
    me2 = next(e for e in data2["entries"] if e["is_me"])
    assert me2["picks"][0]["full_name"] == "Bobby Witt Jr."
    assert me2["projected_total"] == pytest.approx(round(before + delta, 2))
    sub = conn.execute("SELECT * FROM substitutions WHERE contest_id=?", (contest_id,)).fetchone()
    assert sub["reason"] == "late scratch"
    assert sub["delta"] == pytest.approx(delta)


def test_settle_sets_finishes_result_and_opponents(tmp_path):
    conn, slate_id, contest_id = _build(tmp_path)
    data = contest.load_contest(conn, contest_id)
    me = next(e for e in data["entries"] if e["is_me"])
    rival = next(e for e in data["entries"] if not e["is_me"])

    # I win: 95 vs 80.
    res = contest.settle_contest(conn, contest_id, {me["entry_id"]: 95.0, rival["entry_id"]: 80.0})
    assert res["result"] == "win"
    assert res["my_finish"] == 1

    c = conn.execute("SELECT * FROM contests WHERE contest_id=?", (contest_id,)).fetchone()
    assert c["status"] == "completed"
    assert c["my_actual_score"] == pytest.approx(95.0)

    opp = conn.execute("SELECT * FROM opponents WHERE name='Rival'").fetchone()
    assert opp["h2h_wins"] == 1 and opp["h2h_losses"] == 0  # from my perspective
    assert opp["contests_played"] == 1
    assert opp["avg_actual_score"] == pytest.approx(80.0)


def test_resettle_is_idempotent_for_opponents(tmp_path):
    conn, slate_id, contest_id = _build(tmp_path)
    data = contest.load_contest(conn, contest_id)
    me = next(e for e in data["entries"] if e["is_me"])
    rival = next(e for e in data["entries"] if not e["is_me"])
    contest.settle_contest(conn, contest_id, {me["entry_id"]: 95.0, rival["entry_id"]: 80.0})
    # Re-settle with me losing this time.
    contest.settle_contest(conn, contest_id, {me["entry_id"]: 70.0, rival["entry_id"]: 80.0})
    opp = conn.execute("SELECT * FROM opponents WHERE name='Rival'").fetchone()
    assert opp["contests_played"] == 1  # not double-counted
    assert opp["h2h_wins"] == 0 and opp["h2h_losses"] == 1


def test_my_stats_and_records(tmp_path):
    conn, slate_id, contest_id = _build(tmp_path)
    data = contest.load_contest(conn, contest_id)
    me = next(e for e in data["entries"] if e["is_me"])
    rival = next(e for e in data["entries"] if not e["is_me"])
    contest.settle_contest(conn, contest_id, {me["entry_id"]: 95.0, rival["entry_id"]: 80.0},
                           payout=10.0)
    s = stats.my_stats(conn)
    assert s["n"] == 1 and s["wins"] == 1
    assert s["win_rate"] == pytest.approx(1.0)
    assert s["roi"] == pytest.approx((10.0 - 5.0) / 5.0)
    assert s["streak"] == 1 and s["streak_type"] == "win"

    by_site = stats.records_by_site(conn)
    assert any(r["key"] == "Underdog" and r["wins"] == 1 for r in by_site)


def test_calibration_series(tmp_path):
    conn, slate_id, contest_id = _build(tmp_path)
    data = contest.load_contest(conn, contest_id)
    me = next(e for e in data["entries"] if e["is_me"])
    rival = next(e for e in data["entries"] if not e["is_me"])
    proj = me["projected_total"]
    contest.settle_contest(conn, contest_id, {me["entry_id"]: proj + 12.0, rival["entry_id"]: 50.0})
    series = stats.calibration_series(conn, is_me=True)
    assert len(series) == 1
    assert series[0]["delta"] == pytest.approx(12.0)  # ran cold (under-projected)


def test_history_filters(tmp_path):
    conn, slate_id, contest_id = _build(tmp_path)
    data = contest.load_contest(conn, contest_id)
    me = next(e for e in data["entries"] if e["is_me"])
    rival = next(e for e in data["entries"] if not e["is_me"])
    contest.settle_contest(conn, contest_id, {me["entry_id"]: 95.0, rival["entry_id"]: 80.0})
    assert len(stats.history(conn, site="Underdog")) == 1
    assert len(stats.history(conn, site="DoesNotExist")) == 0
    assert len(stats.history(conn, opponent="Rival")) == 1
    assert len(stats.history(conn, slot=1)) == 1
