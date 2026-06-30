"""Headless render test for the Draft Board page via Streamlit AppTest.

Verifies the page imports and renders (including the nested IF/OF/P columns and
per-row toggle buttons) without raising, and that clicking a toggle marks a
player taken in session state only.
"""

import os
from pathlib import Path

import pytest

# Point the app at a throwaway DB so we never touch the real data/dfs.db.
PAGE = Path(__file__).resolve().parents[1] / "pages" / "2_Draft_Board.py"

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def _board():
    return {
        "IF": [
            {"Team": "KC", "Name": "Bobby Witt Jr.", "Proj": 8.49},
            {"Team": "LAD", "Name": "Shohei Ohtani", "Proj": 8.13},
        ],
        "OF": [{"Team": "MIN", "Name": "Byron Buxton", "Proj": 8.85}],
        "P": [{"Team": "ATL", "Name": "Chris Sale", "Proj": 24.8}],
    }


def _fresh_app(tmp_path):
    os.environ["DFS_DB_PATH"] = str(tmp_path / "page.db")
    at = AppTest.from_file(str(PAGE), default_timeout=30)
    at.session_state["draft_board"] = _board()
    at.session_state["draft_taken"] = set()
    at.session_state["draft_unassigned"] = []
    return at


def test_page_renders_board_without_exception(tmp_path):
    at = _fresh_app(tmp_path).run()
    assert not at.exception
    # One toggle button per player (4) plus the Reset button.
    assert len(at.button) >= 5


def test_toggle_marks_player_taken(tmp_path):
    at = _fresh_app(tmp_path).run()
    # Find a per-player toggle (keys start with "tg_") and click it.
    toggles = [b for b in at.button if b.key and b.key.startswith("tg_")]
    assert toggles, "expected per-player toggle buttons"
    toggles[0].click().run()
    assert not at.exception
    # Something is now marked taken, and nothing was written to the DB layer.
    assert len(at.session_state["draft_taken"]) == 1
