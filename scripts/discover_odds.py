"""Run this LOCALLY (your machine, your home internet) to capture FanDuel +
DraftKings MLB odds so I can build the parsers from your real data.

    python scripts/discover_odds.py --state va      # use YOUR FanDuel state

It saves raw JSON into ./odds_dump/ and prints a summary. Paste me the summary
(and/or send the files in odds_dump/) and I'll wire up the parsers.

Stdlib only — no installs needed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
OUT = pathlib.Path("odds_dump")


def get(url: str, headers: dict | None = None, timeout: int = 30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        **(headers or {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def try_draftkings():
    print("\n=== DraftKings MLB (pitcher props) ===")
    urls = {
        "dk_v5_eventgroup": "https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/84240?format=json",
        "dk_nash": "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusny/v1/leagues/84240",
    }
    for name, url in urls.items():
        try:
            status, body = get(url)
            (OUT / f"{name}.json").write_bytes(body)
            print(f"  OK {name}: HTTP {status}, {len(body)} bytes -> odds_dump/{name}.json")
            try:
                eg = json.loads(body).get("eventGroup", {})
                for cat in eg.get("offerCategories", []):
                    subs = [s.get("name") for s in cat.get("offerSubcategoryDescriptors", [])]
                    print(f"     category {cat.get('offerCategoryId')} {cat.get('name')!r}: {subs}")
            except Exception:
                pass
        except urllib.error.HTTPError as e:
            print(f"  BLOCKED {name}: HTTP {e.code} {e.reason}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {name}: {type(e).__name__}: {e}")


def try_fanduel(state: str, ak: str):
    print(f"\n=== FanDuel MLB (batter props) — state={state} ===")
    base = f"https://sbapi.{state}.fanduel.com/api"
    page = (f"{base}/content-managed-page?page=CUSTOM&customPageId=mlb"
            f"&_ak={ak}&timezone=America%2FNew_York")
    try:
        status, body = get(page)
        (OUT / "fd_mlb_page.json").write_bytes(body)
        print(f"  OK page: HTTP {status}, {len(body)} bytes -> odds_dump/fd_mlb_page.json")
        events = (json.loads(body).get("attachments", {}) or {}).get("events", {}) or {}
        print(f"     events found: {len(events)}")
        if events:
            eid = next(iter(events))
            s2, b2 = get(f"{base}/event-page?eventId={eid}&_ak={ak}")
            (OUT / "fd_event_page.json").write_bytes(b2)
            print(f"  OK event {eid}: HTTP {s2}, {len(b2)} bytes -> odds_dump/fd_event_page.json")
            markets = (json.loads(b2).get("attachments", {}) or {}).get("markets", {}) or {}
            names = sorted({(m.get("marketType") or m.get("marketName") or "?") for m in markets.values()})
            print(f"     market types (sample): {names[:40]}")
    except urllib.error.HTTPError as e:
        print(f"  BLOCKED FanDuel: HTTP {e.code} {e.reason}")
        print("     The _ak key or state host is probably wrong. In your browser: open")
        print("     FanDuel MLB -> F12 -> Network -> XHR -> click a game's props, and copy")
        print("     the request URL (it contains the correct host and _ak). Send me that URL.")
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR FanDuel: {type(e).__name__}: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="va", help="your FanDuel state, e.g. va, pa, nj, mi, co")
    ap.add_argument("--ak", default="FhMFpcPWXMeyZxOx",
                    help="FanDuel _ak key from the site if the default one fails")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    try_draftkings()
    try_fanduel(args.state, args.ak)
    print("\nDone. Paste me the summary above (and send odds_dump/*.json if you can).")
