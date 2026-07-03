"""Streamlit entrypoint (Home).

Run with:  streamlit run app.py

Pages live in ./pages and appear in the sidebar.
"""

from __future__ import annotations

import datetime as _dt

import streamlit as st

from dfs import cloudsync
from dfs.db import effective_db_path

st.set_page_config(page_title="MLB DFS Engine", page_icon="⚾", layout="wide")

st.title("⚾ MLB DFS Projection & Contest Engine")
st.caption("Local-first · 100% free · all data in one SQLite file")
# Bump this on each deploy so you can confirm the running build after a reboot.
st.caption("🔧 build 2026-07-02e — odds source access test")

st.markdown(
    """
Welcome. This app turns sportsbook odds into player projections, scores your
draft contests, tracks results, and learns your opponents. Use the sidebar:

1. **Projections** — upload a batter-props CSV, assign positions, enter pitchers,
   view/sort/filter the table, save the slate.
2. **Draft Board** — a temporary live-draft "who's left" board.
3. **New Contest** — read a draft screenshot (OCR) or enter it by hand, score it.
4. **Active Contests** — substitute players, settle results.
5. **History & Stats** — record, ROI, calibration, head-to-head ledgers.
6. **Opponents** — scouting reports and draft-tendency charts.
"""
)

# ---------------------------------------------------------------------------
# Database + backup / restore
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Database")
db_path = effective_db_path()
st.write(f"SQLite file: `{db_path}`")
st.write("All your data is this one file. Set the `DFS_DB_PATH` environment "
         "variable to relocate it.")

# --- Cloud persistence (Turso) ---------------------------------------------
if cloudsync.configured():
    st.success("☁️ **Cloud persistence is ON** (Turso). Your data is restored on "
               "startup and auto-saved in the background, so it survives reboots/redeploys.")
    cc = st.columns(3)
    if cc[0].button("☁️ Save to cloud now"):
        st.success("Saved to cloud." if cloudsync.force_push(db_path) else "Nothing to save.")
    if cc[1].button("⬇️ Restore from cloud"):
        if cloudsync.force_pull(db_path):
            st.cache_resource.clear()
            st.success("Restored from cloud. Reloading…")
            st.rerun()
        else:
            st.info("No cloud backup found yet.")
    if cc[2].button("🔌 Test connection"):
        info = cloudsync.status(db_path)
        if info["error"]:
            st.error(f"Cloud error: {info['error']}")
        else:
            st.info(f"Connected. Cloud backup holds {info['cloud_bytes']} bytes.")
else:
    st.info("☁️ **Cloud persistence is OFF.** On Streamlit Cloud, data is wiped on "
            "reboot/redeploy. To keep it for free, set up **Turso** — see the README "
            "*Data persistence* section. (Running locally, your data already persists.)")

with st.expander("💾 Backup & restore  (important on Streamlit Cloud)"):
    st.caption(
        "Streamlit Cloud's storage is **ephemeral** — your data can be wiped on "
        "reboot or redeploy. Download a backup regularly, and restore it after a "
        "reset. (Running locally, your data persists on disk and this is optional.)"
    )

    if db_path.exists():
        with open(db_path, "rb") as fh:
            st.download_button(
                "⬇️ Download backup (.db)",
                data=fh.read(),
                file_name=f"dfs_backup_{_dt.date.today().isoformat()}.db",
                mime="application/x-sqlite3",
            )
    else:
        st.info("No database yet — it's created the first time you save data.")

    st.markdown("**Restore** (replaces all current data):")
    up = st.file_uploader("Upload a backup .db", type=["db"], key="restore_db")
    if up is not None:
        raw = up.getvalue()
        if not raw.startswith(b"SQLite format 3\x00"):
            st.error("That doesn't look like a SQLite database file.")
        elif st.button("♻️ Restore this backup (overwrites current data)"):
            try:
                st.cache_resource.clear()  # drop cached DB connections first
                db_path.parent.mkdir(parents=True, exist_ok=True)
                with open(db_path, "wb") as fh:
                    fh.write(raw)
                st.success("Restored. Reloading…")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Restore failed — the app may need a reboot to release the file. {e}")
