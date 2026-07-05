"""Shared Streamlit rendering for scored contest boards.

Used by both the New Contest (preview) and Active Contests pages so they look
identical. Imports streamlit lazily-at-top (only the UI imports this module).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# No DNP badge — a player without a projection in the slate just shows 0; check
# the site's lineup for actual scratches.
SOURCE_FLAG = {"dnp": "", "batter_fallback": "🔁 bat", "pitcher": "", "batter": "",
               "manual": "✎"}


def render_leaderboard(data: dict) -> None:
    """A ranked summary table with the leader and 'me' highlighted."""
    entries = data["entries"]
    if not entries:
        st.info("No entries.")
        return
    leader_id = data["leader_entry_id"]
    settled = any(e.get("actual_total") is not None for e in entries)
    rows = []
    for e in sorted(entries, key=lambda x: (x["projected_total"] or 0.0), reverse=True):
        s = e["summary"]
        row = {
            "Drafter": ("⭐ " if e["is_me"] else "") + (e["drafter_name"] or f"Seat {e['draft_slot']}"),
            "Seat": e["draft_slot"],
            "Projected": round(e["projected_total"] or 0.0, 2),
        }
        if settled:
            actual = e.get("actual_total")
            row["Actual"] = round(actual, 2) if actual is not None else None
            row["Δ"] = round((actual - (e["projected_total"] or 0.0)), 2) if actual is not None else None
            row["Place"] = e.get("finish_place")
        row.update({
            "Floor": s["floor"],
            "Pitch": s["pitching"],
            "Hit": s["hitting"],
            "_leader": e["entry_id"] == leader_id,
            "_me": e["is_me"],
        })
        rows.append(row)
    df = pd.DataFrame(rows)
    display_df = df.drop(columns=["_leader", "_me"])

    def _style_row(row):
        # row comes from display_df; use the matching index in df for flags.
        if bool(df.loc[row.name, "_leader"]):
            return ["background-color: rgba(60,160,90,0.25)"] * len(row)
        if bool(df.loc[row.name, "_me"]):
            return ["background-color: rgba(60,120,200,0.18)"] * len(row)
        return [""] * len(row)

    fmt = {"Projected": "{:.2f}", "Actual": "{:.2f}", "Δ": "{:+.2f}", "Floor": "{:.2f}",
           "Pitch": "{:.1f}", "Hit": "{:.1f}"}
    fmt = {k: v for k, v in fmt.items() if k in display_df.columns}
    styler = display_df.style.apply(_style_row, axis=1).format(fmt, na_rep="—")
    st.dataframe(styler, hide_index=True, use_container_width=True)
    st.caption("👑 green = projected leader · ⭐ blue = you · 🔁 = hitter scored in a P slot (Ohtani-type)")


def render_pick_boards(data: dict) -> None:
    """Side-by-side per-drafter pick lists (expanders when there are many)."""
    entries = data["entries"]
    if not entries:
        return
    leader_id = data["leader_entry_id"]

    def _picks_df(e):
        return pd.DataFrame([
            {
                "#": p["overall_pick_number"],
                "Slot": p["roster_slot"] or "",
                "Player": p["full_name"],
                "Proj": round(p["player_projection"], 2),
                "": SOURCE_FLAG.get(p["source"], ""),
            }
            for p in e["picks"]
        ])

    def _header(e):
        crown = "👑 " if e["entry_id"] == leader_id else ""
        me = " ⭐" if e["is_me"] else ""
        return f"{crown}{e['drafter_name'] or ('Seat ' + str(e['draft_slot']))}{me} — {(e['projected_total'] or 0):.2f}"

    if len(entries) <= 4:
        for col, e in zip(st.columns(len(entries)), entries):
            with col:
                st.markdown(f"**{_header(e)}**")
                st.dataframe(_picks_df(e), hide_index=True, use_container_width=True)
    else:
        for e in entries:
            with st.expander(_header(e), expanded=bool(e["is_me"])):
                st.dataframe(_picks_df(e), hide_index=True, use_container_width=True)


def render_contest(data: dict) -> None:
    render_leaderboard(data)
    st.markdown("#### Boards")
    render_pick_boards(data)
