"""SportsGameOdds (SGO) integration — fetch today's MLB props and build a slate.

Pulls current-day (US/Eastern) pre-match MLB events, converts SGO's fair (no-vig)
odds into over-probabilities, and produces:

* a batter props DataFrame in the exact shape the ladder engine already reads, and
* auto-computed pitcher projections from their prop lines.

The API key is read from the SGO_API_KEY environment variable or Streamlit
secrets — never hardcoded.

SGO oddID format: ``{statID}-{statEntityID}-{periodID}-{betTypeID}-{sideID}``;
player props have ``statEntityID`` = a playerID present in the event's players.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from . import config, projections

API_BASE = "https://api.sportsgameodds.com/v2"
# A real User-Agent — SGO 403s the default urllib UA.
_USER_AGENT = "Mozilla/5.0 (mlb-dfs)"

# SGO batting statID -> our normalized_market_key.
SGO_BATTER_MARKETS = {
    "points": config.MARKET_R,               # a batter's "points" = runs
    "batting_singles": config.MARKET_1B,
    "batting_doubles": config.MARKET_2B,
    "batting_triples": config.MARKET_3B,
    "batting_homeRuns": config.MARKET_HR,
    "batting_RBI": config.MARKET_RBI,
    "batting_basesOnBalls": config.MARKET_BB,
    "batting_stolenBases": config.MARKET_SB,
    "batting_hits": config.MARKET_HITS,       # kept for the singles residual fallback
}

# SGO pitching statID -> pitcher line key used by compute_pitcher_projection.
SGO_PITCH_STATS = {
    "pitching_outs": "outs",
    "pitching_strikeouts": "k",
    "pitching_earnedRuns": "er",
    "pitching_hits": "hits",
    "pitching_basesOnBalls": "bb",
}

BATTER_COLUMNS = [
    "player", "normalized_market_key", "point", "over_prob",
    "game", "commence_time_local", "away_team", "home_team",
]


# ---------------------------------------------------------------------------
# Config / auth
# ---------------------------------------------------------------------------
def api_key() -> str | None:
    key = os.environ.get("SGO_API_KEY")
    if not key:
        try:
            import streamlit as st
            key = st.secrets.get("SGO_API_KEY")
        except Exception:
            pass
    return str(key) if key else None


def configured() -> bool:
    return api_key() is not None


# ---------------------------------------------------------------------------
# Odds math
# ---------------------------------------------------------------------------
def american_to_prob(odds) -> float | None:
    """Implied probability from American odds (e.g. '+150' -> 0.4, '-120' -> .545)."""
    try:
        a = int(str(odds).replace("+", "").strip())
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    return round(100 / (a + 100), 4) if a > 0 else round((-a) / ((-a) + 100), 4)


def prob_for(odd: dict, odds: dict) -> float | None:
    """Best no-vig probability for one side: prefer SGO fairOdds; else de-vig the
    book over/under pair; else the raw book implied prob."""
    fair = odd.get("fairOdds")
    if fair:
        return american_to_prob(fair)
    book = odd.get("bookOdds")
    opp = odds.get(odd.get("opposingOddID")) if odd.get("opposingOddID") else None
    if book and opp and opp.get("bookOdds"):
        po, pu = american_to_prob(book), american_to_prob(opp["bookOdds"])
        if po is not None and pu is not None and (po + pu) > 0:
            return round(po / (po + pu), 4)
    return american_to_prob(book)


def _et():
    from zoneinfo import ZoneInfo
    return ZoneInfo("America/New_York")


def _to_et_str(iso_utc: str | None) -> str | None:
    if not iso_utc:
        return None
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(_et())
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_utc


# ---------------------------------------------------------------------------
# Parsing (pure — testable without the network)
# ---------------------------------------------------------------------------
def parse_event(event: dict) -> dict:
    """Return {batter_rows, player_teams, pitchers} for one SGO event."""
    teams = event.get("teams", {})
    team_short = {}
    for side in ("home", "away"):
        t = teams.get(side, {})
        if t.get("teamID"):
            team_short[t["teamID"]] = t.get("names", {}).get("short")
    away = teams.get("away", {}).get("names", {}).get("short")
    home = teams.get("home", {}).get("names", {}).get("short")
    game = f"{away}@{home}"
    commence = _to_et_str(event.get("status", {}).get("startsAt"))

    players = event.get("players", {})
    odds = event.get("odds", {})
    batter_rows: list[dict] = []
    player_teams: dict[str, str] = {}
    pitch: dict[str, dict] = defaultdict(dict)

    for o in odds.values():
        pid = o.get("statEntityID")
        if pid not in players:
            continue
        sid, bt, side = o.get("statID"), o.get("betTypeID"), o.get("sideID")
        name = players[pid].get("name")
        team = team_short.get(players[pid].get("teamID"))
        line = o.get("bookOverUnder")
        if line is None:
            line = o.get("fairOverUnder")

        if bt == "ou" and side == "over" and sid in SGO_BATTER_MARKETS and line is not None:
            p = prob_for(o, odds)
            if p is not None:
                batter_rows.append({
                    "player": name, "normalized_market_key": SGO_BATTER_MARKETS[sid],
                    "point": float(line), "over_prob": p, "game": game,
                    "commence_time_local": commence, "away_team": away, "home_team": home,
                })
                if team:
                    player_teams[name] = team

        if bt == "ou" and side == "over" and sid in SGO_PITCH_STATS and line is not None:
            pitch[pid][SGO_PITCH_STATS[sid]] = float(line)
            pitch[pid]["_name"], pitch[pid]["_team"] = name, team
        if sid == "pitching_win" and bt == "yn" and side == "yes":
            p = prob_for(o, odds)
            if p is not None:
                pitch[pid]["win_prob"] = p
                pitch[pid]["_name"], pitch[pid]["_team"] = name, team

    pitchers = []
    for lines in pitch.values():
        name = lines.pop("_name", None)
        team = lines.pop("_team", None)
        proj, comps = projections.compute_pitcher_projection(lines)
        pitchers.append({"name": name, "team": team, "proj_pts": proj,
                         "components": comps, "lines": dict(lines)})

    return {"batter_rows": batter_rows, "player_teams": player_teams, "pitchers": pitchers}


def build_slate(events: list[dict]) -> dict:
    """Combine parsed events into {batter_df, player_teams, pitchers}."""
    rows: list[dict] = []
    teams: dict[str, str] = {}
    pitchers: list[dict] = []
    for ev in events:
        parsed = parse_event(ev)
        rows.extend(parsed["batter_rows"])
        teams.update(parsed["player_teams"])
        pitchers.extend(parsed["pitchers"])
    batter_df = pd.DataFrame(rows, columns=BATTER_COLUMNS)
    # dedupe pitchers by name, keep the highest projection
    best: dict[str, dict] = {}
    for p in pitchers:
        key = (p["name"] or "").strip().lower()
        if key and (key not in best or p["proj_pts"] > best[key]["proj_pts"]):
            best[key] = p
    return {"batter_df": batter_df, "player_teams": teams,
            "pitchers": sorted(best.values(), key=lambda x: x["proj_pts"], reverse=True)}


# ---------------------------------------------------------------------------
# Live fetch
# ---------------------------------------------------------------------------
def _get(path: str, key: str, params: dict) -> dict:
    url = f"{API_BASE}{path}?{urlencode(params)}"
    req = Request(url, headers={"X-Api-Key": key, "User-Agent": _USER_AGENT})
    with urlopen(req, timeout=45) as r:
        return json.load(r)


def fetch_events(key: str | None = None, date_et=None, max_pages: int = 25) -> list[dict]:
    """Fetch today's (US/Eastern) pre-match MLB events with odds.

    Filters out any game that has already started, been cancelled, or completed,
    and keeps only games whose local (ET) date is today.
    """
    key = key or api_key()
    if not key:
        raise RuntimeError("No SGO API key. Set SGO_API_KEY (env or Streamlit secrets).")

    today = date_et or datetime.now(_et()).date()
    events: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        params = {"leagueID": "MLB", "oddsAvailable": "true", "limit": "5"}
        if cursor:
            params["cursor"] = cursor
        data = _get("/events/", key, params)
        stop = False
        for e in data.get("data", []):
            st = e.get("status", {})
            starts = st.get("startsAt")
            et_date = None
            if starts:
                try:
                    et_date = datetime.fromisoformat(
                        starts.replace("Z", "+00:00")).astimezone(_et()).date()
                except Exception:
                    et_date = None
            if et_date and et_date > today:
                stop = True  # ascending by date — nothing more for today
                continue
            if et_date != today:
                continue
            if st.get("started") or st.get("cancelled") or st.get("completed"):
                continue
            events.append(e)
        cursor = data.get("nextCursor")
        if stop or not cursor:
            break
    return events


def pull_slate(key: str | None = None, date_et=None) -> dict:
    """Fetch + build in one call. Returns build_slate(...) plus n_games."""
    events = fetch_events(key, date_et)
    slate = build_slate(events)
    slate["n_games"] = len(events)
    return slate
