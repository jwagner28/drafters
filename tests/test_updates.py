"""Tests for the update/edit features:

- one slate per day + merge (no-delete) batter/pitcher updates
- contest projection refresh that never zeroes a started-game pick
- sticky manual projection overrides + player-name fixes
- opponent manual totals, dated history ranges, rename, add-new
"""

from pathlib import Path

import pandas as pd
import pytest

from dfs import contest, draft, opponents, registry
from dfs import slate as slate_mod
from dfs.db import connect
from dfs.projections import compute_projections

SAMPLE_CSV = Path(__file__).resolve().parents[1] / "sample_data" / "sample_props.csv"


# ---------------------------------------------------------------------------
# Slate: one per day + merge
# ---------------------------------------------------------------------------
def test_daily_slate_is_singular(tmp_path):
    conn = connect(tmp_path / "s.db")
    a = slate_mod.get_or_create_daily_slate(conn, "2026-07-05")
    b = slate_mod.get_or_create_daily_slate(conn, "2026-07-05")
    assert a == b
    c = slate_mod.get_or_create_daily_slate(conn, "2026-07-06")
    assert c != a
    conn.close()


def test_merge_batters_does_not_delete_missing(tmp_path):
    conn = connect(tmp_path / "s.db")
    projs = compute_projections(pd.read_csv(SAMPLE_CSV))
    sid = slate_mod.get_or_create_daily_slate(conn, "2026-07-05")
    slate_mod.merge_batter_projections(conn, sid, projs)
    before = slate_mod.slate_counts(conn, sid)["batters"]
    assert before > 1

    # A later pull that only contains ONE player must not drop the others.
    one = [p for p in projs if p.player == "Aaron Judge"]
    slate_mod.merge_batter_projections(conn, sid, one)
    after = slate_mod.slate_counts(conn, sid)["batters"]
    assert after == before  # nobody deleted
    conn.close()


# ---------------------------------------------------------------------------
# Contest: refresh keeps started-game picks, overrides are sticky
# ---------------------------------------------------------------------------
def _one_pick_contest(tmp_path):
    conn = connect(tmp_path / "c.db")
    projs = compute_projections(pd.read_csv(SAMPLE_CSV))
    sid = slate_mod.get_or_create_daily_slate(conn, "2026-07-05")
    slate_mod.merge_batter_projections(conn, sid, projs)
    pid = registry.get_player_by_name(conn, "Aaron Judge")["player_id"]
    grid = {(c.round_number, c.seat): c for c in draft.generate_grid(1, 1)}
    cell = grid[(1, 1)]
    entries = [{"drafter_name": "Me", "is_me": 1, "draft_slot": 1, "picks": [{
        "overall_pick_number": cell.overall_pick_number, "round_number": 1,
        "slot_in_round": 1, "player_id": pid, "roster_slot": "OF"}]}]
    cid = contest.save_contest(conn, sid, entries, my_draft_slot=1)
    return conn, sid, cid, pid


def test_refresh_keeps_started_game_pick(tmp_path):
    conn, sid, cid, pid = _one_pick_contest(tmp_path)
    judge_proj = contest.load_contest(conn, cid)["entries"][0]["picks"][0]["player_projection"]
    assert judge_proj > 0

    # Simulate the game starting: the player drops out of the odds feed.
    conn.execute("DELETE FROM batter_projections WHERE slate_id=? AND player_id=?", (sid, pid))
    conn.commit()

    res = contest.refresh_contest_projections(conn, cid)
    assert res["kept"] == 1 and res["updated"] == 0
    pick = contest.load_contest(conn, cid)["entries"][0]["picks"][0]
    assert pick["player_projection"] == judge_proj  # NOT zeroed
    conn.close()


def test_manual_override_is_sticky(tmp_path):
    conn, sid, cid, pid = _one_pick_contest(tmp_path)
    pick_id = contest.load_contest(conn, cid)["entries"][0]["picks"][0]["pick_id"]
    contest.set_pick_override(conn, pick_id, 12.34)

    data = contest.load_contest(conn, cid)
    p = data["entries"][0]["picks"][0]
    assert p["player_projection"] == 12.34 and p["overridden"] and p["source"] == "manual"
    assert data["entries"][0]["projected_total"] == pytest.approx(12.34)

    # A refresh must NOT clobber the override.
    contest.refresh_contest_projections(conn, cid)
    p2 = contest.load_contest(conn, cid)["entries"][0]["picks"][0]
    assert p2["player_projection"] == 12.34 and p2["overridden"]

    # Clearing re-resolves from the slate.
    contest.set_pick_override(conn, pick_id, None)
    contest.refresh_contest_projections(conn, cid)
    p3 = contest.load_contest(conn, cid)["entries"][0]["picks"][0]
    assert not p3["overridden"] and p3["player_projection"] > 0
    conn.close()


def test_set_pick_player_fixes_name(tmp_path):
    conn, sid, cid, pid = _one_pick_contest(tmp_path)
    pick_id = contest.load_contest(conn, cid)["entries"][0]["picks"][0]["pick_id"]
    betts = registry.get_player_by_name(conn, "Mookie Betts")["player_id"]
    contest.set_pick_player(conn, pick_id, betts)
    p = contest.load_contest(conn, cid)["entries"][0]["picks"][0]
    assert p["full_name"] == "Mookie Betts" and p["player_id"] == betts
    conn.close()


# ---------------------------------------------------------------------------
# Opponents: manual totals, ranges, rename, add-new
# ---------------------------------------------------------------------------
def test_opponent_totals_and_history(tmp_path):
    conn = connect(tmp_path / "o.db")
    opponents.add_opponent(conn, "Ghost", winnings=250.0, games=40)
    row = conn.execute("SELECT * FROM opponents WHERE name='Ghost'").fetchone()
    assert row["manual_winnings"] == 250.0 and row["manual_games"] == 40

    opponents.add_history_range(conn, "Ghost", "2026-01-01", "2026-03-31", 5, 3, 120.0, "Q1")
    opponents.add_history_range(conn, "Ghost", "2026-04-01", "2026-06-30", 2, 6, -40.0, "Q2")
    agg = opponents.aggregate_history(conn, "Ghost")
    assert agg == {"wins": 7, "losses": 9, "winnings": 80.0, "ranges": 2}

    ranges = opponents.list_history_ranges(conn, "Ghost")
    assert [r["note"] for r in ranges] == ["Q2", "Q1"]  # newest first

    opponents.delete_history_range(conn, ranges[0]["history_id"])
    assert opponents.aggregate_history(conn, "Ghost")["ranges"] == 1

    # Manual-only opponent shows up in the list.
    assert any(r["name"] == "Ghost" for r in opponents.list_opponents(conn))
    conn.close()


def test_rename_opponent_moves_history(tmp_path):
    conn = connect(tmp_path / "o.db")
    opponents.add_opponent(conn, "Typo Name", winnings=10.0, games=2)
    opponents.add_history_range(conn, "Typo Name", "2026-01-01", "2026-01-31", 1, 0, 5.0, None)
    opponents.rename_opponent(conn, "Typo Name", "Correct Name")
    assert conn.execute("SELECT 1 FROM opponents WHERE name='Typo Name'").fetchone() is None
    assert opponents.aggregate_history(conn, "Correct Name")["ranges"] == 1
    conn.close()
