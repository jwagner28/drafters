"""Tests for the projection engine (the ladder method)."""

import pandas as pd
import pytest

from dfs import config
from dfs.projections import (
    compute_expected_components,
    compute_projections,
    project_from_components,
)


def _row(player, market, point, over_prob, game="G1", t="2026-06-29 19:00"):
    return {
        "player": player,
        "normalized_market_key": market,
        "point": point,
        "over_prob": over_prob,
        "game": game,
        "commence_time_local": t,
        "away_team": "AAA",
        "home_team": "BBB",
    }


def test_counting_stat_expected_value_is_sum_of_rungs():
    # E[X] = sum of over_prob across rungs (P(X>=1)+P(X>=2)+...).
    rungs = {
        config.MARKET_HR: [
            {"point": 0.5, "over_prob": 0.30},
            {"point": 1.5, "over_prob": 0.08},
            {"point": 2.5, "over_prob": 0.01},
        ]
    }
    comps = compute_expected_components(rungs)
    assert comps["e_hr"] == pytest.approx(0.39)


def test_singles_from_explicit_market_sums_rungs():
    rungs = {
        config.MARKET_1B: [
            {"point": 0.5, "over_prob": 0.41},
            {"point": 1.5, "over_prob": 0.07},
        ]
    }
    comps = compute_expected_components(rungs)
    # Explicit singles market with multiple rungs -> ladder sum E[1B].
    assert comps["e_1b"] == pytest.approx(0.48)


def test_singles_residual_when_no_singles_market():
    rungs = {
        config.MARKET_HITS: [{"point": 0.5, "over_prob": 0.62}],
        config.MARKET_HR: [{"point": 0.5, "over_prob": 0.34}],
        config.MARKET_2B: [{"point": 0.5, "over_prob": 0.22}],
        # no triples market -> treated as 0
    }
    comps = compute_expected_components(rungs)
    assert comps["e_1b"] == pytest.approx(0.62 - 0.34 - 0.22)


def test_singles_residual_clamped_at_zero():
    rungs = {
        config.MARKET_HITS: [{"point": 0.5, "over_prob": 0.20}],
        config.MARKET_HR: [{"point": 0.5, "over_prob": 0.30}],
    }
    comps = compute_expected_components(rungs)
    assert comps["e_1b"] == 0.0


def test_project_from_components_known_value():
    # Hand-computed against the default scoring + baselines.
    comps = {
        "e_r": 0.58, "e_1b": 0.06, "e_2b": 0.22, "e_3b": 0.0,
        "e_hr": 0.40, "e_rbi": 0.76, "e_sb": 0.05,
    }
    # 2*.58 + 2*.06 + 4*.22 + 6*0 + 8*.40 + 2*.76 + 2*.08 + 2*.011 + 3*.05
    assert project_from_components(comps) == 7.21


def test_full_pipeline_known_player_judge():
    """Aaron Judge from the sample data -> 7.21 (residual singles path)."""
    rows = [
        _row("Aaron Judge", config.MARKET_HR, 0.5, 0.34),
        _row("Aaron Judge", config.MARKET_HR, 1.5, 0.06),
        _row("Aaron Judge", config.MARKET_2B, 0.5, 0.22),
        _row("Aaron Judge", config.MARKET_HITS, 0.5, 0.62),
        _row("Aaron Judge", config.MARKET_HITS, 1.5, 0.24),
        _row("Aaron Judge", config.MARKET_R, 0.5, 0.58),
        _row("Aaron Judge", config.MARKET_RBI, 0.5, 0.55),
        _row("Aaron Judge", config.MARKET_RBI, 1.5, 0.21),
        _row("Aaron Judge", config.MARKET_SB, 0.5, 0.05),
    ]
    projs = compute_projections(pd.DataFrame(rows))
    assert len(projs) == 1
    assert projs[0].proj_pts == 7.21


def test_full_pipeline_known_player_betts_singles_market():
    """Mookie Betts (explicit singles market) -> 6.00."""
    rows = [
        _row("Mookie Betts", config.MARKET_HR, 0.5, 0.18),
        _row("Mookie Betts", config.MARKET_2B, 0.5, 0.24),
        _row("Mookie Betts", config.MARKET_3B, 0.5, 0.02),
        _row("Mookie Betts", config.MARKET_1B, 0.5, 0.41),
        _row("Mookie Betts", config.MARKET_R, 0.5, 0.56),
        _row("Mookie Betts", config.MARKET_RBI, 0.5, 0.44),
        _row("Mookie Betts", config.MARKET_SB, 0.5, 0.16),
    ]
    projs = compute_projections(pd.DataFrame(rows))
    assert projs[0].proj_pts == 6.00


def test_dedupe_keeps_highest_projection():
    rows = [
        _row("Dup Player", config.MARKET_HR, 0.5, 0.10),  # lower
        _row("dup player", config.MARKET_HR, 0.5, 0.50),  # higher, different case
    ]
    projs = compute_projections(pd.DataFrame(rows))
    assert len(projs) == 1
    # 0.50 HR -> 8*0.5 = 4.0 plus BB/HBP baseline, rounded to 2 decimals.
    assert projs[0].proj_pts == round(8 * 0.50 + 2 * 0.08 + 2 * 0.011, 2)


def test_custom_scoring_weights_apply():
    comps = {"e_r": 1.0, "e_1b": 0, "e_2b": 0, "e_3b": 0, "e_hr": 0, "e_rbi": 0, "e_sb": 0}
    scoring = dict(config.DEFAULT_SCORING)
    scoring["R"] = 10.0
    # 10*1 + BB/HBP baseline
    assert project_from_components(comps, scoring) == round(10 + 2 * 0.08 + 2 * 0.011, 2)


def test_inflated_game_flag():
    rows = []
    for i in range(4):
        # Each player gets ~2 HR expected -> ~16 pts, well above 9.
        rows.append(_row(f"Big Bopper {i}", config.MARKET_HR, 0.5, 1.0))
        rows.append(_row(f"Big Bopper {i}", config.MARKET_HR, 1.5, 1.0))
    projs = compute_projections(pd.DataFrame(rows))
    assert all("inflated" in p.flags for p in projs)


def test_in_progress_game_flag():
    rows = [
        _row("Zero One", config.MARKET_HR, 0.5, 0.0),
        _row("Zero Two", config.MARKET_HR, 0.5, 0.0),
        _row("Zero Three", config.MARKET_HR, 0.5, 0.0),
    ]
    projs = compute_projections(pd.DataFrame(rows))
    assert all("in_progress" in p.flags for p in projs)


def test_missing_required_column_raises():
    df = pd.DataFrame([{"player": "X", "point": 0.5, "over_prob": 0.5}])
    with pytest.raises(ValueError):
        compute_projections(df)
