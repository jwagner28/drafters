"""Projections page.

Upload a batter-props CSV, assign positions for unknown players, enter pitcher
projections, then view / sort / filter / export and save the slate to the DB.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dfs import config, fanduel, registry, sgo
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
# 1. Get props — pull live odds (SportsGameOdds) or upload a CSV
# ---------------------------------------------------------------------------
st.header("1. Get props")

st.subheader("Pull live odds")
st.caption("Batter props from **FanDuel** (full slate coverage, real over-the-line "
           "ladders); pitcher projections from **SportsGameOdds**. Today's MLB games "
           "(US/Eastern), **pre-match only**.")
if st.button("☁️ Pull today's MLB odds", type="primary"):
    with st.spinner("Fetching FanDuel batter props + SGO pitcher projections…"):
        try:
            fd = fanduel.pull_batters()
            ss["props_df"] = fd["batter_df"]
            teams = dict(fd["player_teams"])
            n_bat = fd["batter_df"]["player"].nunique() if not fd["batter_df"].empty else 0

            # Pitcher projections from SGO (best-effort — batters don't depend on it).
            pitchers: list[dict] = []
            pitch_note = ""
            if sgo.configured():
                try:
                    slate = sgo.pull_slate()
                    pitchers = [{"name": p["name"], "proj_pts": p["proj_pts"]}
                                for p in slate["pitchers"]]
                    for nm, tm in slate["player_teams"].items():
                        if tm and nm not in teams:  # add pitcher teams
                            teams[nm] = tm
                except Exception as e:  # noqa: BLE001
                    pitch_note = f"  ⚠️ pitcher pull failed: {e}"
            else:
                pitch_note = "  (set SGO_API_KEY to auto-project pitchers)"
            ss["pitchers"] = pitchers

            # Record each player's team (fixes empty Team columns elsewhere).
            for nm, tm in teams.items():
                if tm:
                    registry.upsert_player(conn, nm, team=tm)

            st.success(f"Pulled {fd['n_games']} games · {n_bat} batters (FanDuel) · "
                       f"{len(pitchers)} pitchers (SGO).{pitch_note}")
        except Exception as e:  # noqa: BLE001
            st.error("Odds pull failed — full error below.")
            st.exception(e)

with st.expander("…or upload a props CSV / use sample data"):
    st.caption(
        "Columns: " + ", ".join(
            ["player", "normalized_market_key", "point", "over_prob",
             "game", "commence_time_local", "away_team", "home_team"]
        )
    )
    uploaded = st.file_uploader("Props CSV", type=["csv"])
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
    projections = compute_projections(props_df, uplift=True)
    ss["projections"] = projections
    names = [p.player for p in projections]

    # -----------------------------------------------------------------------
    # 2. Resolve unknown positions
    # -----------------------------------------------------------------------
    st.header("2. Assign / edit positions")
    # Ensure registry rows exist so we have stable ids; then find ones missing a position.
    slate_mod.ensure_players(conn, names)
    pending = slate_mod.players_needing_position(conn, names)

    if pending:
        st.warning(
            f"{len(pending)} player(s) still need a position. Tick **IF / OF / P** below "
            "(a flex bat is IF+OF; Ohtani is IF+P), then **Save**. Stored forever."
        )
    else:
        st.success("All players have positions. ✅ You can still edit any of them below.")
    st.caption("Edit here anytime — if you mis-assign someone or a player changes position, "
               "just retick and save.")

    only_unassigned = st.checkbox("Show only players needing a position", value=bool(pending))
    grid_names = pending if (only_unassigned and pending) else names

    def _pos_row(name: str) -> dict:
        row = registry.get_player_by_name(conn, name)
        pos = registry.get_positions(row) if row else []
        return {"Player": name, "IF": "IF" in pos, "OF": "OF" in pos, "P": "P" in pos}

    pos_df = pd.DataFrame([_pos_row(n) for n in grid_names])
    edited_pos = st.data_editor(
        pos_df,
        column_config={
            "Player": st.column_config.TextColumn("Player", disabled=True),
            "IF": st.column_config.CheckboxColumn("IF"),
            "OF": st.column_config.CheckboxColumn("OF"),
            "P": st.column_config.CheckboxColumn("P"),
        },
        hide_index=True,
        use_container_width=True,
        height=360,
        key=f"pos_grid_{only_unassigned}",
    )
    if st.button("💾 Save positions"):
        changed = 0
        for r in edited_pos.to_dict("records"):
            groups = [g for g in ("IF", "OF", "P") if r[g]]
            existing = registry.get_player_by_name(conn, r["Player"])
            current = registry.get_positions(existing) if existing else []
            if set(groups) != set(current):
                registry.assign_positions(conn, r["Player"], groups)
                changed += 1
        st.success(f"Updated positions for {changed} player(s).")
        st.rerun()

    # -----------------------------------------------------------------------
    # 3. Pitcher projections (manual)
    # -----------------------------------------------------------------------
    st.header("3. Pitcher projections")
    st.caption("Auto-filled from SportsGameOdds when you pull odds; you can also add/edit by hand. "
               "Stored per slate when you save.")
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
