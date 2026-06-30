"""Tests for the persistent player registry + position resolution.

Positions are one or more of the three roster groups {IF, OF, P}.
"""

import pytest

from dfs import registry
from dfs.config import normalize_position, normalize_positions
from dfs.db import connect


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    yield c
    c.close()


def test_upsert_and_resolve_by_exact_name(conn):
    pid = registry.upsert_player(conn, "Aaron Judge", "OF", team="NYY")
    row = registry.get_player_by_name(conn, "Aaron Judge")
    assert row is not None
    assert row["player_id"] == pid
    assert registry.get_positions(row) == ["OF"]
    assert row["team"] == "NYY"


def test_name_matching_is_case_insensitive(conn):
    registry.upsert_player(conn, "Mookie Betts", "IF")
    assert registry.get_player_by_name(conn, "mookie betts") is not None
    assert registry.get_player_by_name(conn, "  Mookie   Betts ") is not None


def test_alias_resolution(conn):
    pid = registry.upsert_player(conn, "Bobby Witt Jr.", "IF")
    registry.add_alias(conn, pid, "Bobby Witt")
    row = registry.get_player_by_name(conn, "Bobby Witt")
    assert row is not None and row["player_id"] == pid


def test_unknown_players_detected(conn):
    registry.upsert_player(conn, "Known Guy", "IF")
    unknown = registry.find_unknown_players(conn, ["Known Guy", "Mystery Man", "known guy"])
    assert unknown == ["Mystery Man"]


def test_multi_position_eligibility(conn):
    # A flex bat eligible at both IF and OF.
    registry.upsert_player(conn, "Mookie Betts", ["OF", "IF"])
    row = registry.get_player_by_name(conn, "Mookie Betts")
    # Canonical order is always IF, OF, P regardless of input order.
    assert registry.get_positions(row) == ["IF", "OF"]


def test_ohtani_is_if_and_p_via_union(conn):
    # Assigned IF as a batter, later entered as a pitcher -> union keeps both.
    registry.upsert_player(conn, "Shohei Ohtani", "IF")
    registry.upsert_player(conn, "Shohei Ohtani", "P")
    row = registry.get_player_by_name(conn, "Shohei Ohtani")
    assert registry.get_positions(row) == ["IF", "P"]


def test_assign_positions_replaces_not_merges(conn):
    registry.upsert_player(conn, "Flex Guy", ["IF", "OF"])
    registry.assign_positions(conn, "Flex Guy", ["OF"])  # explicit set
    row = registry.get_player_by_name(conn, "Flex Guy")
    assert registry.get_positions(row) == ["OF"]


def test_position_normalization():
    assert normalize_position("LF") == "OF"
    assert normalize_position("ss") == "IF"
    assert normalize_position("DH") == "IF"
    assert normalize_position("P") == "P"
    assert normalize_position(None) is None
    assert normalize_position("ZZ") is None
    # Granular inputs collapse to groups, deduped, in canonical order.
    assert normalize_positions(["2B", "CF"]) == ["IF", "OF"]
    assert normalize_positions("RF") == ["OF"]
    assert normalize_positions(["P", "1B"]) == ["IF", "P"]
    assert normalize_positions(None) == []
