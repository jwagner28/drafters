"""FanDuel batter props.

FanDuel's public `_ak`-keyed sportsbook API needs no login cookie and works from
any IP (including cloud servers) — unlike DraftKings, which blocks datacenter
IPs. It exposes yes/no threshold markets ("to hit a HR", "to hit 2+ HR", "to
record an RBI", "to record 2+ RBIs", …) which give the projection engine a real
multi-rung ladder (P(≥1), P(≥2)).

Odds are the offered American price (vig included) — we use the implied
probability; the calibration panel can tune out the average vig over time.

Config via env: FANDUEL_HOST (default Ontario), FANDUEL_AK, FANDUEL_REGION.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from . import config
from .sgo import BATTER_COLUMNS, american_to_prob

DEFAULT_HOST = "sbapi.on.sportsbook.fanduel.ca"
DEFAULT_AK = "FhMFpcPWXMeyZxOx"
DEFAULT_REGION = "ON"
_UA = ("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36")

# FanDuel marketType -> (our normalized market, point/threshold as k-0.5).
FD_BATTER_MARKETS = {
    "TO_RECORD_A_RUN": (config.MARKET_R, 0.5),
    "TO_RECORD_2+_RUNS": (config.MARKET_R, 1.5),
    "TO_HIT_A_SINGLE": (config.MARKET_1B, 0.5),
    "TO_HIT_A_DOUBLE": (config.MARKET_2B, 0.5),
    "TO_HIT_A_TRIPLE": (config.MARKET_3B, 0.5),
    "TO_HIT_A_HOME_RUN": (config.MARKET_HR, 0.5),
    "TO_HIT_2+_HOME_RUNS": (config.MARKET_HR, 1.5),
    "TO_RECORD_AN_RBI": (config.MARKET_RBI, 0.5),
    "TO_RECORD_2+_RBIS": (config.MARKET_RBI, 1.5),
    "TO_RECORD_A_STOLEN_BASE": (config.MARKET_SB, 0.5),
    "TO_RECORD_2+_STOLEN_BASES": (config.MARKET_SB, 1.5),
    "PLAYER_TO_RECORD_A_HIT": (config.MARKET_HITS, 0.5),
    "PLAYER_TO_RECORD_2+_HITS": (config.MARKET_HITS, 1.5),
}

# abbr, full name, FanDuel logo slug.
_TEAMS = [
    ("AZ", "Arizona Diamondbacks", "arizona_diamondbacks"),
    ("ATL", "Atlanta Braves", "atlanta_braves"),
    ("BAL", "Baltimore Orioles", "baltimore_orioles"),
    ("BOS", "Boston Red Sox", "boston_red_sox"),
    ("CHC", "Chicago Cubs", "chicago_cubs"),
    ("CWS", "Chicago White Sox", "chicago_white_sox"),
    ("CIN", "Cincinnati Reds", "cincinnati_reds"),
    ("CLE", "Cleveland Guardians", "cleveland_guardians"),
    ("COL", "Colorado Rockies", "colorado_rockies"),
    ("DET", "Detroit Tigers", "detroit_tigers"),
    ("HOU", "Houston Astros", "houston_astros"),
    ("KC", "Kansas City Royals", "kansas_city_royals"),
    ("LAA", "Los Angeles Angels", "los_angeles_angels"),
    ("LAD", "Los Angeles Dodgers", "los_angeles_dodgers"),
    ("MIA", "Miami Marlins", "miami_marlins"),
    ("MIL", "Milwaukee Brewers", "milwaukee_brewers"),
    ("MIN", "Minnesota Twins", "minnesota_twins"),
    ("NYM", "New York Mets", "new_york_mets"),
    ("NYY", "New York Yankees", "new_york_yankees"),
    ("ATH", "Athletics", "athletics"),
    ("PHI", "Philadelphia Phillies", "philadelphia_phillies"),
    ("PIT", "Pittsburgh Pirates", "pittsburgh_pirates"),
    ("SD", "San Diego Padres", "san_diego_padres"),
    ("SF", "San Francisco Giants", "san_francisco_giants"),
    ("SEA", "Seattle Mariners", "seattle_mariners"),
    ("STL", "St. Louis Cardinals", "st_louis_cardinals"),
    ("TB", "Tampa Bay Rays", "tampa_bay_rays"),
    ("TEX", "Texas Rangers", "texas_rangers"),
    ("TOR", "Toronto Blue Jays", "toronto_blue_jays"),
    ("WSH", "Washington Nationals", "washington_nationals"),
]
_SLUG_ABBR = {slug: abbr for abbr, _n, slug in _TEAMS}
_NAME_ABBR = {name.lower(): abbr for abbr, name, _s in _TEAMS}


def _ak() -> str:
    return os.environ.get("FANDUEL_AK") or DEFAULT_AK


def _host() -> str:
    return os.environ.get("FANDUEL_HOST") or DEFAULT_HOST


def _region() -> str:
    return os.environ.get("FANDUEL_REGION") or DEFAULT_REGION


def configured() -> bool:
    return True  # public key; always available


def _et():
    from zoneinfo import ZoneInfo
    return ZoneInfo("America/New_York")


def _get(path: str, params: dict) -> dict:
    url = f"https://{_host()}/api/{path}?{urlencode(params)}"
    req = Request(url, headers={
        "User-Agent": _UA, "Accept": "application/json",
        "X-Sportsbook-Region": _region(),
        "Referer": f"https://{_host().split('.', 1)[1].replace('sportsbook.', '')}/",
    })
    with urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8"))


def _team_from_logo(url: str | None) -> str | None:
    if not url:
        return None
    slug = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return _SLUG_ABBR.get(slug)


def _matchup(name: str) -> tuple[str, str]:
    """'Away Team (P Name) @ Home Team (P Name)' -> (AWAY_ABBR, HOME_ABBR)."""
    def clean(side: str) -> str:
        side = side.split("(")[0].strip()
        return _NAME_ABBR.get(side.lower(), side)
    if " @ " in name:
        away, home = name.split(" @ ", 1)
        return clean(away), clean(home)
    return ("", "")


def fetch_events(date_et=None) -> list[dict]:
    """Today's (ET) pre-match MLB games from FanDuel."""
    d = _get("content-managed-page",
             {"page": "CUSTOM", "customPageId": "mlb", "_ak": _ak(), "timezone": "America/Toronto"})
    events = (d.get("attachments", {}) or {}).get("events", {}) or {}
    et = _et()
    today = date_et or datetime.now(et).date()
    now = datetime.now(timezone.utc)
    out = []
    for e in events.values():
        name = e.get("name") or ""
        if " @ " not in name:  # skip Futures / Player Awards / etc.
            continue
        try:
            start = datetime.fromisoformat((e.get("openDate") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if start.astimezone(et).date() != today or start <= now:
            continue
        out.append({"eventId": e.get("eventId"), "name": name, "start": start})
    return out


def fetch_event_batter_rows(event: dict) -> tuple[list[dict], dict]:
    d = _get("event-page", {"_ak": _ak(), "eventId": event["eventId"], "tab": "batter-props"})
    markets = (d.get("attachments", {}) or {}).get("markets", {}) or {}
    away, home = _matchup(event["name"])
    game = f"{away}@{home}"
    commence = event["start"].astimezone(_et()).strftime("%Y-%m-%d %H:%M")
    rows, teams = [], {}
    for m in markets.values():
        mapping = FD_BATTER_MARKETS.get(m.get("marketType"))
        if not mapping:
            continue
        market_key, point = mapping
        for r in (m.get("runners") or []):
            if not r.get("isPlayerSelection") or r.get("runnerStatus") != "ACTIVE":
                continue
            name = r.get("runnerName")
            odds = (((r.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOdds"))
            prob = american_to_prob(odds)
            if name and prob is not None:
                rows.append({
                    "player": name, "normalized_market_key": market_key, "point": point,
                    "over_prob": prob, "game": game, "commence_time_local": commence,
                    "away_team": away, "home_team": home,
                })
                tm = _team_from_logo(r.get("secondaryLogo"))
                if tm:
                    teams[name] = tm
    return rows, teams


def pull_batters(date_et=None) -> dict:
    """Fetch every today's pre-match game's batter props -> {batter_df, player_teams, n_games}."""
    events = fetch_events(date_et)
    rows, teams = [], {}
    for e in events:
        try:
            r, t = fetch_event_batter_rows(e)
            rows.extend(r)
            teams.update(t)
        except Exception:  # noqa: BLE001 - skip a bad game, keep the rest
            continue
    return {"batter_df": pd.DataFrame(rows, columns=BATTER_COLUMNS),
            "player_teams": teams, "n_games": len(events)}
