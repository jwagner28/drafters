"""Tests for the SportsGameOdds integration (parsing + projections; no network)."""

import pandas as pd
import pytest

from dfs import projections, sgo


def _odd(stat, pid, bt, side, fair, line=None):
    o = {"statID": stat, "statEntityID": pid, "playerID": pid, "betTypeID": bt,
         "sideID": side, "fairOdds": fair, "opposingOddID": None}
    if line is not None:
        o["bookOverUnder"] = str(line)
    return o


def _fixture_event():
    """One event: a batter (BAL) with clean +/- odds, and a pitcher (CWS)."""
    odds = {}

    def add(o):
        odds[f"{o['statID']}-{o['statEntityID']}-{o['sideID']}"] = o

    b = "TB_1"
    for stat, fair in [("points", "+100"), ("batting_singles", "+100"),
                       ("batting_doubles", "+400"), ("batting_triples", "+900"),
                       ("batting_homeRuns", "+400"), ("batting_RBI", "+100"),
                       ("batting_basesOnBalls", "+100"), ("batting_stolenBases", "+900")]:
        add(_odd(stat, b, "ou", "over", fair, line=0.5))

    p = "TP_1"
    for stat, line in [("pitching_outs", 15.5), ("pitching_strikeouts", 6.5),
                       ("pitching_earnedRuns", 2.5), ("pitching_hits", 5.5),
                       ("pitching_basesOnBalls", 2.5)]:
        add(_odd(stat, p, "ou", "over", "+100", line=line))
    add(_odd("pitching_win", p, "yn", "yes", "+200"))

    return {
        "teams": {"home": {"teamID": "BAL", "names": {"short": "BAL"}},
                  "away": {"teamID": "CWS", "names": {"short": "CWS"}}},
        "status": {"started": False, "startsAt": "2026-07-01T20:00:00.000Z"},
        "players": {b: {"playerID": b, "teamID": "BAL", "name": "Test Batter"},
                    p: {"playerID": p, "teamID": "CWS", "name": "Test Pitcher"}},
        "odds": odds,
    }


def test_american_to_prob():
    assert sgo.american_to_prob("+100") == 0.5
    assert sgo.american_to_prob("+400") == 0.2
    assert sgo.american_to_prob("-150") == pytest.approx(0.6, abs=1e-3)
    assert sgo.american_to_prob("bad") is None


def test_parse_event_batter_rows():
    parsed = sgo.parse_event(_fixture_event())
    rows = parsed["batter_rows"]
    markets = {r["normalized_market_key"] for r in rows}
    assert "batter_walks" in markets          # walks now a real market
    assert "batter_runs_scored" in markets    # SGO 'points' -> runs
    walk = next(r for r in rows if r["normalized_market_key"] == "batter_walks")
    assert walk["over_prob"] == 0.5 and walk["point"] == 0.5
    assert parsed["player_teams"]["Test Batter"] == "BAL"
    assert rows[0]["game"] == "CWS@BAL"


def test_build_slate_batter_projection_with_uplift_and_walks():
    slate = sgo.build_slate([_fixture_event()])
    projs = projections.compute_projections(slate["batter_df"], uplift=True)
    p = next(x for x in projs if x.player == "Test Batter")
    # Hand-computed with uplift 0.5 on R/1B/RBI and real walks (E[BB]=0.5).
    assert p.e_bb == 0.5
    assert p.e_r == pytest.approx(0.5966, abs=1e-3)   # uplifted from 0.5
    assert p.proj_pts == pytest.approx(7.90, abs=0.02)


def test_pitcher_projection_from_fixture():
    slate = sgo.build_slate([_fixture_event()])
    assert len(slate["pitchers"]) == 1
    pit = slate["pitchers"][0]
    assert pit["name"] == "Test Pitcher" and pit["team"] == "CWS"
    assert pit["components"]["ip"] == pytest.approx(5.17, abs=0.01)
    assert pit["proj_pts"] == pytest.approx(15.55, abs=0.05)


def test_compute_pitcher_projection_direct():
    lines = {"outs": 15.5, "k": 6.5, "er": 2.5, "hits": 5.5, "bb": 2.5, "win_prob": 1 / 3}
    proj, comps = projections.compute_pitcher_projection(lines)
    assert proj == pytest.approx(15.55, abs=0.05)
    assert comps["k"] == 6.5


def test_build_slate_columns():
    slate = sgo.build_slate([_fixture_event()])
    assert list(slate["batter_df"].columns) == sgo.BATTER_COLUMNS
