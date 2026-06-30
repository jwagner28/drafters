"""Tests for the temporary draft-board data loaders."""

from pathlib import Path

import pandas as pd
import pytest

from dfs import draftboard, registry
from dfs import slate as slate_mod
from dfs.db import connect
from dfs.projections import compute_projections

SAMPLE_CSV = Path(__file__).resolve().parents[1] / "sample_data" / "sample_props.csv"


@pytest.fixture()
def slate_conn(tmp_path):
    conn = connect(tmp_path / "board.db")
    df = pd.read_csv(SAMPLE_CSV)
    projs = compute_projections(df)
    # Assign positions, incl. a flex bat and an Ohtani-type.
    registry.assign_positions(conn, "Aaron Judge", ["OF"])
    registry.assign_positions(conn, "Mookie Betts", ["IF", "OF"])
    registry.assign_positions(conn, "Freddie Freeman", ["IF"])
    registry.assign_positions(conn, "Shohei Ohtani", ["IF"])
    slate_id = slate_mod.create_slate(conn, "2026-06-29")
    slate_mod.save_batter_projections(conn, slate_id, projs)
    slate_mod.save_pitcher_projection(conn, slate_id, "Shohei Ohtani", 9.0)
    slate_mod.save_pitcher_projection(conn, slate_id, "Gerrit Cole", 18.5)
    yield conn, slate_id
    conn.close()


def test_board_groups_and_sorting(slate_conn):
    conn, slate_id = slate_conn
    board, unassigned = draftboard.load_board_from_slate(conn, slate_id)

    # Each group sorted by Proj descending.
    for group in draftboard.GROUPS:
        projs = [r["Proj"] for r in board[group]]
        assert projs == sorted(projs, reverse=True)

    of_names = [r["Name"] for r in board["OF"]]
    if_names = [r["Name"] for r in board["IF"]]
    p_names = [r["Name"] for r in board["P"]]

    # Flex bat shows up in both IF and OF.
    assert "Mookie Betts" in of_names and "Mookie Betts" in if_names
    # OF-only player not in IF.
    assert "Aaron Judge" in of_names and "Aaron Judge" not in if_names
    # Pitchers come from pitcher_projections.
    assert "Gerrit Cole" in p_names


def test_ohtani_appears_in_if_and_p(slate_conn):
    conn, slate_id = slate_conn
    board, _ = draftboard.load_board_from_slate(conn, slate_id)
    assert "Shohei Ohtani" in [r["Name"] for r in board["IF"]]
    assert "Shohei Ohtani" in [r["Name"] for r in board["P"]]


def test_unassigned_batters_reported(tmp_path):
    conn = connect(tmp_path / "u.db")
    df = pd.read_csv(SAMPLE_CSV)
    projs = compute_projections(df)
    slate_id = slate_mod.create_slate(conn, "2026-06-29")
    slate_mod.save_batter_projections(conn, slate_id, projs)  # nobody assigned
    board, unassigned = draftboard.load_board_from_slate(conn, slate_id)
    assert len(unassigned) == len(projs)
    assert all(not board[g] for g in ["IF", "OF"])
    conn.close()


def test_load_board_from_csv_parses_columns_and_multipos():
    raw = pd.DataFrame(
        [
            {"Name": "Flex Guy", "Pos": "IF/OF", "Proj": 7.5, "Team": "NYY"},
            {"Name": "Just OF", "Pos": "CF", "Proj": 9.1, "Team": "LAD"},
            {"Name": "Ace", "Pos": "P", "Proj": 20.0, "Team": "ATL"},
        ]
    )
    board = draftboard.load_board_from_csv(raw)
    assert "Flex Guy" in [r["Name"] for r in board["IF"]]
    assert "Flex Guy" in [r["Name"] for r in board["OF"]]
    # Granular CF folded into OF.
    assert "Just OF" in [r["Name"] for r in board["OF"]]
    assert board["OF"][0]["Name"] == "Just OF"  # higher proj sorts first
    assert board["P"][0]["Team"] == "ATL"


def test_load_board_from_csv_missing_columns_raises():
    with pytest.raises(ValueError):
        draftboard.load_board_from_csv(pd.DataFrame([{"Name": "X", "Proj": 1.0}]))
