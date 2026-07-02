"""Name matching for draft boards (last-name first, then first initial).

Draft boards render names as ``F. Lastname`` (e.g. "J. Misiorowski"), so generic
fuzzy matching over the whole string is useless — it scores "Juan Soto" and
"J. Misiorowski" alike. Instead we:

1. split each name into (first-initial, last-name),
2. match primarily on the **last name** (fuzzy, to survive OCR errors), and
3. break ties with the **first initial**.

Aliases are intentionally NOT used here (they were a source of mis-routing);
last-name matching handles abbreviations directly and deterministically.
"""

from __future__ import annotations

import sqlite3

from rapidfuzz import fuzz

# Combined-score thresholds (0..100). Exact last name + matching initial = 100.
AUTO_ACCEPT = 95     # confident enough to auto-select
NEEDS_CONFIRM = 82   # show as the suggested option, but let the user confirm

_AUTO_LAST = 90      # last-name similarity needed to auto-accept
_CONFIRM_LAST = 74   # last-name similarity needed to even suggest
_MIN_LAST = 60       # below this, not the same last name at all

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def split_name(name: str) -> tuple[str | None, str]:
    """Return (first_initial, last_name) for a player or draft name.

    "J. Misiorowski" -> ("J", "Misiorowski"); "Bobby Witt Jr." -> ("B", "Witt");
    "Ohtani" -> (None, "Ohtani"); "Pete Crow-Armstrong" -> ("P", "Crow-Armstrong").
    """
    tokens = [t for t in str(name).replace(",", " ").split() if t]
    while tokens and tokens[-1].strip(".").lower() in _SUFFIXES:
        tokens.pop()
    if not tokens:
        return (None, "")
    if len(tokens) == 1:
        return (None, tokens[0].strip("."))
    first = tokens[0].strip(".")
    last = " ".join(tokens[1:])
    initial = first[:1].upper() if first else None
    return (initial, last)


def _ranked(conn: sqlite3.Connection, raw_name: str, limit: int = 5) -> list[dict]:
    q_initial, q_last = split_name(raw_name)
    q_last_l = q_last.lower()
    if not q_last_l:
        return []
    out: list[dict] = []
    for r in conn.execute("SELECT player_id, full_name FROM players"):
        p_initial, p_last = split_name(r["full_name"])
        last_score = fuzz.ratio(q_last_l, p_last.lower())
        if last_score < _MIN_LAST:
            continue
        initial_match = (q_initial is None) or (p_initial is not None and p_initial == q_initial)
        score = last_score * 0.85 + (15 if initial_match else 0)
        out.append({
            "player_id": r["player_id"], "full_name": r["full_name"],
            "score": round(score, 1), "last_score": last_score, "initial_match": initial_match,
        })
    # Best last-name + initial-match first; exact last names before fuzzy ones.
    out.sort(key=lambda x: (x["score"], x["last_score"], x["initial_match"]), reverse=True)
    return out[:limit]


def best_matches(conn: sqlite3.Connection, raw_name: str, limit: int = 5) -> list[tuple[int, str, float]]:
    """Top registry matches: (player_id, full_name, score). Last-name ranked."""
    return [(m["player_id"], m["full_name"], m["score"]) for m in _ranked(conn, raw_name, limit)]


def match_name(conn: sqlite3.Connection, raw_name: str) -> dict:
    """Classify a raw name into auto / confirm / new with the best candidate."""
    ranked = _ranked(conn, raw_name, 1)
    if not ranked:
        return {"status": "new", "raw": raw_name}
    top = ranked[0]
    base = {"raw": raw_name, "player_id": top["player_id"],
            "full_name": top["full_name"], "score": top["score"]}
    if top["last_score"] >= _AUTO_LAST and top["initial_match"]:
        return {"status": "auto", **base}
    if top["last_score"] >= _CONFIRM_LAST:
        return {"status": "confirm", **base}
    return {"status": "new", **base}
