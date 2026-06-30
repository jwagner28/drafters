"""Draft Board (temporary).

A scratch board for live drafting: three columns (IF / OF / P) of Team · Player
· Proj, sorted high to low. Cross players off as they're drafted to see who's
left. Everything here lives in session state only — nothing is written to the
database, and it all clears when you reset or close the app.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dfs import draftboard
from dfs import slate as slate_mod
from dfs.db import connect

st.set_page_config(page_title="Draft Board", page_icon="⚡", layout="wide")
st.title("⚡ Draft Board")
st.caption(
    "Temporary live-draft board — mark players off as they're taken to see who's "
    "left. Nothing is saved; it clears on **Reset** or when you close the app."
)


@st.cache_resource
def get_conn():
    return connect()


conn = get_conn()
ss = st.session_state
ss.setdefault("draft_taken", set())
ss.setdefault("draft_board", None)
ss.setdefault("draft_unassigned", [])

GROUP_COLORS = {"IF": "#1f77b4", "OF": "#2e9b57", "P": "#d6432f"}


def _norm(name: str) -> str:
    return " ".join(name.strip().lower().split())


# ---------------------------------------------------------------------------
# Load projections (saved slate or CSV)
# ---------------------------------------------------------------------------
with st.expander("📥 Load projections", expanded=ss["draft_board"] is None):
    source = st.radio("Source", ["Saved slate", "Upload CSV"], horizontal=True)

    if source == "Saved slate":
        slates = slate_mod.list_slates(conn)
        if not slates:
            st.info("No saved slates yet. Save one on the **Projections** page, or upload a CSV.")
        else:
            labels = {
                f"#{s['slate_id']} · {s['date']}" + (f" · {s['notes']}" if s["notes"] else ""): s["slate_id"]
                for s in slates
            }
            choice = st.selectbox("Slate", list(labels.keys()))
            if st.button("Load board", type="primary", key="load_slate"):
                board, unassigned = draftboard.load_board_from_slate(conn, labels[choice])
                ss["draft_board"] = board
                ss["draft_unassigned"] = unassigned
                ss["draft_taken"] = set()
                st.success("Board loaded.")
    else:
        st.caption("Columns (case-insensitive): **name**, **position** (IF/OF/P), **proj**, optional **team**.")
        up = st.file_uploader("Projections CSV", type=["csv"], key="board_csv")
        if up is not None and st.button("Load board", type="primary", key="load_csv"):
            try:
                board = draftboard.load_board_from_csv(pd.read_csv(up))
                ss["draft_board"] = board
                ss["draft_unassigned"] = []
                ss["draft_taken"] = set()
                st.success("Board loaded.")
            except ValueError as e:
                st.error(str(e))

board = ss["draft_board"]
if not board:
    st.info("Load a slate or CSV above to start your draft board.")
    st.stop()

taken: set[str] = ss["draft_taken"]

# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
c1, c2, c3, c4 = st.columns([1.2, 1, 2.2, 1.4])
hide_taken = c1.toggle("Hide taken", value=True, help="Hide drafted players so only who's-left shows.")
query = c3.text_input("Find player", placeholder="type a name to locate / cross off", label_visibility="collapsed")
if c2.button("🔄 Reset", help="Clear all marks"):
    ss["draft_taken"] = set()
    st.rerun()

total = sum(len(board[g]) for g in draftboard.GROUPS)
remaining_total = sum(1 for g in draftboard.GROUPS for r in board[g] if _norm(r["Name"]) not in taken)
c4.metric("Players left", f"{remaining_total} / {total}")

if ss["draft_unassigned"]:
    names = ss["draft_unassigned"]
    st.warning(
        f"{len(names)} batter(s) have no IF/OF assignment and are hidden: "
        + ", ".join(names[:12]) + (" …" if len(names) > 12 else "")
        + ". Assign them on the **Projections** page."
    )

q = query.strip().lower()

# ---------------------------------------------------------------------------
# Three-column board
# ---------------------------------------------------------------------------
cols = st.columns(3)
for col, group in zip(cols, draftboard.GROUPS):
    rows = board[group]
    remaining = sum(1 for r in rows if _norm(r["Name"]) not in taken)
    with col:
        st.markdown(
            f"<h3 style='color:{GROUP_COLORS[group]};margin:0'>{group}"
            f" <span style='font-size:0.55em;color:#888'>{remaining} left / {len(rows)}</span></h3>",
            unsafe_allow_html=True,
        )
        head = st.columns([1, 1.6, 4.4, 1.6])
        head[1].caption("Team")
        head[2].caption("Player")
        head[3].caption("Proj")

        for i, r in enumerate(rows):
            key = _norm(r["Name"])
            is_taken = key in taken
            if hide_taken and is_taken:
                continue
            if q and q not in r["Name"].lower():
                continue

            rc = st.columns([1, 1.6, 4.4, 1.6])
            if rc[0].button("↺" if is_taken else "✕", key=f"tg_{group}_{i}",
                            help="Restore" if is_taken else "Mark taken"):
                if is_taken:
                    taken.discard(key)
                else:
                    taken.add(key)
                st.rerun()

            rc[1].markdown(f"<span style='color:#888'>{r['Team']}</span>", unsafe_allow_html=True)
            if is_taken:
                rc[2].markdown(
                    f"<span style='color:#bbb;text-decoration:line-through'>{r['Name']}</span>",
                    unsafe_allow_html=True,
                )
                rc[3].markdown(
                    f"<span style='color:#bbb;text-decoration:line-through'>{r['Proj']:.2f}</span>",
                    unsafe_allow_html=True,
                )
            else:
                rc[2].markdown(f"**{r['Name']}**")
                rc[3].markdown(f"`{r['Proj']:.2f}`")
