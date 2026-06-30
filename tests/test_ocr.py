"""Tests for the OCR cell parser (pure; no Tesseract/Pillow required)."""

from dfs import ocr


def test_parse_full_cell():
    res = ocr.parse_cell_text("12 Bobby Witt KC IF")
    assert res["pick_number"] == 12
    assert res["team"] == "KC"
    assert res["roster_slot"] == "IF"
    assert "Bobby Witt" in res["name"]


def test_parse_slot_collapses_granular():
    assert ocr.parse_cell_text("3 Mike Trout LAA CF")["roster_slot"] == "OF"
    assert ocr.parse_cell_text("5 Some Guy NYY SS")["roster_slot"] == "IF"
    assert ocr.parse_cell_text("8 Hit Ter BOS HT")["roster_slot"] == "HT"


def test_parse_pitcher_cell():
    res = ocr.parse_cell_text("1 Chris Sale ATL P")
    assert res["pick_number"] == 1
    assert res["roster_slot"] == "P"
    assert res["team"] == "ATL"
    assert "Sale" in res["name"]


def test_parse_handles_missing_fields():
    res = ocr.parse_cell_text("Just A Name")
    assert res["pick_number"] is None
    assert res["roster_slot"] is None
    assert res["name"] == "Just A Name"


def test_parse_empty():
    res = ocr.parse_cell_text("")
    assert res == {"pick_number": None, "name": "", "team": None, "roster_slot": None}


def test_ocr_available_returns_tuple():
    ok, msg = ocr.ocr_available()
    assert isinstance(ok, bool)
    assert isinstance(msg, str) and msg


# --- Position-aware parser (the real board layout) --------------------------
def _w(text, left, top, width=40, height=18):
    return {"text": text, "left": left, "top": top, "width": width, "height": height}


def test_parse_cell_words_corner_layout():
    # 200x100 cell: name top-left, pick# top-right, slot under it, team bottom-left.
    cell_w, cell_h = 200, 100
    words = [
        _w("G.", 8, 6, 16, 18),
        _w("Kirby", 28, 6, 60, 18),
        _w("1", 178, 6, 12, 16),       # pick number, top-right
        _w("P", 180, 40, 12, 18),      # slot, under pick number
        _w("SEA", 8, 72, 40, 18),      # team, bottom-left
    ]
    res = ocr.parse_cell_words(words, cell_w, cell_h)
    assert res["pick_number"] == 1
    assert res["roster_slot"] == "P"
    assert res["team"] == "SEA"
    assert res["name"] == "G. Kirby"


def test_parse_cell_words_ht_flex_and_granular():
    cell_w, cell_h = 200, 100
    words = [
        _w("M.", 8, 6, 16, 18), _w("Moniak", 28, 6, 70, 18),
        _w("12", 172, 6, 18, 16),
        _w("HT", 176, 40, 18, 18),
        _w("COL", 8, 72, 40, 18),
    ]
    res = ocr.parse_cell_words(words, cell_w, cell_h)
    assert res["pick_number"] == 12
    assert res["roster_slot"] == "HT"
    assert res["team"] == "COL"
    assert "Moniak" in res["name"]


def test_parse_cell_words_name_initial_not_mistaken_for_slot():
    # "P." is a name initial on the LEFT — must not be read as a Pitcher slot.
    cell_w, cell_h = 200, 100
    words = [
        _w("P.", 8, 6, 16, 18), _w("Crow", 28, 6, 50, 18),
        _w("8", 178, 6, 12, 16),
        _w("OF", 176, 40, 18, 18),
        _w("CHC", 8, 72, 40, 18),
    ]
    res = ocr.parse_cell_words(words, cell_w, cell_h)
    assert res["roster_slot"] == "OF"        # from the right column, not "P."
    assert res["name"].startswith("P.")
    assert res["team"] == "CHC"
