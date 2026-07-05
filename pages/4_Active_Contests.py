"""Active Contests page.

View saved contests and their scored boards, substitute players on any entry,
and settle results (actual scores + win/loss) which updates opponent H2H records
and feeds the History & Stats page.
"""

from __future__ import annotations

import streamlit as st

from dfs import boards_ui, contest as contest_mod, matching, registry
from dfs.db import connect

st.set_page_config(page_title="Active Contests", page_icon="📋", layout="wide")
st.title("📋 Active Contests")


@st.cache_resource
def get_conn():
    return connect()


conn = get_conn()

status = st.radio("Show", ["active", "all"], horizontal=True)
contests = contest_mod.list_contests(conn, status=None if status == "all" else "active")

if not contests:
    st.info("No contests yet. Build one on the **New Contest** page.")
    st.stop()

labels = {}
for c in contests:
    labels[
        f"#{c['contest_id']} · {c['site'] or 'site?'} · slate {c['slate_date'] or c['slate_id']} · {c['status']}"
    ] = c["contest_id"]

choice = st.selectbox("Contest", list(labels.keys()))
contest_id = labels[choice]
data = contest_mod.load_contest(conn, contest_id)
c = data["contest"]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Entries", len(data["entries"]))
m2.metric("Status", c["status"])
m3.metric("Buy-in", f"${c['buy_in']:.2f}" if c["buy_in"] else "—")
if c["status"] == "completed":
    m4.metric("Result", f"{(c['result'] or '—').upper()} · {c['my_actual_score'] or '—'}")
else:
    leader = next((e for e in data["entries"] if e["entry_id"] == data["leader_entry_id"]), None)
    m4.metric("Projected leader", leader["drafter_name"] if leader else "—")

st.divider()
rc1, rc2 = st.columns([3, 2])
rc1.caption("Pull the latest projections from this day's slate into the contest. "
            "Players whose game already started keep their last projection (never zeroed).")
if rc2.button("🔄 Update projections from slate", use_container_width=True):
    res = contest_mod.refresh_contest_projections(conn, contest_id)
    st.success(f"Refreshed: {res['updated']} updated, {res['kept']} kept "
               f"(game started), across {res['entries']} entries.")
    st.rerun()

st.divider()
boards_ui.render_contest(data)

# ---------------------------------------------------------------------------
# Edit picks — fix a name or override a projection
# ---------------------------------------------------------------------------
st.divider()
st.subheader("✏️ Edit picks")
entry_by_label = {
    (e["drafter_name"] or f"Seat {e['draft_slot']}") + (" ⭐" if e["is_me"] else ""): e
    for e in data["entries"]
}
ec1, ec2 = st.columns([2, 4])
edit_entry_label = ec1.selectbox("Entry", list(entry_by_label.keys()), key="edit_entry")
edit_entry = entry_by_label[edit_entry_label]

# Rename this drafter.
new_drafter = ec2.text_input("Rename drafter", value=edit_entry["drafter_name"] or "",
                             key=f"edit_drafter_{edit_entry['entry_id']}")
if ec2.button("Save drafter name") and new_drafter.strip():
    contest_mod.rename_entry_drafter(conn, edit_entry["entry_id"], new_drafter)
    st.success("Drafter renamed.")
    st.rerun()

edit_pick_by_label = {
    f"#{p['overall_pick_number']} {p['roster_slot'] or ''} — {p['full_name']} "
    f"({p['player_projection']:.2f}{' ✎' if p.get('overridden') else ''})": p
    for p in edit_entry["picks"]
}
if not edit_pick_by_label:
    st.info("This entry has no picks.")
else:
    edit_pick_label = st.selectbox("Pick", list(edit_pick_by_label.keys()), key="edit_pick")
    epick = edit_pick_by_label[edit_pick_label]

    fx1, fx2 = st.columns(2)
    # --- Fix player name -----------------------------------------------------
    with fx1:
        st.markdown("**Fix player name**")
        typed = st.text_input("Correct name", value=epick["full_name"], key=f"fix_name_{epick['pick_id']}")
        cands = matching.best_matches(conn, typed, 5) if typed.strip() else []
        fix_options = [f"➕ Create/keep: {typed.strip()}"] + [
            f"{full}  ({score:.0f})" for _pid, full, score in cands]
        fix_choice = st.selectbox("Resolve to", fix_options, key=f"fix_choice_{epick['pick_id']}")
        if st.button("Apply name fix"):
            if fix_choice.startswith("➕") or fix_choice not in fix_options:
                new_pid = registry.upsert_player(conn, typed.strip())
            else:
                new_pid = cands[fix_options.index(fix_choice) - 1][0]
            contest_mod.set_pick_player(conn, epick["pick_id"], new_pid)
            st.success("Player updated.")
            st.rerun()

    # --- Manual projection override -----------------------------------------
    with fx2:
        st.markdown("**Manual projection**")
        st.caption("Sticky — survives 'Update projections from slate'.")
        ov = st.number_input("Projection", min_value=0.0,
                             value=float(epick["player_projection"]), step=0.5,
                             key=f"ov_val_{epick['pick_id']}")
        ob1, ob2 = st.columns(2)
        if ob1.button("Set override"):
            contest_mod.set_pick_override(conn, epick["pick_id"], ov)
            st.success("Override set.")
            st.rerun()
        if ob2.button("Clear override", disabled=not epick.get("overridden")):
            contest_mod.set_pick_override(conn, epick["pick_id"], None)
            contest_mod.refresh_contest_projections(conn, contest_id)
            st.success("Override cleared; re-resolved from slate.")
            st.rerun()

# ---------------------------------------------------------------------------
# Substitution
# ---------------------------------------------------------------------------
st.divider()
st.subheader("🔁 Substitute a player")
sc1, sc2, sc3 = st.columns([2, 3, 3])
entry_label = sc1.selectbox("Entry", list(entry_by_label.keys()), key="sub_entry")
entry = entry_by_label[entry_label]

pick_by_label = {
    f"#{p['overall_pick_number']} {p['roster_slot'] or ''} — {p['full_name']} ({p['player_projection']:.2f})": p
    for p in entry["picks"]
}
if not pick_by_label:
    sc2.info("This entry has no picks.")
else:
    pick_label = sc2.selectbox("Pick to replace", list(pick_by_label.keys()), key="sub_pick")
    pick = pick_by_label[pick_label]
    pick_id = pick["pick_id"]

    options = contest_mod.slate_player_options(conn, c["slate_id"])
    repl_by_label = {f"{name} ({kind})": pid for pid, name, kind in options}
    repl_label = sc3.selectbox("Replacement (from slate)", list(repl_by_label.keys()), key="sub_repl")
    reason = st.text_input("Reason (optional)", key="sub_reason")
    if st.button("Apply substitution"):
        delta = contest_mod.substitute_player(
            conn, contest_id, entry["entry_id"], pick_id, repl_by_label[repl_label], reason=reason or None
        )
        st.success(f"Substituted. Projection change: {delta:+.2f}")
        st.rerun()

# ---------------------------------------------------------------------------
# Settle
# ---------------------------------------------------------------------------
st.divider()
st.subheader("🏁 Settle results")
st.caption("Enter each team's actual score and the result. Updates opponent H2H and your stats.")
with st.form("settle_form"):
    actual_inputs = {}
    for e in data["entries"]:
        label = (e["drafter_name"] or f"Seat {e['draft_slot']}") + (" ⭐ (you)" if e["is_me"] else "")
        default = float(e["actual_total"]) if e["actual_total"] is not None else 0.0
        actual_inputs[e["entry_id"]] = st.number_input(label, value=default, step=0.1, key=f"settle_{e['entry_id']}")
    fc1, fc2 = st.columns(2)
    result = fc1.selectbox("Your result", ["(auto from finish)", "win", "loss"])
    payout = fc2.number_input("Payout ($)", min_value=0.0, value=float(c["payout"] or 0.0), step=1.0)
    submitted = st.form_submit_button("Settle contest", type="primary")
if submitted:
    res = contest_mod.settle_contest(
        conn, contest_id, actual_inputs,
        result=None if result.startswith("(") else result,
        payout=payout or None,
    )
    st.success(f"Settled: **{(res['result'] or '—').upper()}**, finished #{res['my_finish']}.")
    st.rerun()
