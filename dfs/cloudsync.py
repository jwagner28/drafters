"""Free durable persistence via Turso (libSQL) — without changing the app's DB.

Streamlit Community Cloud has an ephemeral filesystem, so the local SQLite file
is wiped on every reboot/redeploy. To keep data for free, we store a copy of the
whole SQLite file in a free **Turso** database and sync it:

* **pull_once** — on process start, if there's no local DB yet, download the
  saved copy from Turso and write it. (Restores your data after a redeploy.)
* **push_if_changed** — when the local DB file has changed since the last push,
  upload it to Turso. (Saves your data.)

All app logic keeps using plain stdlib `sqlite3` on the local file, so nothing
else changes. If Turso isn't configured, every function is a no-op and the app
runs purely locally.

Configuration (either environment variables or Streamlit **secrets**):
    TURSO_DATABASE_URL   e.g. libsql://your-db-your-org.turso.io
    TURSO_AUTH_TOKEN     a database token from `turso db tokens create`
"""

from __future__ import annotations

import base64
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

_ENV_URL = "TURSO_DATABASE_URL"
_ENV_TOKEN = "TURSO_AUTH_TOKEN"

_lock = threading.Lock()
_pulled = False
_autosync_started = False
_last_push_mtime = 0.0


def _snapshot_bytes(path: Path) -> bytes:
    """A consistent byte image of the SQLite DB.

    Uses sqlite3.Connection.serialize() (Python 3.11+) so we never upload a
    half-written file; falls back to a raw read if unavailable.
    """
    try:
        conn = sqlite3.connect(str(path))
        try:
            return bytes(conn.serialize())
        finally:
            conn.close()
    except Exception:
        return path.read_bytes()


# --- configuration ----------------------------------------------------------
def _creds() -> tuple[str, str] | None:
    url = os.environ.get(_ENV_URL)
    token = os.environ.get(_ENV_TOKEN)
    if not (url and token):
        try:  # Streamlit secrets (only available inside a running app)
            import streamlit as st

            url = url or st.secrets.get(_ENV_URL)
            token = token or st.secrets.get(_ENV_TOKEN)
        except Exception:
            pass
    if url and token:
        return str(url), str(token)
    return None


def configured() -> bool:
    return _creds() is not None


# --- low-level store (Turso) ------------------------------------------------
def _client(url: str, token: str):
    import libsql_client

    # The sync client speaks the HTTP pipeline; https:// is the safe scheme.
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return libsql_client.create_client_sync(url=url, auth_token=token)


def _ensure_table(client) -> None:
    client.execute(
        "CREATE TABLE IF NOT EXISTS app_backup "
        "(id INTEGER PRIMARY KEY, data BLOB, updated_at TEXT)"
    )


def download() -> bytes | None:
    """Fetch the saved SQLite file from Turso (or None if there isn't one)."""
    creds = _creds()
    if not creds:
        return None
    client = _client(*creds)
    try:
        _ensure_table(client)
        rs = client.execute("SELECT data FROM app_backup WHERE id = 1")
        if not rs.rows:
            return None
        data = rs.rows[0][0]
        if data is None:
            return None
        if isinstance(data, str):  # some transports return base64 text
            return base64.b64decode(data)
        return bytes(data)
    finally:
        client.close()


def upload(data: bytes) -> None:
    """Save the SQLite file bytes to Turso (overwrites the single backup row)."""
    creds = _creds()
    if not creds:
        return
    client = _client(*creds)
    try:
        _ensure_table(client)
        client.execute(
            "INSERT OR REPLACE INTO app_backup (id, data, updated_at) VALUES (1, ?, ?)",
            [data, datetime.now().isoformat(timespec="seconds")],
        )
    finally:
        client.close()


# --- sync orchestration -----------------------------------------------------
def pull_once(db_path) -> None:
    """Once per process: if there's no local DB, restore it from Turso."""
    global _pulled, _last_push_mtime
    with _lock:
        if _pulled:
            return
        _pulled = True
    if not configured():
        return
    path = Path(db_path)
    try:
        if not path.exists() or path.stat().st_size == 0:
            data = download()
            if data:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        if path.exists():
            # Baseline the push marker so we don't immediately re-upload.
            with _lock:
                _last_push_mtime = path.stat().st_mtime
    except Exception:
        pass  # never let a sync problem break the app


def push_if_changed(db_path) -> bool:
    """Upload the local DB to Turso if it changed since the last push.

    Returns True if an upload happened.
    """
    global _last_push_mtime
    if not configured():
        return False
    path = Path(db_path)
    try:
        if not path.exists():
            return False
        mtime = path.stat().st_mtime
        with _lock:
            if mtime <= _last_push_mtime:
                return False
        upload(_snapshot_bytes(path))
        with _lock:
            _last_push_mtime = mtime
        return True
    except Exception:
        return False


def start_autosync(db_path, interval: float = 8.0) -> None:
    """Start one background daemon that pushes changes every `interval` seconds.

    Guarded so it starts at most once per process. No-op if Turso isn't
    configured. Combined with pull_once (on connect), this gives hands-off free
    persistence without touching any page code.
    """
    global _autosync_started
    with _lock:
        if _autosync_started:
            return
        _autosync_started = True
    if not configured():
        return

    def _loop():
        while True:
            time.sleep(interval)
            try:
                push_if_changed(db_path)
            except Exception:
                pass

    threading.Thread(target=_loop, daemon=True, name="turso-autosync").start()


def sync(db_path) -> None:
    """Restore on first run, then flush any local changes. Safe to call often."""
    pull_once(db_path)
    push_if_changed(db_path)


def force_pull(db_path) -> bool:
    """Overwrite the local DB with the cloud copy (manual 'restore from cloud')."""
    global _last_push_mtime
    data = download()
    if not data:
        return False
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    with _lock:
        _last_push_mtime = path.stat().st_mtime
    return True


def force_push(db_path) -> bool:
    """Upload the local DB now regardless of mtime (manual 'save to cloud')."""
    global _last_push_mtime
    if not configured():
        return False
    path = Path(db_path)
    if not path.exists():
        return False
    upload(_snapshot_bytes(path))
    with _lock:
        _last_push_mtime = path.stat().st_mtime
    return True


def status(db_path) -> dict:
    """Small status dict for the UI."""
    info = {"configured": configured(), "cloud_bytes": None, "error": None}
    if not info["configured"]:
        return info
    try:
        data = download()
        info["cloud_bytes"] = len(data) if data else 0
    except Exception as e:  # noqa: BLE001
        info["error"] = str(e)
    return info
