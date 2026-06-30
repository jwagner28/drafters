"""Projections page.

Upload a batter-props CSV, assign positions for unknown players, enter pitcher
projections, then view / sort / filter / export and save the slate to the DB.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dfs import config, registry
from dfs.db import connect
from dfs.projections import (
    REQUIRED_COLUMNS,
    compute_projections,
    projections_to_dataframe,
)
from dfs import slate as slate_mod

st.set_page_config(page_title="Projections", page_icon="📊", layout="wide")
st.title("📊 Projections")


def props_warning_games(projections) -> dict[str, set[str]]:
    """Collect game -> set of flags for warning banners."""
    out: dict[str, set[str]] = {}
    for p in projections:
        if p.flags and p.game:
            out.setdefault(p.game, set()).update(p.flags)
    return out


@st.cache_resource
def get_conn():
    # One shared connection for the session. cache_resource keeps it alive.
    return connect()


conn = get_conn()
ss = st.session_state

# ---------------------------------------------------------------------------
# 1. Upload props CSV
# ---------------------------------------------------------------------------
st.header("1. Upload batter props")
st.caption(
    "Expected columns: " + ", ".join(
        ["player", "normalized_market_key", "point", "over_prob",
         "game", "commence_time_local", "away_team", "home_team"]
    )
)

uploaded = st.file_uploader("Props CSV", type=["csv"])
col_a, col_b = st.columns([1, 3])
with col_a:
    use_sample = st.button("Use sample data")

if use_sample:
    sample_path = "sample_data/sample_props.csv"
    try:
        ss["props_df"] = pd.read_csv(sample_path)
        st.success(f"Loaded sample: {sample_path}")
    except FileNotFoundError:
        st.error(f"Sample file not found at {sample_path}")

if uploaded is not None:
    ss["props_df"] = pd.read_csv(uploaded)

props_df = ss.get("props_df")

if props_df is not None:
    missing = [c for c in REQUIRED_COLUMNS if c not in props_df.columns]
    if missing:
        st.error(f"CSV is missing required columns: {missing}")
        st.stop()
    st.write(f"Loaded **{len(props_df)}** prop rows.")
    with st.expander("Preview raw props"):
        st.dataframe(props_df.head(50), use_container_width=True)

    # Compute projections (cached on the dataframe contents would be ideal, but
    # the math is cheap, so just recompute).
    projections = compute_projections(props_df)
    ss["projections"] = projections
    names = [p.player for p in projections]

    # -----------------------------------------------------------------------
    # 2. Resolve unknown positions
    # -----------------------------------------------------------------------
    st.header("2. Assign positions for new players")
    # Ensure registry rows exist so we have stable ids; then find ones missing a position.
    slate_mod.ensure_players(conn, names)
    pending = slate_mod.players_needing_position(conn, names)

    if not pending:
        st.success("All players already have positions in the registry. ✅")
    else:
        st.warning(
            f"{len(pending)} player(s) need a position. Pick **one or more** of "
            "IF / OF / P (a flex bat is IF+OF; Ohtani is IF+P). Stored forever."
        )
        with st.form("assign_positions"):
            assignments: dict[str, list[str]] = {}
            for name in pending:
                cols = st.columns([3, 3])
                cols[0].markdown(f"**{name}**")
                assignments[name] = cols[1].multiselect(
                    f"Positions for {name}",
                    options=config.ASSIGNABLE_POSITIONS,
                    key=f"pos_{name}",
                    label_visibility="collapsed",
                )
            submitted = st.form_submit_button("Save positions")
        if submitted:
            saved = 0
            for name, positions in assignments.items():
                if positions:
                    registry.assign_positions(conn, name, positions)
                    saved += 1
            st.success(f"Saved positions for {saved} player(s).")
            st.rerun()

    # -----------------------------------------------------------------------
    # 3. Pitcher projections (manual)
    # -----------------------------------------------------------------------
    st.header("3. Pitcher projections (manual)")
    st.caption("Pitchers are entered by hand — no formula. They're stored per slate when you save.")
    if "pitchers" not in ss:
        ss["pitchers"] = []
    with st.form("add_pitcher", clear_on_submit=True):
        pc = st.columns([3, 1, 1])
        p_name = pc[0].text_input("Pitcher name")
        p_pts = pc[1].number_input("Proj pts", min_value=0.0, value=0.0, step=0.5)
        add_p = pc[2].form_submit_button("Add")
    if add_p and p_name.strip():
        ss["pitchers"].append({"name": p_name.strip(), "proj_pts": float(p_pts)})
    if ss["pitchers"]:
        st.dataframe(pd.DataFrame(ss["pitchers"]), use_container_width=True)
        if st.button("Clear pitchers"):
            ss["pitchers"] = []
            st.rerun()

    # -----------------------------------------------------------------------
    # 4. Projection table (view / sort / filter / export)
    # -----------------------------------------------------------------------
    st.header("4. Projection table")
    table = projections_to_dataframe(projections)

    # Attach registry position eligibility (e.g. "IF/OF").
    def _positions(name: str) -> str:
        row = registry.get_player_by_name(conn, name)
        if row is None:
            return ""
        return "/".join(registry.get_positions(row))

    table.insert(1, "positions", table["player"].map(_positions))

    fc = st.columns([2, 2, 2])
    group_filter = fc[0].multiselect("Eligible at", config.ASSIGNABLE_POSITIONS, default=[])
    flag_filter = fc[1].multiselect("Flags", ["in_progress", "inflated"], default=[])
    min_proj = fc[2].number_input("Min projection", min_value=0.0, value=0.0, step=0.5)

    view = table.copy()
    if group_filter:
        view = view[view["positions"].apply(
            lambda s: any(g in s.split("/") for g in group_filter)
        )]
    if flag_filter:
        view = view[view["flags"].apply(lambda s: any(f in s for f in flag_filter))]
    view = view[view["proj_pts"] >= min_proj]

    # Surface flag warnings.
    flagged_games = props_warning_games(projections)
    for game, kinds in flagged_games.items():
        st.warning(f"Game **{game}** flagged: {', '.join(sorted(kinds))}")

    st.dataframe(
        view.sort_values("proj_pts", ascending=False),
        use_container_width=True,
        height=500,
    )

    st.download_button(
        "⬇️ Export projections CSV",
        data=table.to_csv(index=False).encode("utf-8"),
        file_name="projections.csv",
        mime="text/csv",
    )

    # -----------------------------------------------------------------------
    # 5. Save slate
    # -----------------------------------------------------------------------
    st.header("5. Save slate to database")
    sc = st.columns([2, 3])
    slate_date = sc[0].date_input("Slate date")
    slate_notes = sc[1].text_input("Notes (optional)")
    if st.button("💾 Save slate"):
        slate_id = slate_mod.create_slate(conn, str(slate_date), slate_notes or None)
        slate_mod.save_batter_projections(conn, slate_id, projections)
        for p in ss.get("pitchers", []):
            slate_mod.save_pitcher_projection(conn, slate_id, p["name"], p["proj_pts"])
        st.success(f"Saved slate #{slate_id} with {len(projections)} batters "
                   f"and {len(ss.get('pitchers', []))} pitchers.")
else:
    st.info("Upload a props CSV or click **Use sample data** to begin.")
