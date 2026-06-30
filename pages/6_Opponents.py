"""Opponents page.

Per-opponent scouting reports (template, no LLM) and tendency charts built from
their draft history across all contests.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dfs import opponents as opponents_mod, scouting, tendencies
from dfs.db import connect

st.set_page_config(page_title="Opponents", page_icon="🕵️", layout="wide")
st.title("🕵️ Opponents")


@st.cache_resource
def get_conn():
    return connect()


conn = get_conn()

top = st.columns([3, 1])
top[0].caption("Scouting is built from every contest you've drafted against each opponent. "
               "Recompute after drafting or settling new contests.")
if top[1].button("🔄 Recompute tendencies"):
    n = tendencies.compute_all_tendencies(conn)
    st.success(f"Recomputed tendencies for {n} opponent(s).")

opps = opponents_mod.list_opponents(conn)
# Also include opponents that have draft history but no settled games yet.
all_names = [r["name"] for r in conn.execute("SELECT DISTINCT name FROM opponents ORDER BY name")]
if not all_names:
    st.info("No opponents yet. Save a contest with named drafters on the **New Contest** page, "
            "then click **Recompute tendencies**.")
    st.stop()

name = st.selectbox("Opponent", all_names)
t = tendencies.load_tendencies(conn, name)
opp_row = conn.execute("SELECT * FROM opponents WHERE name=?", (name,)).fetchone()

# H2H header.
hc = st.columns(4)
hc[0].metric("Your H2H", f"{opp_row['h2h_wins']}-{opp_row['h2h_losses']}")
hc[1].metric("Contests vs them", opp_row["contests_played"])
hc[2].metric("Their avg score", f"{opp_row['avg_actual_score']:.1f}" if opp_row["avg_actual_score"] else "—")
hc[3].metric("Drafts analyzed", t["n_contests"] if t else 0)

# ---------------------------------------------------------------------------
# Scouting report
# ---------------------------------------------------------------------------
st.subheader("Scouting report")
report = scouting.scouting_report(t)
if not report["has_data"]:
    st.info(report["summary"][0] + "  \nClick **Recompute tendencies** if you've drafted against them.")
    st.stop()
for line in report["summary"]:
    st.markdown(f"- {line}")

# ---------------------------------------------------------------------------
# Tendency charts
# ---------------------------------------------------------------------------
st.divider()
left, right = st.columns(2)

with left:
    st.markdown("**Positional profile by round**")
    buckets = t.get("round_buckets", {})
    if buckets:
        rows = []
        for label, b in buckets.items():
            rows.append({"Round": label, "P": b["P"], "IF": b["IF"], "OF": b["OF"], "HT": b["HT"]})
        prof = pd.DataFrame(rows).set_index("Round")
        st.bar_chart(prof)
    p = t.get("pitchers", {})
    st.caption(f"Pitchers per draft: **{p.get('per_draft', 0)}** · "
               f"first pitcher ≈ round **{p.get('first_pitcher_avg_round') or '—'}**")

with right:
    st.markdown("**Value discipline**")
    v = t.get("value")
    if v:
        vc = st.columns(2)
        vc[0].metric("Avg in-position rank", v["avg_pos_rank"],
                     help="1 = always takes the best available at the position")
        vc[1].metric("Avg overall rank", v["avg_overall_rank"])
        st.caption(f"In-position percentile ≈ {round(v['avg_pos_pct'] * 100)}% "
                   "(lower = more disciplined)")
    s = t.get("stacking")
    if s and s.get("coefficient") is not None:
        st.metric("Stacking coefficient", s["coefficient"],
                  help=">1 stacks teammates more than chance; <1 diversifies")

st.divider()
tc1, tc2 = st.columns(2)
with tc1:
    st.markdown("**Favorite players** (affinity)")
    aff = t.get("player_affinity", [])
    if aff:
        st.dataframe(pd.DataFrame([
            {"Player": a["name"], "Affinity": a["affinity"],
             "Drafted": a["drafted"], "Avail": a["available"]}
            for a in aff
        ]), hide_index=True, use_container_width=True)
    else:
        st.caption("No standout players yet.")
with tc2:
    st.markdown("**Team bias** (vs league)")
    teams = t.get("team_affinity", [])
    if teams:
        st.dataframe(pd.DataFrame([
            {"Team": x["team"], "Share": x["share"], "League": x["league_share"],
             "Ratio": x["ratio"] if x["ratio"] is not None else "—"}
            for x in teams
        ]), hide_index=True, use_container_width=True)
    else:
        st.caption("No team bias yet.")
