"""Tests for fuzzy name matching against the registry."""

import pytest

from dfs import matching, registry
from dfs.db import connect


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "match.db")
    registry.upsert_player(c, "Bobby Witt Jr.", "IF")
    registry.upsert_player(c, "Shohei Ohtani", ["IF", "P"])
    registry.upsert_player(c, "Aaron Judge", "OF")
    yield c
    c.close()


def test_exact_name_auto_accepts(conn):
    res = matching.match_name(conn, "Aaron Judge")
    assert res["status"] == "auto"
    assert res["full_name"] == "Aaron Judge"
    assert res["score"] >= matching.AUTO_ACCEPT


def test_minor_misspelling_resolves_to_right_player(conn):
    res = matching.match_name(conn, "Aaron Judg")
    assert res["full_name"] == "Aaron Judge"
    assert res["status"] in ("auto", "confirm")


def test_alias_is_matchable(conn):
    pid = registry.get_player_by_name(conn, "Bobby Witt Jr.")["player_id"]
    registry.add_alias(conn, pid, "Witt")
    res = matching.match_name(conn, "Witt")
    assert res["full_name"] == "Bobby Witt Jr."


def test_unrelated_name_is_new(conn):
    res = matching.match_name(conn, "Zxqwerty Nobody")
    assert res["status"] == "new"


def test_best_matches_ranked_and_deduped(conn):
    pid = registry.get_player_by_name(conn, "Aaron Judge")["player_id"]
    registry.add_alias(conn, pid, "A. Judge")
    matches = matching.best_matches(conn, "Aaron Judge", limit=5)
    ids = [m[0] for m in matches]
    assert ids.count(pid) == 1  # deduped despite name + alias both matching
    assert matches[0][0] == pid  # best is Judge


def test_empty_registry_returns_new(tmp_path):
    c = connect(tmp_path / "empty.db")
    assert matching.match_name(c, "Anyone")["status"] == "new"
    c.close()
