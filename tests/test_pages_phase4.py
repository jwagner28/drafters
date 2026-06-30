"""Headless render test (Streamlit AppTest) for the Opponents page."""

import os
from pathlib import Path

import pytest

from dfs import contest, draft, registry, tendencies
from dfs import slate as slate_mod
from dfs.db import connect

ROOT = Path(__file__).resolve().parents[1]
OPPONENTS = ROOT / "pages" / "6_Opponents.py"

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def _clear_caches():
    import streamlit as st
    try:
        st.cache_resource.clear()
        st.cache_data.clear()
    except Exception:
        pass


def _add_batter(conn, slate_id, name, team, group, proj):
    pid = registry.upsert_player(conn, name, positions=group, team=team)
    conn.execute("INSERT INTO batter_projections (slate_id, player_id, proj_pts, flags_json)"
                 " VALUES (?, ?, ?, '[]')", (slate_id, pid, proj))
    conn.commit()
    return pid


def _seed(tmp_path):
    db = tmp_path / "p4.db"
    os.environ["DFS_DB_PATH"] = str(db)
    _clear_caches()
    conn = connect(db)
    slate_id = slate_mod.create_slate(conn, "2026-06-29")
    p = {
        "LAD A": _add_batter(conn, slate_id, "LAD A", "LAD", "OF", 8.0),
        "LAD B": _add_batter(conn, slate_id, "LAD B", "LAD", "IF", 7.0),
        "NYY A": _add_batter(conn, slate_id, "NYY A", "NYY", "IF", 7.5),
        "BOS A": _add_batter(conn, slate_id, "BOS A", "BOS", "IF", 6.0),
    }
    grid = {(c.round_number, c.seat): c for c in draft.generate_grid(2, 2)}

    def cell(r, s, name, slot):
        c = grid[(r, s)]
        return {"overall_pick_number": c.overall_pick_number, "round_number": c.round_number,
                "slot_in_round": c.slot_in_round, "player_id": p[name], "roster_slot": slot}

    entries = [
        {"drafter_name": "Me", "is_me": 1, "draft_slot": 1,
         "picks": [cell(1, 1, "NYY A", "IF"), cell(2, 1, "BOS A", "IF")]},
        {"drafter_name": "Rival", "is_me": 0, "draft_slot": 2,
         "picks": [cell(1, 2, "LAD A", "OF"), cell(2, 2, "LAD B", "IF")]},
    ]
    contest.save_contest(conn, slate_id, entries, site="UD", my_draft_slot=1)
    tendencies.compute_all_tendencies(conn)
    conn.close()


def test_opponents_page_renders(tmp_path):
    _seed(tmp_path)
    at = AppTest.from_file(str(OPPONENTS), default_timeout=60).run()
    assert not at.exception
    # H2H metrics + a scouting report (markdown bullets) should be present.
    assert len(at.metric) >= 1
    assert any("Rival" in str(s.value) for s in at.selectbox)
