"""Streamlit entrypoint (Home).

Run with:  streamlit run app.py

Pages live in ./pages and appear in the sidebar. Phase 1 ships the Projections
page; later phases add New Contest, Active, History, Opponents, Draft Assistant.
"""

from __future__ import annotations

import streamlit as st

from dfs.db import effective_db_path

st.set_page_config(page_title="MLB DFS Engine", page_icon="⚾", layout="wide")

st.title("⚾ MLB DFS Projection & Contest Engine")
st.caption("Local-first · 100% free · all data in one SQLite file")

st.markdown(
    """
Welcome. This app turns sportsbook odds into player projections, scores your
draft contests, tracks results, and (later) learns your opponents.

**Phase 1 is live:** open **Projections** in the sidebar to:

1. Upload a batter-props CSV
2. Assign positions for any new players (stored forever)
3. Enter pitcher projections manually
4. View / sort / filter the full projection table and export it

Later phases (screenshot ingestion, contest scoring, history & stats, opponent
modeling, the draft simulator) will add the remaining sidebar pages.
"""
)

st.divider()
st.subheader("Database")
st.write(f"SQLite file: `{effective_db_path()}`")
st.write("Back up the app by copying that single file. "
         "Set the `DFS_DB_PATH` environment variable to relocate it.")
