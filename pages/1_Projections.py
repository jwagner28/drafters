"""Projections page — one slate per day.

Pick a date, then update **batter props** (FanDuel) and/or **pitcher props**
(SportsGameOdds) into that day's single slate. Updates MERGE: a player whose
game already started (and dropped out of the odds feed) keeps their last
projection instead of disappearing. Assign positions and view the table below.
"""

from __future__ import annotations

import datetime as _dt
import json

import pandas as pd
import streamlit as st

from dfs import config, fanduel, registry, sgo
from dfs import slate as slate_mod
from dfs.db import connect
from dfs.projections import REQUIRED_COLUMNS, compute_projections

st.set_page_config(page_title="Projections", page_icon="📊", layout="wide")
st.title("📊 Projections")


@st.cache_resource
def get_conn():
    return connect()


conn = get_conn()
ss = st.session_state

# ---------------------------------------------------------------------------
# 1. Day → the single slate for that date
# ---------------------------------------------------------------------------
st.header("1. Day")
slate_date = st.date_input("Slate date", value=_dt.date.today())
slate_id = slate_mod.get_daily_slate(conn, str(slate_date))  # read-only; created on first update
if slate_id is not None:
    counts = slate_mod.slate_counts(conn, slate_id)
    st.caption(f"Slate **#{slate_id}** for **{slate_date}** — "
               f"{counts['batters']} batters · {counts['pitchers']} pitchers stored. "
               "There is one slate per day; updates below merge into it.")
else:
    st.caption(f"No slate for **{slate_date}** yet — an update below creates it.")

# ---------------------------------------------------------------------------
# 2. Update props — two independent buttons
# ---------------------------------------------------------------------------
st.header("2. Update props")
st.caption("Each update **merges** into today's slate — a player who lost props "
           "(their game started) keeps their last projection. Pitcher props use "
           "the SportsGameOdds API, so update those sparingly.")
bc = st.columns(2)

if bc[0].button("🏏 Update batter props (FanDuel)", type="primary", use_container_width=True):
    with st.spinner("Fetching FanDuel batter props…"):
        try:
            fd = fanduel.pull_batters()
            if fd["batter_df"].empty:
                st.warning("FanDuel returned no pre-match batter props right now "
                           "(no upcoming games today, or props not posted yet).")
            else:
                projs = compute_projections(fd["batter_df"], uplift=True)
                sid = slate_mod.get_or_create_daily_slate(conn, str(slate_date))
                slate_mod.merge_batter_projections(conn, sid, projs)
                for nm, tm in fd["player_teams"].items():
                    if tm:
                        registry.upsert_player(conn, nm, team=tm)
                st.success(f"Updated **{len(projs)}** batters from {fd['n_games']} games.")
                st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error("Batter update failed — full error below.")
            st.exception(e)

if bc[1].button("⚾ Update pitcher props (SGO)", use_container_width=True):
    if not sgo.configured():
        st.error("Set **SGO_API_KEY** (Streamlit secrets or env var) to pull pitcher props.")
    else:
        with st.spinner("Fetching SGO pitcher projections…"):
            try:
                slate = sgo.pull_slate()
                pitchers = slate["pitchers"]
                sid = slate_mod.get_or_create_daily_slate(conn, str(slate_date))
                for p in pitchers:
                    slate_mod.save_pitcher_projection(
                        conn, sid, p["name"], p["proj_pts"], p.get("team"))
                for nm, tm in slate["player_teams"].items():
                    if tm:
                        registry.upsert_player(conn, nm, team=tm)
                st.success(f"Updated **{len(pitchers)}** pitchers.")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error("Pitcher update failed — full error below.")
                st.exception(e)

with st.expander("…or upload a batter-props CSV (also merges into today's slate)"):
    st.caption("Columns: " + ", ".join(REQUIRED_COLUMNS))
    uploaded = st.file_uploader("Props CSV", type=["csv"], key="proj_csv")
    if uploaded is not None and st.button("Merge CSV into slate"):
        df = pd.read_csv(uploaded)
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            st.error(f"CSV is missing required columns: {missing}")
        else:
            projs = compute_projections(df, uplift=True)
            sid = slate_mod.get_or_create_daily_slate(conn, str(slate_date))
            slate_mod.merge_batter_projections(conn, sid, projs)
            st.success(f"Merged {len(projs)} batters from CSV.")
            st.rerun()

# ---------------------------------------------------------------------------
# Load the slate's stored batters (source of truth from here down)
# ---------------------------------------------------------------------------
rows = slate_mod.load_slate_projections(conn, slate_id) if slate_id is not None else []
if not rows:
    st.info("No batters in this slate yet. Click **Update batter props** above.")
    st.stop()

names = [r["full_name"] for r in rows]

# ---------------------------------------------------------------------------
# 3. Assign / edit positions
# ---------------------------------------------------------------------------
st.header("3. Assign / edit positions")
pending = [r["full_name"] for r in rows if not registry.get_positions(r)]
if pending:
    st.warning(f"{len(pending)} player(s) need a position. Tick **IF / OF / P** "
               "(flex bat = IF+OF; Ohtani = IF+P), then **Save**.")
else:
    st.success("All players have positions. ✅ You can still edit any below.")

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

# ---------------------------------------------------------------------------
# 4. Pitchers (auto from SGO; add/edit by hand)
# ---------------------------------------------------------------------------
st.header("4. Pitchers")
prows = conn.execute(
    "SELECT pp.player_id, pl.full_name, pl.team, pp.proj_pts FROM pitcher_projections pp"
    " JOIN players pl ON pl.player_id = pp.player_id WHERE pp.slate_id=?"
    " ORDER BY pp.proj_pts DESC",
    (slate_id,),
).fetchall()
if prows:
    st.dataframe(
        pd.DataFrame([{"Pitcher": r["full_name"], "Team": r["team"] or "",
                       "Proj": round(r["proj_pts"], 2)} for r in prows]),
        hide_index=True, use_container_width=True, height=240,
    )
else:
    st.caption("No pitchers yet — click **Update pitcher props**, or add one by hand.")

with st.form("add_pitcher", clear_on_submit=True):
    pc = st.columns([3, 1, 1])
    p_name = pc[0].text_input("Pitcher name")
    p_pts = pc[1].number_input("Proj pts", min_value=0.0, value=0.0, step=0.5)
    if pc[2].form_submit_button("Add / update") and p_name.strip():
        slate_mod.save_pitcher_projection(conn, slate_id, p_name.strip(), float(p_pts))
        st.success(f"Saved pitcher {p_name.strip()}.")
        st.rerun()

# ---------------------------------------------------------------------------
# 5. Projection table (view / sort / filter / export)
# ---------------------------------------------------------------------------
st.header("5. Projection table")
table = pd.DataFrame([{
    "player": r["full_name"],
    "positions": "/".join(registry.get_positions(r)),
    "team": r["team"] or "",
    "proj_pts": round(r["proj_pts"], 2),
    "R": r["e_r"], "1B": r["e_1b"], "2B": r["e_2b"], "3B": r["e_3b"],
    "HR": r["e_hr"], "RBI": r["e_rbi"], "SB": r["e_sb"], "BB": r["e_bb"],
    "game": r["game"] or "",
    "flags": ", ".join(json.loads(r["flags_json"] or "[]")),
} for r in rows])

fc = st.columns([2, 2, 2])
group_filter = fc[0].multiselect("Eligible at", config.ASSIGNABLE_POSITIONS, default=[])
flag_filter = fc[1].multiselect("Flags", ["in_progress", "inflated"], default=[])
min_proj = fc[2].number_input("Min projection", min_value=0.0, value=0.0, step=0.5)

view = table.copy()
if group_filter:
    view = view[view["positions"].apply(lambda s: any(g in s.split("/") for g in group_filter))]
if flag_filter:
    view = view[view["flags"].apply(lambda s: any(f in s for f in flag_filter))]
view = view[view["proj_pts"] >= min_proj]

# Flag warnings by game.
flagged: dict[str, set[str]] = {}
for r in rows:
    fl = json.loads(r["flags_json"] or "[]")
    if fl and r["game"]:
        flagged.setdefault(r["game"], set()).update(fl)
for game, kinds in flagged.items():
    st.warning(f"Game **{game}** flagged: {', '.join(sorted(kinds))}")

st.dataframe(view.sort_values("proj_pts", ascending=False), use_container_width=True, height=500)
st.download_button(
    "⬇️ Export projections CSV",
    data=table.to_csv(index=False).encode("utf-8"),
    file_name=f"projections_{slate_date}.csv",
    mime="text/csv",
)
