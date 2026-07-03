"""Diagnostic: can THIS server (e.g. Streamlit Cloud) reach the sportsbooks?

DraftKings/FanDuel block datacenter IPs, so this checks whether the deployed
server can fetch them directly. If everything but the control is blocked, odds
must be pulled from a residential (local) machine. Temporary — safe to delete.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import streamlit as st

st.set_page_config(page_title="Odds source test", page_icon="🔎")
st.title("🔎 Odds source access test")
st.caption("Checks whether this server can reach DraftKings / FanDuel directly. "
           "Run it on the DEPLOYED app and tell me the results.")

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://sportsbook.draftkings.com/",
    "Origin": "https://sportsbook.draftkings.com",
}

TARGETS = {
    "FanDuel MLB (ON) — the one we USE": (
        "https://sbapi.on.sportsbook.fanduel.ca/api/content-managed-page"
        "?page=CUSTOM&customPageId=mlb&_ak=FhMFpcPWXMeyZxOx&timezone=America%2FToronto"
    ),
    "DraftKings MLB (nash)": "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusva/v1/leagues/84240",
    "Control — MLB StatsAPI": "https://statsapi.mlb.com/api/v1/teams?sportId=1",
}

if st.button("Run access test", type="primary"):
    for name, url in TARGETS.items():
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r:
                body = r.read(400)
                st.success(f"✅ {name}: HTTP {r.status} · {len(body)}+ bytes")
                st.code(body[:250].decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            st.error(f"⛔ {name}: HTTP {e.code} {e.reason}")
        except Exception as e:  # noqa: BLE001
            st.error(f"⛔ {name}: {type(e).__name__}: {e}")
