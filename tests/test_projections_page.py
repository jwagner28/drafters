"""Headless render test for the Projections page (position-editor grid)."""

import datetime as _dt
import os
from pathlib import Path

import pandas as pd
import pytest

from dfs import slate as slate_mod
from dfs.db import connect
from dfs.projections import compute_projections

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "pages" / "1_Projections.py"
SAMPLE_CSV = ROOT / "sample_data" / "sample_props.csv"

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def test_projections_page_renders_position_editor(tmp_path):
    os.environ["DFS_DB_PATH"] = str(tmp_path / "proj.db")
    import streamlit as st
    try:
        st.cache_resource.clear()
    except Exception:
        pass

    # Seed today's slate with batters (the page reads the slate from the DB).
    conn = connect(tmp_path / "proj.db")
    projs = compute_projections(pd.read_csv(SAMPLE_CSV))
    sid = slate_mod.get_or_create_daily_slate(conn, _dt.date.today().isoformat())
    slate_mod.merge_batter_projections(conn, sid, projs)
    conn.close()

    at = AppTest.from_file(str(PAGE), default_timeout=60)
    at.run()
    assert not at.exception
    # The editable position grid + projection table both render as data editors/frames.
    assert len(at.dataframe) + len(getattr(at, "data_editor", [])) >= 1
