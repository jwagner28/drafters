"""Snake-draft order reconstruction.

A snake (serpentine) draft reverses seat order every round: round 1 goes seat
1..N, round 2 goes N..1, and so on. Every pick has a global `overall_pick_number`
(1..N*R) which the opponent model later needs to reconstruct who was available
at each pick.
"""

from __future__ import annotations

from dataclasses import dataclass

ROSTER_SLOTS = ["P", "IF", "OF", "HT"]


def snake_overall(round_number: int, seat: int, num_drafters: int) -> int:
    """Global pick number for a (round, seat) in a snake draft. All 1-based."""
    if round_number < 1 or seat < 1 or seat > num_drafters:
        raise ValueError("round_number/seat out of range")
    if round_number % 2 == 1:  # odd round: left -> right
        pos = seat
    else:  # even round: right -> left
        pos = num_drafters - seat + 1
    return (round_number - 1) * num_drafters + pos


def snake_round_seat(overall: int, num_drafters: int) -> tuple[int, int]:
    """Inverse of snake_overall: (round_number, seat) from a global pick number."""
    if overall < 1 or num_drafters < 1:
        raise ValueError("overall/num_drafters out of range")
    round_number = (overall - 1) // num_drafters + 1
    pos = (overall - 1) % num_drafters + 1  # position within the round (1..N)
    if round_number % 2 == 1:
        seat = pos
    else:
        seat = num_drafters - pos + 1
    return round_number, seat


@dataclass
class GridCell:
    overall_pick_number: int
    round_number: int
    seat: int            # drafter seat, 1-based
    slot_in_round: int   # position within the round, 1-based


def generate_grid(num_drafters: int, num_rounds: int) -> list[GridCell]:
    """All cells of a snake draft, ordered by overall pick number (1..N*R)."""
    if num_drafters < 1 or num_rounds < 1:
        raise ValueError("num_drafters and num_rounds must be >= 1")
    cells: list[GridCell] = []
    for overall in range(1, num_drafters * num_rounds + 1):
        r, seat = snake_round_seat(overall, num_drafters)
        pos = overall - (r - 1) * num_drafters
        cells.append(GridCell(overall, r, seat, pos))
    return cells


def reconstruct_from_pick_numbers(pick_numbers: list[int], num_drafters: int) -> list[tuple[int, int]]:
    """Given printed pick numbers (any order), return (round, seat) for each.

    Useful when OCR reads the printed numbers off the board — we trust those to
    place each cell in the snake order.
    """
    return [snake_round_seat(n, num_drafters) for n in pick_numbers]


def validate_pick_numbers(pick_numbers: list[int], num_drafters: int, num_rounds: int) -> list[str]:
    """Return a list of human-readable problems with a set of printed numbers."""
    problems: list[str] = []
    expected = set(range(1, num_drafters * num_rounds + 1))
    got = list(pick_numbers)
    got_set = set(got)
    if len(got) != len(got_set):
        problems.append("Duplicate pick numbers found.")
    missing = expected - got_set
    extra = got_set - expected
    if missing:
        problems.append(f"Missing pick numbers: {sorted(missing)[:10]}")
    if extra:
        problems.append(f"Unexpected pick numbers: {sorted(extra)[:10]}")
    return problems
