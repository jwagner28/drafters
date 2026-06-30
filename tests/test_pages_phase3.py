"""Headless render tests (Streamlit AppTest) for the Phase 3 pages."""

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
ACTIVE = ROOT / "pages" / "4_Active_Contests.py"
HISTORY = ROOT / "pages" / "5_History_and_Stats.py"

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def _clear_caches():
    import streamlit as st
    try:
        st.cache_resource.clear()
        st.cache_data.clear()
    except Exception:
        pass


def _seed_settled(tmp_path):
    db = tmp_path / "p3.db"
    os.environ["DFS_DB_PATH"] = str(db)
    _clear_caches()
    conn = connect(db)
    projs = compute_projections(pd.read_csv(SAMPLE_CSV))
    slate_id = slate_mod.create_slate(conn, "2026-06-29")
    slate_mod.save_batter_projections(conn, slate_id, projs)

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
    contest_id = contest_mod.save_contest(conn, slate_id, entries, site="Underdog", my_draft_slot=1, buy_in=5.0)
    data = contest_mod.load_contest(conn, contest_id)
    actuals = {e["entry_id"]: (95.0 if e["is_me"] else 80.0) for e in data["entries"]}
    contest_mod.settle_contest(conn, contest_id, actuals, payout=10.0)
    conn.close()
    return contest_id


def test_active_page_with_settled_contest_renders(tmp_path):
    _seed_settled(tmp_path)
    at = AppTest.from_file(str(ACTIVE), default_timeout=60).run()
    # Settled contest is 'completed'; switch the filter to "all" to see it.
    at.radio[0].set_value("all").run()
    assert not at.exception
    assert len(at.dataframe) >= 1


def test_history_page_renders_stats_and_calibration(tmp_path):
    _seed_settled(tmp_path)
    at = AppTest.from_file(str(HISTORY), default_timeout=60).run()
    assert not at.exception
    # Metrics (record, win rate, …) and at least one table should render.
    assert len(at.metric) >= 1
    assert len(at.dataframe) >= 1
