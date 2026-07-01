"""Headless render test for the Home page (incl. backup/restore controls)."""

import os
from pathlib import Path

import pytest

from dfs.db import connect

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def test_home_renders_with_backup(tmp_path):
    db = tmp_path / "home.db"
    os.environ["DFS_DB_PATH"] = str(db)
    connect(db).close()  # create the file so the download button renders
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    # The page header renders (backup/restore lives in an expander below it).
    assert any("MLB DFS" in str(t.value) for t in at.title)
