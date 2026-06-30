"""History & Stats page.

Your record, ROI, records by site/slot, head-to-head ledgers, projection
calibration (does the model run hot or cold?), and a filterable contest history.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dfs import boards_ui, contest as contest_mod, stats
from dfs.db import connect

st.set_page_config(page_title="History & Stats", page_icon="📈", layout="wide")
st.title("📈 History & Stats")


@st.cache_resource
def get_conn():
    return connect()


conn = get_conn()

# ---------------------------------------------------------------------------
# My stats
# ---------------------------------------------------------------------------
s = stats.my_stats(conn)
if s["n"] == 0:
    st.info("No settled contests yet. Settle one on the **Active Contests** page to build your stats.")
    st.stop()

st.subheader("Your record")
m = st.columns(6)
m[0].metric("Record", f"{s['wins']}–{s['losses']}")
m[1].metric("Win rate", f"{s['win_rate']*100:.0f}%")
m[2].metric("ROI", f"{s['roi']*100:.0f}%" if s["roi"] is not None else "—")
m[3].metric("Profit", f"${s['profit']:.2f}")
m[4].metric("Best / Worst", f"{(s['best'] or 0):.1f} / {(s['worst'] or 0):.1f}")
streak_txt = f"{s['streak']}{(s['streak_type'] or '')[:1].upper()}" if s["streak_type"] else "—"
m[5].metric("Streak · Avg finish", f"{streak_txt} · {s['avg_finish'] or '—'}")

# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
st.subheader("Projection calibration")
st.caption("`actual − projected` per contest. Positive = the model ran **cold** (you/they beat the projection); negative = ran **hot**.")
mine = stats.calibration_series(conn, is_me=True)
opp = stats.calibration_series(conn, is_me=False)
if mine:
    chart_df = pd.DataFrame({"contest": [r["contest_id"] for r in mine],
                             "you": [r["delta"] for r in mine]}).set_index("contest")
    st.line_chart(chart_df)
    cc = st.columns(2)
    my_mean = sum(r["delta"] for r in mine) / len(mine)
    cc[0].metric("Your avg miss", f"{my_mean:+.2f}")
    if opp:
        opp_mean = sum(r["delta"] for r in opp) / len(opp)
        cc[1].metric("Opponents avg miss", f"{opp_mean:+.2f}")
else:
    st.caption("No calibration data yet.")

# ---------------------------------------------------------------------------
# Records by site / slot + H2H ledger
# ---------------------------------------------------------------------------
r1, r2, r3 = st.columns(3)
with r1:
    st.markdown("**By site**")
    rows = stats.records_by_site(conn)
    if rows:
        st.dataframe(pd.DataFrame([
            {"Site": r["key"], "W-L": f"{r['wins']}-{r['losses']}",
             "Win%": f"{(r['win_rate'] or 0)*100:.0f}%",
             "ROI": f"{r['roi']*100:.0f}%" if r["roi"] is not None else "—"}
            for r in rows
        ]), hide_index=True, use_container_width=True)
with r2:
    st.markdown("**By draft slot**")
    rows = stats.records_by_slot(conn)
    if rows:
        st.dataframe(pd.DataFrame([
            {"Slot": r["key"], "W-L": f"{r['wins']}-{r['losses']}",
             "Win%": f"{(r['win_rate'] or 0)*100:.0f}%"}
            for r in rows
        ]), hide_index=True, use_container_width=True)
with r3:
    st.markdown("**Head-to-head (your record)**")
    ledger = stats.h2h_ledger(conn)
    if ledger:
        st.dataframe(pd.DataFrame([
            {"Opponent": r["name"], "You W-L": f"{r['h2h_wins']}-{r['h2h_losses']}",
             "GP": r["contests_played"],
             "Their avg": f"{r['avg_actual_score']:.1f}" if r["avg_actual_score"] is not None else "—"}
            for r in ledger
        ]), hide_index=True, use_container_width=True)
    else:
        st.caption("No opponents tracked yet.")

# ---------------------------------------------------------------------------
# History (filterable)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("History")
f1, f2, f3, f4 = st.columns(4)
site = f1.selectbox("Site", ["(all)"] + stats.distinct_sites(conn))
slot = f2.selectbox("Draft slot", ["(all)"] + [str(x) for x in stats.distinct_slots(conn)])
opponent = f3.selectbox("Opponent", ["(all)"] + stats.distinct_opponents(conn))
status_f = f4.selectbox("Status", ["(all)", "completed", "active"])

rows = stats.history(
    conn,
    site=None if site == "(all)" else site,
    slot=None if slot == "(all)" else int(slot),
    opponent=None if opponent == "(all)" else opponent,
    status=None if status_f == "(all)" else status_f,
)
if not rows:
    st.caption("No contests match those filters.")
else:
    hist_df = pd.DataFrame([
        {
            "#": r["contest_id"], "Date": r["slate_date"], "Site": r["site"],
            "Slot": r["my_draft_slot"], "Status": r["status"],
            "Result": (r["result"] or "").upper(), "Finish": r["finish_place"],
            "My score": r["my_actual_score"],
            "Buy-in": r["buy_in"], "Payout": r["payout"],
        }
        for r in rows
    ])
    st.dataframe(hist_df, hide_index=True, use_container_width=True)

    ids = {f"#{r['contest_id']} · {r['site'] or ''} · {r['status']}": r["contest_id"] for r in rows}
    pick = st.selectbox("View a contest's board", ["(none)"] + list(ids.keys()))
    if pick != "(none)":
        boards_ui.render_contest(contest_mod.load_contest(conn, ids[pick]))
