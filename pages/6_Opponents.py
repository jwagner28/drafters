"""Opponents page.

Manually-tracked winnings / games / dated match-history for each opponent, plus
scouting reports (template, no LLM) and tendency charts built from the contests
you've drafted against them.
"""

from __future__ import annotations

import datetime as _dt

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
top[0].caption("Track each opponent's totals + dated history by hand, and (once you've "
               "drafted against them) see scouting built from those contests.")
if top[1].button("🔄 Recompute tendencies"):
    n = tendencies.compute_all_tendencies(conn)
    st.success(f"Recomputed tendencies for {n} opponent(s).")

# ---------------------------------------------------------------------------
# Add a new opponent (even one you haven't drafted against)
# ---------------------------------------------------------------------------
with st.expander("➕ Add a new opponent"):
    with st.form("add_opp", clear_on_submit=True):
        ac = st.columns([3, 2, 2])
        new_name = ac[0].text_input("Name")
        new_win = ac[1].number_input("Total winnings ($)", value=0.0, step=1.0)
        new_games = ac[2].number_input("Total games", min_value=0, value=0, step=1)
        if st.form_submit_button("Add opponent") and new_name.strip():
            opponents_mod.add_opponent(conn, new_name.strip(),
                                       winnings=new_win, games=int(new_games))
            st.success(f"Added {new_name.strip()}.")
            st.rerun()

all_names = [r["name"] for r in conn.execute("SELECT DISTINCT name FROM opponents ORDER BY name")]
if not all_names:
    st.info("No opponents yet. Add one above, or save a contest with named drafters "
            "on the **New Contest** page.")
    st.stop()

name = st.selectbox("Opponent", all_names)
opp_row = conn.execute("SELECT * FROM opponents WHERE name=?", (name,)).fetchone()
agg = opponents_mod.aggregate_history(conn, name)

# ---------------------------------------------------------------------------
# Headline metrics: manual totals + aggregated dated history
# ---------------------------------------------------------------------------
hc = st.columns(4)
hc[0].metric("Total winnings", f"${opp_row['manual_winnings']:.0f}"
             if opp_row["manual_winnings"] is not None else "—")
hc[1].metric("Total games", opp_row["manual_games"]
             if opp_row["manual_games"] is not None else "—")
hc[2].metric("Record (from ranges)", f"{agg['wins']}-{agg['losses']}")
hc[3].metric("Winnings (from ranges)", f"${agg['winnings']:.0f}")

hc2 = st.columns(2)
hc2[0].metric("Your H2H (drafted)", f"{opp_row['h2h_wins']}-{opp_row['h2h_losses']}")
hc2[1].metric("Contests drafted vs them", opp_row["contests_played"])

# ---------------------------------------------------------------------------
# Manage: totals, rename, add a dated range
# ---------------------------------------------------------------------------
st.divider()
mc1, mc2 = st.columns(2)

with mc1:
    st.markdown("**Lifetime totals**")
    tw = st.number_input("Total winnings ($)", value=float(opp_row["manual_winnings"] or 0.0),
                         step=1.0, key=f"tot_win_{name}")
    tg = st.number_input("Total games", min_value=0, value=int(opp_row["manual_games"] or 0),
                        step=1, key=f"tot_games_{name}")
    if st.button("Save totals"):
        opponents_mod.set_opponent_totals(conn, name, winnings=tw, games=int(tg))
        st.success("Totals saved.")
        st.rerun()

    st.markdown("**Rename opponent**")
    rn = st.text_input("Correct name", value=name, key=f"rename_opp_{name}")
    if st.button("Rename") and rn.strip() and rn.strip() != name:
        opponents_mod.rename_opponent(conn, name, rn.strip())
        st.success(f"Renamed to {rn.strip()}.")
        st.rerun()

with mc2:
    st.markdown("**Add a dated match-history range**")
    with st.form("add_range", clear_on_submit=True):
        dc = st.columns(2)
        start = dc[0].date_input("From", value=_dt.date.today() - _dt.timedelta(days=30), key="r_start")
        end = dc[1].date_input("To", value=_dt.date.today(), key="r_end")
        wc = st.columns(3)
        w = wc[0].number_input("Wins", min_value=0, value=0, step=1)
        l = wc[1].number_input("Losses", min_value=0, value=0, step=1)
        rw = wc[2].number_input("Winnings ($)", value=0.0, step=1.0)
        note = st.text_input("Note (optional)")
        if st.form_submit_button("Add range"):
            opponents_mod.add_history_range(conn, name, str(start), str(end),
                                            int(w), int(l), float(rw), note or None)
            st.success("Range added.")
            st.rerun()

# ---------------------------------------------------------------------------
# The dated ranges, newest first
# ---------------------------------------------------------------------------
with st.expander(f"📅 Show date ranges — newest first ({agg['ranges']})"):
    ranges = opponents_mod.list_history_ranges(conn, name)
    if not ranges:
        st.caption("No date ranges added yet.")
    for r in ranges:
        rc = st.columns([3, 2, 2, 3, 1])
        rc[0].write(f"**{r['start_date'] or '?'} → {r['end_date'] or '?'}**")
        rc[1].write(f"{r['wins']}-{r['losses']}")
        rc[2].write(f"${r['winnings']:.0f}")
        rc[3].write(r["note"] or "")
        if rc[4].button("🗑", key=f"del_range_{r['history_id']}"):
            opponents_mod.delete_history_range(conn, r["history_id"])
            st.rerun()

# ---------------------------------------------------------------------------
# Scouting + tendency charts (only once there's draft history)
# ---------------------------------------------------------------------------
st.divider()
t = tendencies.load_tendencies(conn, name)
report = scouting.scouting_report(t)
st.subheader("Scouting report")
if not report["has_data"]:
    st.info("No drafted contests against this opponent yet — scouting appears once "
            "you save and recompute a contest with them in it.")
    st.stop()
for line in report["summary"]:
    st.markdown(f"- {line}")

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
