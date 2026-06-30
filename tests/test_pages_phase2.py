"""Headless render tests (Streamlit AppTest) for the Phase 2 pages."""

import os
from pathlib import Path

import pandas as pd
import pytest

from dfs import contest as contest_mod, draft, registry
from dfs import slate as slate_mod
from dfs.db import connect
from dfs.projections import compute_projections

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CSV = ROOT / "sample_data" / "sample_props.csv"
NEW_CONTEST = ROOT / "pages" / "3_New_Contest.py"
ACTIVE = ROOT / "pages" / "4_Active_Contests.py"

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def _clear_streamlit_caches():
    # @st.cache_resource caches the DB connection process-globally across AppTest
    # runs; clear it so each page reconnects to the current DFS_DB_PATH.
    import streamlit as st
    try:
        st.cache_resource.clear()
        st.cache_data.clear()
    except Exception:
        pass


def _seed(tmp_path):
    """Create a temp DB (pointed at by DFS_DB_PATH) with a slate + a contest."""
    db = tmp_path / "pages.db"
    os.environ["DFS_DB_PATH"] = str(db)
    _clear_streamlit_caches()
    conn = connect(db)
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
        {"drafter_name": "Rival", "is_me": 0, "draft_slot": 2, "picks": [cell(1, 2, "Gerrit Cole", "P")]},
    ]
    contest_id = contest_mod.save_contest(conn, slate_id, entries, site="Underdog", my_draft_slot=1)
    conn.close()
    return contest_id


def test_new_contest_page_renders_and_shows_board(tmp_path):
    contest_id = _seed(tmp_path)
    at = AppTest.from_file(str(NEW_CONTEST), default_timeout=60)
    # Seed a minimal grid so the page doesn't stop, and a saved contest to render.
    at.session_state["nc_grid"] = [
        {"Overall": 1, "Rd": 1, "Seat": 1, "Drafter": "Me", "Player": "Aaron Judge", "Team": "NYY", "Slot": "OF"},
    ]
    at.session_state["nc_saved"] = contest_id
    at.run()
    assert not at.exception


def test_active_contests_page_renders_board(tmp_path):
    _seed(tmp_path)
    at = AppTest.from_file(str(ACTIVE), default_timeout=60).run()
    assert not at.exception
    # The leaderboard + boards should produce at least one dataframe element.
    assert len(at.dataframe) >= 1
