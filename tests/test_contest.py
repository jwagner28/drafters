"""Tests for contest scoring + persistence."""

from pathlib import Path

import pandas as pd
import pytest

from dfs import contest, draft, registry
from dfs import slate as slate_mod
from dfs.db import connect
from dfs.projections import compute_projections

SAMPLE_CSV = Path(__file__).resolve().parents[1] / "sample_data" / "sample_props.csv"


@pytest.fixture()
def scored(tmp_path):
    conn = connect(tmp_path / "contest.db")
    projs = compute_projections(pd.read_csv(SAMPLE_CSV))
    slate_id = slate_mod.create_slate(conn, "2026-06-29")
    slate_mod.save_batter_projections(conn, slate_id, projs)
    # A real pitcher projection for Cole; deliberately NONE for Ohtani.
    slate_mod.save_pitcher_projection(conn, slate_id, "Gerrit Cole", 18.5)
    # A drafted player who isn't in today's props at all (DNP).
    registry.upsert_player(conn, "Nobody Drafted", "IF")

    def pid(name):
        return registry.get_player_by_name(conn, name)["player_id"]

    # 2 drafters, 2 rounds -> overall order: (1,1),(1,2),(2,2),(2,1)
    grid = {(c.round_number, c.seat): c for c in draft.generate_grid(2, 2)}

    def cell(r, s, name, slot):
        c = grid[(r, s)]
        return {
            "overall_pick_number": c.overall_pick_number,
            "round_number": c.round_number,
            "slot_in_round": c.slot_in_round,
            "player_id": pid(name),
            "roster_slot": slot,
        }

    entries = [
        {  # me, seat 1
            "drafter_name": "Me", "is_me": 1, "draft_slot": 1,
            "picks": [
                cell(1, 1, "Aaron Judge", "OF"),       # batter 7.21
                cell(2, 1, "Shohei Ohtani", "P"),      # P slot, no pitcher proj -> batter 7.86
            ],
        },
        {  # opponent, seat 2
            "drafter_name": "Rival", "is_me": 0, "draft_slot": 2,
            "picks": [
                cell(1, 2, "Gerrit Cole", "P"),        # pitcher 18.5
                cell(2, 2, "Nobody Drafted", "IF"),    # DNP -> 0
            ],
        },
    ]
    contest_id = contest.save_contest(conn, slate_id, entries, site="Underdog", my_draft_slot=1)
    yield conn, contest_id
    conn.close()


def test_totals_and_ohtani_fallback(scored):
    conn, contest_id = scored
    data = contest.load_contest(conn, contest_id)
    by_name = {e["drafter_name"]: e for e in data["entries"]}

    me = by_name["Me"]
    assert me["projected_total"] == pytest.approx(7.21 + 7.86)
    ohtani = next(p for p in me["picks"] if p["full_name"] == "Shohei Ohtani")
    assert ohtani["source"] == "batter_fallback"   # Ohtani-type
    assert ohtani["player_projection"] == pytest.approx(7.86)


def test_dnp_scores_zero_and_is_flagged(scored):
    conn, contest_id = scored
    data = contest.load_contest(conn, contest_id)
    rival = next(e for e in data["entries"] if e["drafter_name"] == "Rival")
    dnp_pick = next(p for p in rival["picks"] if p["full_name"] == "Nobody Drafted")
    assert dnp_pick["source"] == "dnp"
    assert dnp_pick["player_projection"] == 0.0
    assert rival["summary"]["dnp"] == 1
    assert rival["projected_total"] == pytest.approx(18.5)


def test_leader_is_highest_projected(scored):
    conn, contest_id = scored
    data = contest.load_contest(conn, contest_id)
    leader = next(e for e in data["entries"] if e["entry_id"] == data["leader_entry_id"])
    assert leader["drafter_name"] == "Rival"  # 18.5 > 15.07


def test_overall_pick_numbers_stored(scored):
    conn, contest_id = scored
    rows = conn.execute(
        "SELECT overall_pick_number FROM draft_picks WHERE contest_id=? ORDER BY overall_pick_number",
        (contest_id,),
    ).fetchall()
    assert [r["overall_pick_number"] for r in rows] == [1, 2, 3, 4]


def test_pitching_hitting_split(scored):
    conn, contest_id = scored
    data = contest.load_contest(conn, contest_id)
    by_name = {e["drafter_name"]: e for e in data["entries"]}
    # Ohtani in a P slot but scored as a batter -> counts as hitting, not pitching.
    assert by_name["Me"]["summary"]["pitching"] == pytest.approx(0.0)
    assert by_name["Me"]["summary"]["hitting"] == pytest.approx(7.21 + 7.86)
    assert by_name["Rival"]["summary"]["pitching"] == pytest.approx(18.5)


def test_active_status_and_listing(scored):
    conn, contest_id = scored
    active = contest.list_contests(conn, status="active")
    assert any(c["contest_id"] == contest_id for c in active)


def test_resolve_uses_latest_same_date_slate(tmp_path):
    conn = connect(tmp_path / "samedate.db")
    early = slate_mod.create_slate(conn, "2026-07-02")   # incomplete early pull
    late = slate_mod.create_slate(conn, "2026-07-02")    # later, complete pull
    other = slate_mod.create_slate(conn, "2026-07-01")   # a different day

    pid = registry.upsert_player(conn, "Cal Raleigh", "IF")
    conn.execute("INSERT INTO batter_projections (slate_id, player_id, proj_pts, flags_json)"
                 " VALUES (?, ?, ?, '[]')", (late, pid, 7.5))
    conn.commit()
    # A contest on the EARLY same-date slate still finds him via the later pull.
    proj, src = contest.resolve_pick_projection(conn, early, pid, "IF")
    assert src == "batter" and proj == 7.5

    # A player who only exists on a DIFFERENT date is not borrowed -> DNP.
    old = registry.upsert_player(conn, "Yesterday Guy", "IF")
    conn.execute("INSERT INTO batter_projections (slate_id, player_id, proj_pts, flags_json)"
                 " VALUES (?, ?, ?, '[]')", (other, old, 4.0))
    conn.commit()
    proj2, src2 = contest.resolve_pick_projection(conn, early, old, "IF")
    assert src2 == "dnp" and proj2 == 0.0
    conn.close()
