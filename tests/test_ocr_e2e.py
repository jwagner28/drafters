"""End-to-end OCR test against the real Tesseract engine at the box level
(name top-left, pick# top-right, position under it, team bottom-left).

Skipped automatically when Tesseract (or a usable font) isn't available.
"""

import pytest

from dfs import ocr

pytest.importorskip("PIL")
_ok, _msg = ocr.ocr_available()
pytestmark = pytest.mark.skipif(not _ok, reason=f"Tesseract not available: {_msg}")


def _render_cell(name, pick, slot, team, w=260, h=130):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (w, h), (46, 122, 110))  # colored box, light text
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 26)
        small = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 22)
    except Exception:
        pytest.skip("No scalable font available for rendering a test cell.")
    draw.text((8, 8), name, fill=(245, 245, 245), font=font)            # top-left
    draw.text((w - 40, 8), str(pick), fill=(245, 245, 245), font=small)  # top-right
    draw.text((w - 44, 44), slot, fill=(235, 235, 235), font=small)      # under pick#
    draw.text((8, h - 34), team, fill=(225, 225, 230), font=small)       # bottom-left
    return img


def _box_for(img):
    return {"fx": 0, "fy": 0, "fw": img.width, "fh": img.height}


def test_read_box_corner_layout():
    cell = _render_cell("G. Kirby", 1, "P", "SEA")
    info = ocr._read_box(cell, _box_for(cell))
    assert info["roster_slot"] == "P"
    assert "Kirby" in info["name"]
    assert info["team"] == "SEA"


def test_read_box_flex_ht():
    cell = _render_cell("M. Betts", 11, "HT", "LAD")
    info = ocr._read_box(cell, _box_for(cell))
    assert info["roster_slot"] == "HT"
    assert "Betts" in info["name"]
