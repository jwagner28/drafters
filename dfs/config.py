"""Configuration: scoring weights, baselines, and position groupings.

Scoring weights are configurable; the values here are the defaults from the
build brief. The projection engine accepts an override dict, so nothing here is
hardcoded into the math.
"""

from __future__ import annotations

# --- Scoring weights (points per event) -------------------------------------
# R=2, 1B=2, 2B=4, 3B=6, HR=8, RBI=2, BB=2, HBP=2, SB=3. No strikeout penalty.
DEFAULT_SCORING: dict[str, float] = {
    "R": 2.0,
    "1B": 2.0,
    "2B": 4.0,
    "3B": 6.0,
    "HR": 8.0,
    "RBI": 2.0,
    "BB": 2.0,
    "HBP": 2.0,
    "SB": 3.0,
}

# --- Flat baselines (no market exists for these, so we assume a league rate) -
BB_RATE = 0.08     # only used if there's no walk prop (SGO provides real ones)
HBP_RATE = 0.011

# --- Multi-hit uplift -------------------------------------------------------
# SGO posts a single 0.5 line per batting stat, so the raw over_prob is P(X>=1),
# which undercounts 2+ games for common stats. Recover a fuller expectation by
# blending toward the Poisson estimate -ln(1-p). Applied only to common stats
# and only when a stat has a single 0.5 rung.
UPLIFT_FACTOR = 0.5
UPLIFT_STATS = {"r", "1b", "rbi"}

# --- Pitcher scoring (points per event; IP is per inning) -------------------
DEFAULT_PITCHER_SCORING: dict[str, float] = {
    "IP": 2.0, "W": 4.0, "K": 2.0, "H": -0.5, "ER": -2.0,
    "BB": -0.5, "HB": -0.5, "CG": 2.0, "NO_HITTER": 5.0, "PERFECT": 10.0,
}
HB_RATE_PER_IP = 0.045   # hit-batsmen baseline per inning (no prop available)

# --- Auto-flag thresholds ---------------------------------------------------
# A game where most batters project near zero is probably already in progress
# (lines pulled). A game where many batters project very high is probably an
# inflated/garbage line set. Both are surfaced as warnings, never dropped.
NEAR_ZERO_THRESHOLD = 0.5     # proj below this counts as "near zero"
NEAR_ZERO_SHARE = 0.6         # fraction of a game's batters that must be near zero
INFLATED_THRESHOLD = 9.0      # proj at/above this counts as "inflated"
INFLATED_MIN_COUNT = 3        # this many inflated batters flags the game

# --- Market keys ------------------------------------------------------------
MARKET_HR = "batter_home_runs"
MARKET_2B = "batter_doubles"
MARKET_3B = "batter_triples"
MARKET_1B = "batter_singles"
MARKET_HITS = "batter_hits"
MARKET_R = "batter_runs_scored"
MARKET_RBI = "batter_rbis"
MARKET_SB = "batter_stolen_bases"
MARKET_BB = "batter_walks"

VALID_MARKETS = {
    MARKET_HR, MARKET_2B, MARKET_3B, MARKET_1B,
    MARKET_HITS, MARKET_R, MARKET_RBI, MARKET_SB, MARKET_BB,
}

# --- Positions --------------------------------------------------------------
# A player's eligibility is one OR MORE of these three groups. Many batters are
# eligible at both IF and OF; Ohtani-types are eligible at both IF and P.
POSITION_GROUPS = ["IF", "OF", "P"]
VALID_POSITION_GROUPS = set(POSITION_GROUPS)

# Map granular positions (from a CSV, OCR, etc.) onto the three groups so any
# upstream source still resolves. OF = {LF, CF, RF}, IF = {C, 1B, 2B, 3B, SS, DH}.
GRANULAR_TO_GROUP: dict[str, str] = {
    "LF": "OF", "CF": "OF", "RF": "OF", "OF": "OF",
    "C": "IF", "1B": "IF", "2B": "IF", "3B": "IF", "SS": "IF", "DH": "IF",
    "IF": "IF", "UT": "IF", "UTIL": "IF",
    "P": "P", "SP": "P", "RP": "P",
}

# Positions offered in the UI when assigning a player (multi-select of these).
ASSIGNABLE_POSITIONS = list(POSITION_GROUPS)


def normalize_position(position: str | None) -> str | None:
    """Map a single raw position string onto IF / OF / P, or None if unknown."""
    if not position:
        return None
    return GRANULAR_TO_GROUP.get(position.strip().upper())


def normalize_positions(positions) -> list[str]:
    """Normalize a position (str) or list of positions into a unique IF/OF/P list.

    Output order is always canonical: IF, OF, P.
    """
    if positions is None:
        return []
    if isinstance(positions, str):
        positions = [positions]
    found: set[str] = set()
    for p in positions:
        g = normalize_position(p)
        if g:
            found.add(g)
    return [g for g in POSITION_GROUPS if g in found]
