"""Tests for opponent tendency extraction and scouting reports."""

import pytest

from dfs import contest, draft, registry, scouting, tendencies
from dfs import slate as slate_mod
from dfs.db import connect


def _add_batter(conn, slate_id, name, team, group, proj):
    pid = registry.upsert_player(conn, name, positions=group, team=team)
    conn.execute(
        "INSERT INTO batter_projections (slate_id, player_id, proj_pts, flags_json)"
        " VALUES (?, ?, ?, '[]')",
        (slate_id, pid, proj),
    )
    conn.commit()
    return pid


def _add_pitcher(conn, slate_id, name, proj, team="FA"):
    pid = registry.upsert_player(conn, name, positions="P", team=team)
    conn.execute(
        "INSERT OR REPLACE INTO pitcher_projections (slate_id, player_id, proj_pts) VALUES (?, ?, ?)",
        (slate_id, pid, proj),
    )
    conn.commit()
    return pid


@pytest.fixture()
def stacked_conn(tmp_path):
    """A rival who stacks LAD hitters across two identical drafts."""
    conn = connect(tmp_path / "tend.db")
    slate_id = slate_mod.create_slate(conn, "2026-06-29")
    p = {
        "LAD A": _add_batter(conn, slate_id, "LAD A", "LAD", "OF", 8.0),
        "LAD B": _add_batter(conn, slate_id, "LAD B", "LAD", "IF", 7.0),
        "LAD C": _add_batter(conn, slate_id, "LAD C", "LAD", "OF", 6.0),
        "NYY A": _add_batter(conn, slate_id, "NYY A", "NYY", "IF", 7.5),
        "NYY B": _add_batter(conn, slate_id, "NYY B", "NYY", "OF", 6.5),
        "BOS A": _add_batter(conn, slate_id, "BOS A", "BOS", "IF", 6.0),
        "Ace One": _add_pitcher(conn, slate_id, "Ace One", 25.0),
        "Ace Two": _add_pitcher(conn, slate_id, "Ace Two", 20.0),
    }
    grid = {(c.round_number, c.seat): c for c in draft.generate_grid(2, 3)}

    def cell(r, s, name, slot):
        c = grid[(r, s)]
        return {"overall_pick_number": c.overall_pick_number, "round_number": c.round_number,
                "slot_in_round": c.slot_in_round, "player_id": p[name], "roster_slot": slot}

    entries = [
        {"drafter_name": "Me", "is_me": 1, "draft_slot": 1, "picks": [
            cell(1, 1, "Ace One", "P"), cell(2, 1, "NYY A", "IF"), cell(3, 1, "BOS A", "IF")]},
        {"drafter_name": "Rival", "is_me": 0, "draft_slot": 2, "picks": [
            cell(1, 2, "LAD A", "OF"), cell(2, 2, "LAD B", "IF"), cell(3, 2, "LAD C", "OF")]},
    ]
    # Two contests on the same slate so player affinity accumulates.
    contest.save_contest(conn, slate_id, entries, site="UD", my_draft_slot=1)
    contest.save_contest(conn, slate_id, entries, site="UD", my_draft_slot=1)
    yield conn


def test_compute_writes_tendencies(stacked_conn):
    n = tendencies.compute_all_tendencies(stacked_conn)
    assert n >= 1
    t = tendencies.load_tendencies(stacked_conn, "Rival")
    assert t is not None
    assert t["n_contests"] == 2


def test_team_affinity_detects_lad_bias(stacked_conn):
    tendencies.compute_all_tendencies(stacked_conn)
    t = tendencies.load_tendencies(stacked_conn, "Rival")
    lad = next((x for x in t["team_affinity"] if x["team"] == "LAD"), None)
    assert lad is not None
    assert lad["share"] == pytest.approx(1.0)          # every Rival pick is LAD
    assert lad["ratio"] >= 1.5                          # well above league share


def test_stacking_coefficient_above_baseline(stacked_conn):
    tendencies.compute_all_tendencies(stacked_conn)
    t = tendencies.load_tendencies(stacked_conn, "Rival")
    assert t["stacking"] is not None
    assert t["stacking"]["coefficient"] >= 1.3          # stacks more than chance


def test_player_affinity_lists_repeated_targets(stacked_conn):
    tendencies.compute_all_tendencies(stacked_conn)
    t = tendencies.load_tendencies(stacked_conn, "Rival")
    names = {a["name"] for a in t["player_affinity"]}
    assert "LAD A" in names


def test_value_metrics_present(stacked_conn):
    tendencies.compute_all_tendencies(stacked_conn)
    t = tendencies.load_tendencies(stacked_conn, "Rival")
    assert t["value"] is not None
    assert t["value"]["avg_pos_rank"] >= 1.0


def test_scouting_report_reads_stacking_and_teams(stacked_conn):
    tendencies.compute_all_tendencies(stacked_conn)
    t = tendencies.load_tendencies(stacked_conn, "Rival")
    report = scouting.scouting_report(t)
    assert report["has_data"]
    text = " ".join(report["summary"]).lower()
    assert "stack" in text
    assert "lad" in text


# --- Scouting template unit tests (no DB) -----------------------------------
def test_scouting_handles_empty():
    report = scouting.scouting_report(None)
    assert report["has_data"] is False


def test_scouting_pitching_opener_and_diversify():
    t = {
        "n_picks": 30, "n_contests": 3,
        "round_buckets": {"R1-2": {"P": 0.5, "IF": 0.3, "OF": 0.2, "HT": 0.0, "n": 6}},
        "pitchers": {"per_draft": 3.0, "first_pitcher_avg_round": 1.0},
        "value": {"avg_overall_rank": 3.0, "avg_overall_pct": 0.1, "avg_pos_rank": 1.5,
                  "avg_pos_pct": 0.1, "n": 30},
        "player_affinity": [], "team_affinity": [],
        "stacking": {"coefficient": 0.5, "observed_rate": 0.05, "baseline_rate": 0.1, "opportunities": 10},
    }
    text = " ".join(scouting.scouting_report(t)["summary"]).lower()
    assert "pitching" in text
    assert "diversif" in text
