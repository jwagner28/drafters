"""Tests for snake-draft order reconstruction."""

import pytest

from dfs import draft


def test_snake_overall_three_drafters():
    N = 3
    # Round 1 left->right: seats 1,2,3 -> 1,2,3
    assert [draft.snake_overall(1, s, N) for s in (1, 2, 3)] == [1, 2, 3]
    # Round 2 right->left: seat 3 picks first (4), seat 1 last (6)
    assert draft.snake_overall(2, 3, N) == 4
    assert draft.snake_overall(2, 2, N) == 5
    assert draft.snake_overall(2, 1, N) == 6
    # Round 3 back to left->right
    assert [draft.snake_overall(3, s, N) for s in (1, 2, 3)] == [7, 8, 9]


def test_round_seat_is_inverse_of_overall():
    N = 4
    for r in range(1, 6):
        for s in range(1, N + 1):
            overall = draft.snake_overall(r, s, N)
            assert draft.snake_round_seat(overall, N) == (r, s)


def test_generate_grid_is_complete_and_ordered():
    N, R = 4, 5
    cells = draft.generate_grid(N, R)
    assert len(cells) == N * R
    assert [c.overall_pick_number for c in cells] == list(range(1, N * R + 1))
    # Each seat appears exactly R times.
    from collections import Counter
    seat_counts = Counter(c.seat for c in cells)
    assert all(count == R for count in seat_counts.values())


def test_reconstruct_from_pick_numbers():
    N = 3
    # printed numbers 4 and 6 in a 3-person draft -> round 2 seats 3 and 1
    assert draft.reconstruct_from_pick_numbers([4, 6], N) == [(2, 3), (2, 1)]


def test_validate_pick_numbers_detects_problems():
    N, R = 2, 2  # expect 1..4
    assert draft.validate_pick_numbers([1, 2, 3, 4], N, R) == []
    problems = draft.validate_pick_numbers([1, 2, 2, 9], N, R)
    assert any("Duplicate" in p for p in problems)
    assert any("Missing" in p for p in problems)
    assert any("Unexpected" in p for p in problems)


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        draft.snake_overall(1, 5, 3)  # seat out of range
    with pytest.raises(ValueError):
        draft.generate_grid(0, 5)
