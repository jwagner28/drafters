"""New Contest page.

Build a drafted board (from a screenshot via OCR, or by hand), confirm fuzzy
name matches against the registry, see the scored side-by-side boards, and save
the contest as active.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dfs import boards_ui, contest as contest_mod, draft, matching, ocr, registry
from dfs import slate as slate_mod
from dfs.db import connect

st.set_page_config(page_title="New Contest", page_icon="🆕", layout="wide")
st.title("🆕 New Contest")


@st.cache_resource
def get_conn():
    return connect()


conn = get_conn()
ss = st.session_state

SLOT_OPTIONS = ["", "P", "IF", "OF", "HT"]

ss.setdefault("nc_N", 4)
ss.setdefault("nc_R", 10)
# OCR auto-detects the player/round counts; apply them here, before the widgets.
if "nc_pending_dims" in ss:
    ss["nc_N"], ss["nc_R"] = ss.pop("nc_pending_dims")

# ---------------------------------------------------------------------------
# 1. Slate to score against
# ---------------------------------------------------------------------------
st.header("1. Slate")
slates = slate_mod.list_slates(conn)
if not slates:
    st.warning("No saved slates. Create one on the **Projections** page first.")
    st.stop()
slate_labels = {
    f"#{s['slate_id']} · {s['date']}" + (f" · {s['notes']}" if s["notes"] else ""): s["slate_id"]
    for s in slates
}
slate_choice = st.selectbox("Score this contest against", list(slate_labels.keys()))
slate_id = slate_labels[slate_choice]

# ---------------------------------------------------------------------------
# 2. Draft setup
# ---------------------------------------------------------------------------
st.header("2. Draft setup")
st.caption("OCR auto-detects these from a screenshot; set them by hand for manual entry.")
c1, c2 = st.columns(2)
num_drafters = int(c1.number_input("Drafters (seats)", min_value=2, max_value=20, key="nc_N"))
num_rounds = int(c2.number_input("Rounds", min_value=1, max_value=40, key="nc_R"))

# Drafter names + which seat is me. Kept in session and resized to N.
drafters = ss.get("nc_drafters") or []
while len(drafters) < num_drafters:
    i = len(drafters) + 1
    drafters.append({"Seat": i, "Drafter": f"Drafter {i}", "Me": False})
drafters = drafters[:num_drafters]
for i, d in enumerate(drafters):
    d["Seat"] = i + 1
drafters_df = st.data_editor(
    pd.DataFrame(drafters),
    column_config={
        "Seat": st.column_config.NumberColumn("Seat", disabled=True),
        "Drafter": st.column_config.TextColumn("Drafter name"),
        "Me": st.column_config.CheckboxColumn("Me?"),
    },
    hide_index=True,
    use_container_width=True,
    key="nc_drafters_editor",
)
ss["nc_drafters"] = drafters_df.to_dict("records")

# ---------------------------------------------------------------------------
# 3. Fill the board — OCR assist (optional) or manual
# ---------------------------------------------------------------------------
st.header("3. Draft board")

with st.expander("📷 Fill from screenshot (OCR) — optional"):
    ok, msg = ocr.ocr_available()
    if not ok:
        st.info(msg + "  \nOCR is optional — you can always fill the grid manually below.")
    else:
        st.success(msg)
        st.caption(
            "Upload your draft board. It auto-detects each colored pick box (any "
            "zoom, 2–4 players, any rounds) and reads **name** (top-left), **pick #** "
            "(top-right, by snake order), **position** (under it), **team** "
            "(bottom-left). Check the green-box preview, then run OCR."
        )
        img = st.file_uploader("Board screenshot", type=["png", "jpg", "jpeg"], key="nc_img")
        threshold = st.slider(
            "Box brightness threshold", 15, 150, 35, 5,
            help="Lower if dim boxes aren't detected; raise if the background is being picked up.",
        )
        if img is not None:
            try:
                base = ocr._open_image(img.getvalue())
                det = ocr.detect_boxes(base, threshold)
                st.image(
                    ocr.detect_overlay(base, det),
                    caption=f"Detected {det['n_cols']} drafters × {det['n_rows']} rounds "
                            f"({len(det['boxes'])} boxes). Adjust the slider if this looks wrong.",
                )
                if not det["boxes"]:
                    st.warning("No boxes detected — try lowering the brightness threshold.")
            except Exception as e:  # noqa: BLE001
                st.warning("Couldn't render detection preview — full error below.")
                st.exception(e)
        if img is not None and st.button("Run OCR", key="nc_run_ocr"):
            try:
                result = ocr.ocr_board(img.getvalue(), threshold)
                n = result["n_drafters"]
                r = result["n_rounds"]
                # Queue detected dimensions for the next run (before the widgets).
                ss["nc_pending_dims"] = (n, r)
                ss["nc_drafters"] = [
                    {
                        "Seat": i + 1,
                        "Drafter": (result["drafters"][i].strip()
                                    if i < len(result["drafters"]) and result["drafters"][i].strip()
                                    else f"Drafter {i + 1}"),
                        "Me": False,
                    }
                    for i in range(n)
                ]
                seat_names = {d["Seat"]: d["Drafter"] for d in ss["nc_drafters"]}
                by_rc = {(p["round"], p["seat"]): p for p in result["picks"]}
                ss["nc_grid"] = [
                    {
                        "Overall": cell.overall_pick_number, "Rd": cell.round_number, "Seat": cell.seat,
                        "Drafter": seat_names.get(cell.seat, f"Seat {cell.seat}"),
                        "Player": by_rc.get((cell.round_number, cell.seat), {}).get("name", "") or "",
                        "Team": by_rc.get((cell.round_number, cell.seat), {}).get("team") or "",
                        "Slot": by_rc.get((cell.round_number, cell.seat), {}).get("roster_slot") or "",
                    }
                    for cell in draft.generate_grid(n, r)
                ]
                ss.pop("nc_matches", None)
                st.success(f"OCR complete — detected {n} drafters × {r} rounds. "
                           "Review and correct the grid below, then resolve players.")
                st.rerun()
            except Exception as e:  # noqa: BLE001 - surface OCR failures to the user
                st.error("OCR failed — full error below.")
                st.exception(e)

if st.button("🧱 Build / reset grid from setup"):
    seat_names = {d["Seat"]: d["Drafter"] for d in ss["nc_drafters"]}
    ss["nc_grid"] = [
        {
            "Overall": c.overall_pick_number, "Rd": c.round_number, "Seat": c.seat,
            "Drafter": seat_names.get(c.seat, f"Seat {c.seat}"),
            "Player": "", "Team": "", "Slot": "",
        }
        for c in draft.generate_grid(int(num_drafters), int(num_rounds))
    ]
    ss.pop("nc_matches", None)

grid = ss.get("nc_grid")
if not grid:
    st.info("Click **Build / reset grid** (or run OCR) to lay out the snake-order picks, then fill in players.")
    st.stop()

st.caption("Picks are pre-laid in snake order. Fill **Player** (and Team/Slot). Leave a row blank if not drafted yet.")
grid_df = st.data_editor(
    pd.DataFrame(grid),
    column_config={
        "Overall": st.column_config.NumberColumn("#", disabled=True, width="small"),
        "Rd": st.column_config.NumberColumn("Rd", disabled=True, width="small"),
        "Seat": st.column_config.NumberColumn("Seat", disabled=True, width="small"),
        "Drafter": st.column_config.TextColumn("Drafter", disabled=True),
        "Player": st.column_config.TextColumn("Player"),
        "Team": st.column_config.TextColumn("Team", width="small"),
        "Slot": st.column_config.SelectboxColumn("Slot", options=SLOT_OPTIONS, width="small"),
    },
    hide_index=True,
    use_container_width=True,
    height=480,
    key="nc_grid_editor",
)
ss["nc_grid"] = grid_df.to_dict("records")

# ---------------------------------------------------------------------------
# 4. Resolve player names (fuzzy match + confirm)
# ---------------------------------------------------------------------------
st.header("4. Match players")
if st.button("🔎 Resolve players"):
    names = sorted({str(r["Player"]).strip() for r in ss["nc_grid"] if str(r["Player"]).strip()})
    if not names:
        st.warning("No players entered yet.")
    else:
        ss["nc_names"] = names
        ss["nc_matches"] = {n: matching.best_matches(conn, n, 5) for n in names}


def _options_for(name: str) -> list[str]:
    cands = ss["nc_matches"].get(name, [])
    return [f"➕ Create new: {name}"] + [f"{full}  ({score:.0f})" for _pid, full, score in cands]


if ss.get("nc_matches"):
    st.caption("Confirm each name. Strong matches default to the registry player; weak ones default to a new player.")
    for name in ss["nc_names"]:
        cands = ss["nc_matches"].get(name, [])
        options = _options_for(name)
        # Pre-select a match only when it's strong (right surname + initial);
        # otherwise default to "create new" so wrong same-surname players aren't
        # auto-picked.
        default = 1 if (cands and cands[0][2] >= matching.STRONG_DEFAULT) else 0
        st.selectbox(f"“{name}” →", options, index=default, key=f"nc_res_{name}")

# ---------------------------------------------------------------------------
# 5. Score & save
# ---------------------------------------------------------------------------
st.header("5. Score & save")
sc1, sc2, sc3 = st.columns(3)
site = sc1.text_input("Site", value="Underdog")
fmt = sc2.text_input("Format", value="")
buy_in = sc3.number_input("Buy-in ($)", min_value=0.0, value=0.0, step=1.0)

if st.button("💾 Score & save contest", type="primary", disabled=not ss.get("nc_matches")):
    drafters_rec = ss["nc_drafters"]
    seat_meta = {d["Seat"]: d for d in drafters_rec}

    # Resolve each raw name to a player_id (creating new players when chosen).
    # We deliberately do NOT save the raw spelling as an alias — the last-name
    # matcher resolves "F. Lastname" deterministically, and stored aliases were
    # what previously mis-routed players.
    resolution: dict[str, int] = {}
    for name in ss["nc_names"]:
        choice = ss.get(f"nc_res_{name}", "")
        options = _options_for(name)
        cands = ss["nc_matches"].get(name, [])
        if not choice or choice.startswith("➕ Create new") or choice not in options:
            team = next((str(r["Team"]).strip() for r in ss["nc_grid"]
                         if str(r["Player"]).strip() == name and str(r["Team"]).strip()), None)
            resolution[name] = registry.upsert_player(conn, name, team=team or None)
        else:
            resolution[name] = cands[options.index(choice) - 1][0]

    # Build entries grouped by seat.
    seat_entries: dict[int, dict] = {}
    for r in ss["nc_grid"]:
        pname = str(r["Player"]).strip()
        if not pname:
            continue
        seat = int(r["Seat"])
        meta = seat_meta.get(seat, {})
        entry = seat_entries.setdefault(seat, {
            "drafter_name": meta.get("Drafter", f"Seat {seat}"),
            "is_me": 1 if meta.get("Me") else 0,
            "draft_slot": seat,
            "picks": [],
        })
        overall = int(r["Overall"])
        rd = int(r["Rd"])
        entry["picks"].append({
            "overall_pick_number": overall,
            "round_number": rd,
            "slot_in_round": overall - (rd - 1) * int(num_drafters),
            "player_id": resolution[pname],
            "roster_slot": (str(r["Slot"]).strip() or None),
        })

    if not seat_entries:
        st.warning("Nothing to save — no players entered.")
    else:
        my_seat = next((d["Seat"] for d in drafters_rec if d.get("Me")), None)
        entries = [seat_entries[s] for s in sorted(seat_entries)]
        contest_id = contest_mod.save_contest(
            conn, slate_id, entries, site=site or None, format=fmt or None,
            my_draft_slot=my_seat, buy_in=buy_in or None, status="active",
        )
        ss["nc_saved"] = contest_id
        st.success(f"Saved contest #{contest_id} as **active**.")

if ss.get("nc_saved"):
    st.divider()
    st.subheader(f"Scored board — contest #{ss['nc_saved']}")
    boards_ui.render_contest(contest_mod.load_contest(conn, ss["nc_saved"]))
