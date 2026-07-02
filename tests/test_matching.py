"""Tests for last-name-first name matching against the registry."""

import pytest

from dfs import matching, registry
from dfs.db import connect


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "match.db")
    for name, pos in [
        ("Aaron Judge", "OF"), ("Bobby Witt Jr.", "IF"), ("Shohei Ohtani", ["IF", "P"]),
        ("Jacob Misiorowski", "P"), ("Juan Soto", "OF"), ("James Wood", "OF"),
        ("Will Smith", "IF"), ("Dominic Smith", "IF"), ("Pete Crow-Armstrong", "OF"),
        ("Cristopher Sánchez", "P"), ("Gary Sánchez", "IF"), ("Teoscar Hernández", "OF"),
    ]:
        registry.upsert_player(c, name, pos)
    yield c
    c.close()


def test_split_name():
    assert matching.split_name("J. Misiorowski") == ("J", "Misiorowski")
    assert matching.split_name("Bobby Witt Jr.") == ("B", "Witt")
    assert matching.split_name("Ohtani") == (None, "Ohtani")
    assert matching.split_name("Pete Crow-Armstrong") == ("P", "Crow-Armstrong")


def test_abbrev_matches_by_last_name(conn):
    # The bug: "J. Misiorowski" was matching "Juan Soto". Now it matches by surname.
    res = matching.match_name(conn, "J. Misiorowski")
    assert res["full_name"] == "Jacob Misiorowski"
    assert res["status"] == "auto"


def test_wrong_last_name_is_excluded(conn):
    names = [n for _pid, n, _s in matching.best_matches(conn, "J. Misiorowski", 5)]
    assert names[0] == "Jacob Misiorowski"
    assert "Juan Soto" not in names and "James Wood" not in names


def test_initial_disambiguates_same_last_name(conn):
    assert matching.match_name(conn, "W. Smith")["full_name"] == "Will Smith"
    assert matching.match_name(conn, "D. Smith")["full_name"] == "Dominic Smith"


def test_exact_full_name_auto_accepts(conn):
    res = matching.match_name(conn, "Aaron Judge")
    assert res["status"] == "auto" and res["full_name"] == "Aaron Judge"


def test_abbrev_with_suffix(conn):
    assert matching.match_name(conn, "B. Witt Jr.")["full_name"] == "Bobby Witt Jr."


def test_last_name_only_query(conn):
    assert matching.match_name(conn, "Ohtani")["full_name"] == "Shohei Ohtani"


def test_hyphenated_last_name(conn):
    assert matching.match_name(conn, "P. Crow-Armstrong")["full_name"] == "Pete Crow-Armstrong"


def test_ocr_typo_in_last_name_still_matches(conn):
    res = matching.match_name(conn, "J. Misiorowsi")  # missing 'k'
    assert res["full_name"] == "Jacob Misiorowski"


def test_accent_insensitive_matching(conn):
    # Draft boards type "Sanchez"/"Hernandez" without accents.
    assert matching.match_name(conn, "C. Sanchez")["full_name"] == "Cristopher Sánchez"
    assert matching.match_name(conn, "G. Sanchez")["full_name"] == "Gary Sánchez"
    assert matching.match_name(conn, "T. Hernandez")["full_name"] == "Teoscar Hernández"


def test_unrelated_name_is_new(conn):
    assert matching.match_name(conn, "Zxqwerty Nobody")["status"] == "new"


def test_empty_registry_returns_new(tmp_path):
    c = connect(tmp_path / "empty.db")
    assert matching.match_name(c, "Anyone Here")["status"] == "new"
    c.close()
