"""Box-detection + full-board OCR tests using a synthetic board.

Detection (numpy/scipy/Pillow) is tested without Tesseract; the field-reading
test is skipped when Tesseract isn't available.
"""

import pytest

pytest.importorskip("PIL")
pytest.importorskip("scipy")

from dfs import ocr  # noqa: E402

COLORS = [(46, 122, 110), (150, 70, 70), (110, 64, 132), (66, 116, 78), (60, 84, 150)]


def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", size)
    except Exception:
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
        except Exception:
            return None


def _make_board(players, rounds, picks, sc=2, with_text=True):
    """Render a board: dark bg, left round-label column, header, colored boxes
    with name (TL), pick# (TR), position (under it), team (BL)."""
    from PIL import Image, ImageDraw

    W, H = (130 * players + 60) * sc, (56 * rounds + 70) * sc
    img = Image.new("RGB", (W, H), (16, 20, 38))
    d = ImageDraw.Draw(img)
    margin, header = 50 * sc, 64 * sc
    bw = (W - margin) / players
    bh = (H - header) / rounds
    font = _font(15 * sc) if with_text else None
    for r in range(rounds):
        for c in range(players):
            name, team, slot = picks[(r + 1, c + 1)]
            pick = r * players + (c + 1 if (r + 1) % 2 == 1 else players - c)
            x0 = int(margin + bw * c) + 3 * sc
            y0 = int(header + bh * r) + 3 * sc
            x1 = int(margin + bw * (c + 1)) - 3 * sc
            y1 = int(header + bh * (r + 1)) - 3 * sc
            d.rounded_rectangle([x0, y0, x1, y1], radius=6 * sc, fill=COLORS[(r + c) % len(COLORS)])
            if with_text and font is not None:
                d.text((x0 + 6 * sc, y0 + 4 * sc), name, fill=(245, 245, 245), font=font)
                d.text((x1 - 22 * sc, y0 + 4 * sc), str(pick), fill=(245, 245, 245), font=font)
                d.text((x1 - 22 * sc, y0 + 22 * sc), slot, fill=(235, 235, 235), font=font)
                d.text((x0 + 6 * sc, y1 - 20 * sc), team, fill=(225, 225, 230), font=font)
    return img


def _png(img):
    from io import BytesIO
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


DATA_2x4 = {
    (1, 1): ("G. Kirby", "SEA", "P"), (1, 2): ("S. Ohtani", "LAD", "P"),
    (2, 1): ("Y. Alvarez", "HOU", "OF"), (2, 2): ("K. Tucker", "CHC", "OF"),
    (3, 1): ("B. Buxton", "MIN", "HT"), (3, 2): ("B. Lowe", "PIT", "IF"),
    (4, 1): ("P. Alonso", "BAL", "IF"), (4, 2): ("J. Chourio", "MIL", "OF"),
}


def test_detect_counts_2_players():
    img = _make_board(2, 4, DATA_2x4, with_text=False)
    det = ocr.detect_boxes(img)
    assert det["n_cols"] == 2
    assert det["n_rows"] == 4
    assert len(det["boxes"]) == 8
    # Each (col, row) is unique.
    assert len({(b["col"], b["row"]) for b in det["boxes"]}) == 8


def test_detect_counts_4_players():
    data = {(r, c): (f"P{r}{c}", "NYY", "OF") for r in range(1, 4) for c in range(1, 5)}
    det = ocr.detect_boxes(_make_board(4, 3, data, with_text=False))
    assert det["n_cols"] == 4
    assert det["n_rows"] == 3
    assert len(det["boxes"]) == 12


def test_detect_real_board_screenshot():
    """Regression on a real 2x10 board screenshot (Underdog-style, with dim
    green HT boxes that earlier slipped past the brightness threshold)."""
    from pathlib import Path

    from PIL import Image

    fixture = Path(__file__).resolve().parents[1] / "sample_data" / "sample_board.jpg"
    if not fixture.exists():
        pytest.skip("sample_board.jpg fixture not present")
    det = ocr.detect_boxes(Image.open(fixture).convert("RGB"))
    assert det["n_cols"] == 2
    assert det["n_rows"] == 10
    assert len(det["boxes"]) == 20


# --- Full field reading (needs Tesseract) -----------------------------------
_ok, _msg = ocr.ocr_available()
needs_ocr = pytest.mark.skipif(not _ok, reason=f"Tesseract not available: {_msg}")


@needs_ocr
def test_ocr_board_reads_all_fields():
    if _font(30) is None:
        pytest.skip("No scalable font available.")
    result = ocr.ocr_board(_png(_make_board(2, 4, DATA_2x4, sc=2)))
    assert result["n_drafters"] == 2
    assert result["n_rounds"] == 4
    by_rc = {(p["round"], p["seat"]): p for p in result["picks"]}

    # Pick numbers come from snake order — exact.
    assert {p["pick_number"] for p in result["picks"]} == set(range(1, 9))
    assert by_rc[(2, 1)]["pick_number"] == 4 and by_rc[(2, 2)]["pick_number"] == 3

    # Spot-check a few read fields (names/teams/slots).
    assert "Ohtani" in by_rc[(1, 2)]["name"]
    assert by_rc[(1, 1)]["team"] == "SEA"
    assert by_rc[(1, 1)]["roster_slot"] == "P"     # single-letter pitcher slot
    assert by_rc[(3, 1)]["roster_slot"] == "HT"
    assert by_rc[(4, 2)]["roster_slot"] == "OF"


@needs_ocr
def test_ocr_real_board_reads_known_fields():
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[1] / "sample_data" / "sample_board.jpg"
    if not fixture.exists():
        pytest.skip("sample_board.jpg fixture not present")
    result = ocr.ocr_board(fixture.read_bytes())
    assert result["n_drafters"] == 2 and result["n_rounds"] == 10
    by_rc = {(p["round"], p["seat"]): p for p in result["picks"]}
    # Pitcher (single-letter P), a flex HT, and an OF — the tricky slots.
    assert by_rc[(1, 1)]["roster_slot"] == "P" and "Kirby" in by_rc[(1, 1)]["name"]
    assert by_rc[(6, 1)]["roster_slot"] == "HT" and by_rc[(6, 1)]["team"] == "COL"
    assert by_rc[(1, 2)]["roster_slot"] == "OF" and "Ohtani" in by_rc[(1, 2)]["name"]
    # Snake pick numbers are exact.
    assert {p["pick_number"] for p in result["picks"]} == set(range(1, 21))


@needs_ocr
def test_ocr_board_4_players_slots():
    data = {(r, c): (f"Player{r}{c}", "NYY", ["P", "IF", "OF", "HT"][(r + c) % 4])
            for r in range(1, 4) for c in range(1, 5)}
    result = ocr.ocr_board(_png(_make_board(4, 3, data, sc=2)))
    assert result["n_drafters"] == 4 and result["n_rounds"] == 3
    correct = sum(
        1 for p in result["picks"]
        if p["roster_slot"] == ["P", "IF", "OF", "HT"][(p["round"] + p["seat"]) % 4]
    )
    assert correct >= 11  # allow at most one OCR slip across 12 boxes
