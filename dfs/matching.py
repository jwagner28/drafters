"""Fuzzy name matching against the player registry (rapidfuzz).

OCR and hand entry produce messy names; this resolves them to registry players.
High-confidence matches auto-accept; middling ones are surfaced for a one-tap
confirm (after which the raw spelling is saved as an alias so it auto-resolves
next time); anything weak is treated as a new player.
"""

from __future__ import annotations

import json
import sqlite3

from rapidfuzz import fuzz, process

# WRatio scores are 0..100.
AUTO_ACCEPT = 88   # >= this: confident match
NEEDS_CONFIRM = 72  # >= this (but < AUTO): show as a suggestion to confirm


def _registry_index(conn: sqlite3.Connection) -> list[tuple[int, str, str]]:
    """Flatten the registry into (player_id, full_name, candidate_string) rows,
    including each alias as its own candidate string."""
    index: list[tuple[int, str, str]] = []
    for r in conn.execute("SELECT player_id, full_name, aliases_json FROM players"):
        index.append((r["player_id"], r["full_name"], r["full_name"]))
        for alias in json.loads(r["aliases_json"] or "[]"):
            index.append((r["player_id"], r["full_name"], alias))
    return index


def best_matches(conn: sqlite3.Connection, raw_name: str, limit: int = 5) -> list[tuple[int, str, float]]:
    """Top registry matches for a raw name: list of (player_id, full_name, score).

    De-duplicated by player_id, keeping each player's best-scoring candidate.
    """
    index = _registry_index(conn)
    if not index:
        return []
    choices = [c[2] for c in index]
    results = process.extract(raw_name, choices, scorer=fuzz.WRatio, limit=limit * 3)
    best: dict[int, tuple[str, float]] = {}
    for _matched, score, idx in results:
        pid, full, _cand = index[idx]
        if pid not in best or score > best[pid][1]:
            best[pid] = (full, score)
    ranked = sorted(
        ([pid, full, score] for pid, (full, score) in best.items()),
        key=lambda x: x[2],
        reverse=True,
    )
    return [(pid, full, float(score)) for pid, full, score in ranked[:limit]]


def match_name(conn: sqlite3.Connection, raw_name: str) -> dict:
    """Classify a raw name into auto / confirm / new with the best candidate.

    Returns a dict: {"status": "auto"|"confirm"|"new", "raw": ..., and when a
    candidate exists: "player_id", "full_name", "score"}.
    """
    matches = best_matches(conn, raw_name, limit=1)
    if not matches:
        return {"status": "new", "raw": raw_name}
    pid, full, score = matches[0]
    if score >= AUTO_ACCEPT:
        status = "auto"
    elif score >= NEEDS_CONFIRM:
        status = "confirm"
    else:
        return {"status": "new", "raw": raw_name, "player_id": pid, "full_name": full, "score": score}
    return {"status": status, "raw": raw_name, "player_id": pid, "full_name": full, "score": score}
