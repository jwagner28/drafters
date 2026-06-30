"""Seed / demo script.

Builds a fresh demo SQLite database from the sample props CSV: computes
projections, creates a slate, assigns demo positions, and saves everything.

Run:  python scripts/seed.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# Make `dfs` importable when running this file directly (without install).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dfs import registry, slate as slate_mod  # noqa: E402
from dfs.db import connect  # noqa: E402
from dfs.projections import compute_projections, projections_to_dataframe  # noqa: E402

DEMO_POSITIONS = {
    "Aaron Judge": ["OF"],
    "Mookie Betts": ["IF", "OF"],      # flex bat: eligible at both
    "Freddie Freeman": ["IF"],
    "Rafael Devers": ["IF"],
    "Bobby Witt Jr.": ["IF"],
    "Jose Ramirez": ["IF"],
    "Shohei Ohtani": ["IF", "P"],      # Ohtani-type: hitter and pitcher
}

DEMO_PITCHERS = [
    ("Gerrit Cole", 18.5),
    ("Logan Webb", 16.0),
]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    db_path = root / "data" / "demo.db"
    if db_path.exists():
        db_path.unlink()  # fresh demo each run

    conn = connect(db_path)
    props = pd.read_csv(root / "sample_data" / "sample_props.csv")

    projections = compute_projections(props)
    print("Computed projections:")
    print(projections_to_dataframe(projections).to_string(index=False))

    # Assign demo positions so nothing is left in the queue.
    for name, positions in DEMO_POSITIONS.items():
        registry.assign_positions(conn, name, positions)

    slate_id = slate_mod.create_slate(conn, notes="demo slate")
    slate_mod.save_batter_projections(conn, slate_id, projections)
    for name, pts in DEMO_PITCHERS:
        slate_mod.save_pitcher_projection(conn, slate_id, name, pts)

    rows = slate_mod.load_slate_projections(conn, slate_id)
    print(f"\nSaved slate #{slate_id} with {len(rows)} batters and {len(DEMO_PITCHERS)} pitchers.")
    print(f"Demo DB written to: {db_path}")


if __name__ == "__main__":
    main()
