"""Headless render test for the Projections page (position-editor grid)."""

import os
from pathlib import Path

import pandas as pd
import pytest

from dfs.db import connect

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
    connect(tmp_path / "proj.db").close()

    at = AppTest.from_file(str(PAGE), default_timeout=60)
    at.session_state["props_df"] = pd.read_csv(SAMPLE_CSV)
    at.run()
    assert not at.exception
    # The editable position grid + projection table both render as data editors/frames.
    assert len(at.dataframe) + len(getattr(at, "data_editor", [])) >= 1
