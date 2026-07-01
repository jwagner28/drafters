"""Tests for the Turso cloud-sync orchestration (no real Turso needed)."""

import os
import time

import pytest

from dfs import cloudsync
from dfs.db import connect


@pytest.fixture(autouse=True)
def _reset_module_state():
    cloudsync._pulled = False
    cloudsync._autosync_started = False
    cloudsync._last_push_mtime = 0.0
    yield


def test_pull_restores_when_local_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(cloudsync, "configured", lambda: True)
    monkeypatch.setattr(cloudsync, "download", lambda: b"CLOUD-BYTES")
    p = tmp_path / "x.db"
    cloudsync.pull_once(p)
    assert p.read_bytes() == b"CLOUD-BYTES"


def test_pull_noop_when_local_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(cloudsync, "configured", lambda: True)
    monkeypatch.setattr(cloudsync, "download", lambda: b"CLOUD")
    p = tmp_path / "x.db"
    p.write_bytes(b"LOCAL")
    cloudsync.pull_once(p)
    assert p.read_bytes() == b"LOCAL"  # existing local data is not clobbered


def test_pull_runs_only_once(monkeypatch, tmp_path):
    calls = {"n": 0}

    def _dl():
        calls["n"] += 1
        return b"DATA"

    monkeypatch.setattr(cloudsync, "configured", lambda: True)
    monkeypatch.setattr(cloudsync, "download", _dl)
    p = tmp_path / "x.db"
    cloudsync.pull_once(p)
    p.unlink()
    cloudsync.pull_once(p)  # guarded: should not download again
    assert calls["n"] == 1


def test_push_uploads_only_on_change(monkeypatch, tmp_path):
    uploaded = {}
    monkeypatch.setattr(cloudsync, "configured", lambda: True)
    monkeypatch.setattr(cloudsync, "upload", lambda data: uploaded.__setitem__("data", data))
    monkeypatch.setattr(cloudsync, "_snapshot_bytes", lambda path: path.read_bytes())

    p = tmp_path / "x.db"
    p.write_bytes(b"V1")
    assert cloudsync.push_if_changed(p) is True
    assert uploaded["data"] == b"V1"

    uploaded.clear()
    assert cloudsync.push_if_changed(p) is False  # unchanged -> no upload
    assert "data" not in uploaded

    p.write_bytes(b"V2")
    os.utime(p, (time.time() + 10, time.time() + 10))  # ensure mtime advances
    assert cloudsync.push_if_changed(p) is True
    assert uploaded["data"] == b"V2"


def test_not_configured_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(cloudsync, "configured", lambda: False)
    p = tmp_path / "x.db"
    p.write_bytes(b"LOCAL")
    cloudsync.pull_once(p)                 # no error, no change
    assert cloudsync.push_if_changed(p) is False
    assert p.read_bytes() == b"LOCAL"


def test_force_pull_overwrites_local(monkeypatch, tmp_path):
    monkeypatch.setattr(cloudsync, "configured", lambda: True)
    monkeypatch.setattr(cloudsync, "download", lambda: b"CLOUD")
    p = tmp_path / "x.db"
    p.write_bytes(b"LOCAL")
    assert cloudsync.force_pull(p) is True
    assert p.read_bytes() == b"CLOUD"


def test_snapshot_is_valid_sqlite(tmp_path):
    p = tmp_path / "real.db"
    connect(p).close()  # create a real schema'd DB
    data = cloudsync._snapshot_bytes(p)
    assert data.startswith(b"SQLite format 3\x00")
