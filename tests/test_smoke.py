"""End-to-end smoke test: sample CSV -> projections -> roster total."""

from pathlib import Path

import pandas as pd

from dfs.db import connect
from dfs.projections import compute_projections
from dfs import slate as slate_mod

SAMPLE_CSV = Path(__file__).resolve().parents[1] / "sample_data" / "sample_props.csv"


def test_sample_csv_projects_and_sums():
    df = pd.read_csv(SAMPLE_CSV)
    projs = compute_projections(df)
    by_name = {p.player: p.proj_pts for p in projs}

    # Hand-computed against the ladder method + default scoring.
    assert by_name["Aaron Judge"] == 7.21      # residual-singles path
    assert by_name["Mookie Betts"] == 6.00     # explicit singles market
    assert by_name["Freddie Freeman"] == 5.40

    # A 3-man "roster" total = sum of rounded projections.
    roster = ["Aaron Judge", "Mookie Betts", "Freddie Freeman"]
    total = round(sum(by_name[name] for name in roster), 2)
    assert total == 18.61


def test_sample_csv_round_trips_through_db(tmp_path):
    df = pd.read_csv(SAMPLE_CSV)
    projs = compute_projections(df)

    conn = connect(tmp_path / "smoke.db")
    slate_id = slate_mod.create_slate(conn, "2026-06-29", notes="smoke")
    slate_mod.save_batter_projections(conn, slate_id, projs)

    rows = slate_mod.load_slate_projections(conn, slate_id)
    assert len(rows) == len(projs)
    # Highest projection should be sorted first on load.
    assert rows[0]["proj_pts"] == max(p.proj_pts for p in projs)
    conn.close()
