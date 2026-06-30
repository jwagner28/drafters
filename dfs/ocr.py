"""Free local OCR for draft-board screenshots (best-effort assist).

Screenshots vary in zoom, round count, and player count (2–4), so we can't
assume fixed cell sizes. Instead we **detect** the colored pick boxes: each pick
is a bright rectangle on a dark background. We find those rectangles, cluster
them into columns (drafters) and rows (rounds), then read each box by corner:

    NAME (top-left)              PICK# (top-right)
                                 POS   (under pick#)
    TEAM (bottom-left)

The printed pick number (top-right) gives the snake order; HT is a flex hitter
slot (IF or OF).

OCR is OPTIONAL — Pillow + pytesseract + the Tesseract binary may be missing.
Everything imports lazily and `ocr_available()` reports status, so the New
Contest page can always fall back to manual grid entry.
"""

from __future__ import annotations

import os
import re
import shutil
import statistics
from collections import defaultdict
from pathlib import Path

# Where Tesseract commonly installs, so OCR works even if it isn't on PATH.
# Override explicitly with the TESSERACT_CMD environment variable.
_COMMON_TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
]

SLOT_TOKENS = {"P", "IF", "OF", "HT", "C", "1B", "2B", "3B", "SS", "DH", "LF", "CF", "RF", "UT", "UTIL"}
# Granular slot labels collapse to the four roster slots used for scoring.
SLOT_TO_ROSTER = {
    "P": "P",
    "IF": "IF", "C": "IF", "1B": "IF", "2B": "IF", "3B": "IF", "SS": "IF", "DH": "IF",
    "OF": "OF", "LF": "OF", "CF": "OF", "RF": "OF",
    "HT": "HT", "UT": "HT", "UTIL": "HT",
}

# Right-hand column (pick# + position) begins at this fraction of box width;
# top/bottom split sits at this fraction of box height.
_RIGHT_X = 0.58
_MID_Y = 0.50


def _configure_tesseract(pytesseract) -> None:
    """Point pytesseract at the Tesseract binary without requiring PATH.

    Order: explicit TESSERACT_CMD env var, then PATH, then common install dirs.
    """
    env = os.environ.get("TESSERACT_CMD")
    if env and Path(env).exists():
        pytesseract.pytesseract.tesseract_cmd = env
        return
    if shutil.which("tesseract"):
        return
    for candidate in _COMMON_TESSERACT_PATHS:
        if candidate and Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = candidate
            return


def ocr_available() -> tuple[bool, str]:
    """(is_available, message). Checks Pillow, pytesseract, and the binary."""
    try:
        import PIL  # noqa: F401
    except Exception:
        return False, "Pillow not installed. Run: pip install -e \".[ocr]\""
    try:
        import pytesseract
    except Exception:
        return False, "pytesseract not installed. Run: pip install -e \".[ocr]\""
    _configure_tesseract(pytesseract)
    try:
        version = pytesseract.get_tesseract_version()
        return True, f"Tesseract {version} ready."
    except Exception:
        return False, (
            "Tesseract OCR engine not found. Install it (free) from "
            "https://github.com/UB-Mannheim/tesseract/wiki and ensure it's on PATH "
            "(or set the TESSERACT_CMD environment variable)."
        )


# ---------------------------------------------------------------------------
# Word/field parsing
# ---------------------------------------------------------------------------
def parse_cell_words(words: list[dict], cell_w: int, cell_h: int) -> dict:
    """Classify OCR words (with cell-relative boxes) into the four corner fields.

    Each word dict needs `text`, `left`, `top`, `width`, `height`.
    Returns {pick_number, name, team, roster_slot}.
    """
    cell_w = max(cell_w, 1)
    cell_h = max(cell_h, 1)
    right: list[tuple[str, float]] = []
    left_top: list[tuple[str, float]] = []
    left_bottom: list[tuple[str, float]] = []
    for w in words:
        text = str(w["text"]).strip()
        if not text:
            continue
        cx = (w["left"] + w["width"] / 2) / cell_w
        cy = (w["top"] + w["height"] / 2) / cell_h
        if cx >= _RIGHT_X:
            right.append((text, cy))
        elif cy < _MID_Y:
            left_top.append((text, cx))
        else:
            left_bottom.append((text, cx))

    pick_number = None
    slot = None
    for text, _cy in sorted(right, key=lambda t: t[1]):
        clean = text.strip(".,;:|()[]")
        if pick_number is None and clean.isdigit():
            pick_number = int(clean)
            continue
        token = re.sub(r"[^A-Za-z0-9]", "", text).upper()
        if slot is None and token in SLOT_TOKENS:
            slot = SLOT_TO_ROSTER.get(token)

    team = None
    for text, _cx in sorted(left_bottom, key=lambda t: t[1]):
        letters = re.sub(r"[^A-Za-z]", "", text)
        if 2 <= len(letters) <= 4:
            team = letters.upper()
            break

    name = " ".join(text for text, _cx in sorted(left_top, key=lambda t: t[1])).strip()
    return {"pick_number": pick_number, "name": name, "team": team, "roster_slot": slot}


def parse_cell_text(text: str) -> dict:
    """Simple single-line parser ("12 Bobby Witt KC IF"). Kept for callers with
    plain text rather than positioned words."""
    raw = " ".join(str(text).split())
    tokens = re.split(r"\s+", raw) if raw else []
    pick_number = None
    slot = None
    team = None
    name_tokens: list[str] = []
    for tok in tokens:
        clean = tok.strip(".,;:|()[]")
        if not clean:
            continue
        if pick_number is None and clean.isdigit():
            pick_number = int(clean)
            continue
        upper = clean.upper()
        if slot is None and upper in SLOT_TOKENS:
            slot = SLOT_TO_ROSTER.get(upper)
            continue
        if team is None and clean.isalpha() and clean.isupper() and 2 <= len(clean) <= 4:
            team = upper
            continue
        name_tokens.append(clean)
    return {"pick_number": pick_number, "name": " ".join(name_tokens).strip(),
            "team": team, "roster_slot": slot}


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------
def _open_image(image_bytes: bytes):
    from io import BytesIO

    from PIL import Image

    return Image.open(BytesIO(image_bytes)).convert("RGB")


def _preprocess(image):
    """Grayscale + 2x upscale + autocontrast — helps Tesseract on small text."""
    from PIL import ImageOps

    gray = ImageOps.grayscale(image)
    gray = gray.resize((max(gray.width * 2, 1), max(gray.height * 2, 1)))
    return ImageOps.autocontrast(gray)


def _upscale_contrast(image, factor: int = 4):
    from PIL import Image as _Image, ImageOps

    gray = ImageOps.grayscale(image)
    gray = gray.resize((max(gray.width * factor, 1), max(gray.height * factor, 1)),
                       resample=_Image.LANCZOS)
    return ImageOps.autocontrast(gray)


def _cell_words(cell) -> list[dict]:
    """Run Tesseract on one image and return word boxes (sparse-text mode)."""
    import pytesseract
    from pytesseract import Output

    _configure_tesseract(pytesseract)
    data = pytesseract.image_to_data(cell, config="--psm 11", output_type=Output.DICT)
    words = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if 0 <= conf < 25:
            continue
        words.append({
            "text": text, "left": data["left"][i], "top": data["top"][i],
            "width": data["width"][i], "height": data["height"][i],
        })
    return words


def _ocr_line(cell) -> str:
    """Plain single-line OCR (used for header drafter names)."""
    import pytesseract

    _configure_tesseract(pytesseract)
    return " ".join(pytesseract.image_to_string(cell, config="--psm 7").split())


def _ocr_block(image, whitelist: str) -> str:
    """OCR an image as a uniform text block with a character whitelist."""
    import pytesseract

    _configure_tesseract(pytesseract)
    return pytesseract.image_to_string(
        image, config=f"--psm 6 -c tessedit_char_whitelist={whitelist}"
    )


# ---------------------------------------------------------------------------
# Box detection
# ---------------------------------------------------------------------------
def _cluster_means(values: list[float], min_gap: float) -> list[float]:
    """1-D clustering: split a sorted sequence where the gap exceeds min_gap.
    Returns the mean of each cluster (the cluster centers)."""
    vs = sorted(values)
    if not vs:
        return []
    clusters = [[vs[0]]]
    for v in vs[1:]:
        if v - clusters[-1][-1] > min_gap:
            clusters.append([v])
        else:
            clusters[-1].append(v)
    return [sum(c) / len(c) for c in clusters]


def detect_boxes(image, threshold: int = 35, max_width: int = 640) -> dict:
    """Detect the pick boxes and assign each a (col, row).

    Returns {boxes, n_cols, n_rows, header_h, ...}. Each box has full-resolution
    `fx, fy, fw, fh` plus `col`/`row` (0-based). Boxes are bright rectangles on a
    dark background; `threshold` is the brightness cutoff between box and gap.
    """
    import numpy as np
    from scipy import ndimage

    w0, h0 = image.size
    scale = max_width / w0 if w0 > max_width else 1.0
    sw, sh = max(int(w0 * scale), 1), max(int(h0 * scale), 1)
    arr = np.asarray(image.convert("L").resize((sw, sh)), dtype=np.uint8)

    mask = arr > threshold
    # Erode a few px to wipe out THIN bright strokes — round labels (R1..R10),
    # player text on the dark background, header titles — while the solid box
    # fills survive. This is what separates boxes from text.
    mask = ndimage.binary_erosion(mask, iterations=3)
    labeled, n = ndimage.label(mask)
    empty = {"boxes": [], "n_cols": 0, "n_rows": 0, "n_drafters": 0, "n_rounds": 0,
             "header_h": 0, "scale": scale}
    if n == 0:
        return empty

    comps = []
    for idx, sl in enumerate(ndimage.find_objects(labeled), start=1):
        if sl is None:
            continue
        ys, xs = sl
        cw, ch = xs.stop - xs.start, ys.stop - ys.start
        area = int((labeled[sl] == idx).sum())
        comps.append({"x": xs.start, "y": ys.start, "w": cw, "h": ch, "area": area})

    # Pick boxes are WIDE solid rectangles spanning a real fraction of the image.
    # Aspect + size filtering drops avatars (square), leftover text slivers, and
    # noise without relying on a median that text could skew.
    boxes = [
        c for c in comps
        if c["w"] >= 1.2 * c["h"]          # wider than tall
        and c["w"] >= 0.10 * sw            # spans a meaningful width
        and c["h"] >= 0.015 * sh
        and c["area"] >= 0.4 * c["w"] * c["h"]
    ]
    if not boxes:
        return empty

    # Refine: drop anything far from the typical box size (merged rows, etc.).
    med_w = statistics.median(c["w"] for c in boxes)
    med_h = statistics.median(c["h"] for c in boxes)
    boxes = [c for c in boxes if 0.5 * med_w <= c["w"] <= 1.9 * med_w
             and 0.5 * med_h <= c["h"] <= 1.9 * med_h]
    if not boxes:
        return empty

    med_w = statistics.median(c["w"] for c in boxes)
    med_h = statistics.median(c["h"] for c in boxes)
    for c in boxes:
        c["cx"] = c["x"] + c["w"] / 2
        c["cy"] = c["y"] + c["h"] / 2
    col_means = _cluster_means([c["cx"] for c in boxes], 0.5 * med_w)
    row_means = _cluster_means([c["cy"] for c in boxes], 0.5 * med_h)
    inv = 1.0 / scale
    for c in boxes:
        c["col"] = min(range(len(col_means)), key=lambda i: abs(c["cx"] - col_means[i]))
        c["row"] = min(range(len(row_means)), key=lambda i: abs(c["cy"] - row_means[i]))
        c["fx"], c["fy"] = int(c["x"] * inv), int(c["y"] * inv)
        c["fw"], c["fh"] = int(c["w"] * inv), int(c["h"] * inv)
    boxes.sort(key=lambda c: (c["col"], c["row"]))
    n_cols, n_rows = len(col_means), len(row_means)
    return {
        "boxes": boxes, "n_cols": n_cols, "n_rows": n_rows,
        # Aliases so callers can use either vocabulary interchangeably.
        "n_drafters": n_cols, "n_rounds": n_rows,
        "header_h": int(min(c["fy"] for c in boxes)), "scale": scale,
    }


def detect_overlay(image, detection: dict):
    """Annotate the image with the detected boxes (for a visual sanity check)."""
    from PIL import ImageDraw

    img = image.convert("RGB").copy()
    draw = ImageDraw.Draw(img)
    for b in detection.get("boxes", []):
        draw.rectangle([b["fx"], b["fy"], b["fx"] + b["fw"], b["fy"] + b["fh"]],
                       outline=(0, 230, 0), width=2)
        draw.text((b["fx"] + 3, b["fy"] + 2), f"r{b['row'] + 1}c{b['col'] + 1}", fill=(0, 230, 0))
    return img


def _match_slot(text: str) -> str | None:
    letters = re.sub(r"[^A-Za-z]", "", text).upper()
    for cand in ("HT", "IF", "OF", "P"):  # HT/IF/OF before P (P is a substring)
        if cand in letters:
            return SLOT_TO_ROSTER[cand]
    return None


def _read_slot(image, box: dict) -> str | None:
    """Read the position label from the right strip of a box.

    Crops straight from the box's own right edge (NO outward padding) so an
    inner column never bleeds into the next drafter's box. The label sits
    top-right under the pick number; the single-letter 'P' is finicky, so we try
    a few strip starts / upscales / inversion and take the first valid slot.
    """
    from PIL import ImageOps

    fx, fy, fw, fh = box["fx"], box["fy"], box["fw"], box["fh"]
    for frac in (0.58, 0.64, 0.70):
        x0 = fx + int(fw * frac)
        if x0 >= fx + fw - 3:
            continue
        strip = image.crop((x0, fy, fx + fw, fy + fh))
        for factor in (4, 6, 8):
            up = _upscale_contrast(strip, factor)
            # Board text is light-on-dark; Tesseract prefers dark-on-light.
            for img in (ImageOps.invert(up), up):
                slot = _match_slot(_ocr_block(img, "0123456789PIFOHT"))
                if slot:
                    return slot
    return None


def _read_box(image, box: dict) -> dict:
    """OCR one detected box and return {name, team, roster_slot}.

    The pick number is NOT read here — it's computed from the box's (row, col)
    via the snake order, which is far more reliable than OCR'ing a tiny digit.
    """
    # Pad outward a little: detection erodes the mask, so the raw bbox can clip
    # edge text (especially the right-hand position label).
    pad_x = max(2, int(box["fw"] * 0.03))
    pad_y = max(2, int(box["fh"] * 0.06))
    x0 = max(box["fx"] - pad_x, 0)
    y0 = max(box["fy"] - pad_y, 0)
    x1 = min(box["fx"] + box["fw"] + pad_x, image.width)
    y1 = min(box["fy"] + box["fh"] + pad_y, image.height)
    crop = image.crop((x0, y0, x1, y1))

    proc = _preprocess(crop)
    base = parse_cell_words(_cell_words(proc), proc.size[0], proc.size[1])
    slot = base["roster_slot"] or _read_slot(image, box)
    return {"name": base["name"], "team": base["team"], "roster_slot": slot}


def _read_headers(image, detection: dict) -> list[str]:
    """Read the drafter name strip above the first row, per column."""
    cols: dict[int, list[dict]] = defaultdict(list)
    for b in detection["boxes"]:
        cols[b["col"]].append(b)
    header_h = max(detection.get("header_h", 0), 1)
    names = []
    for i in sorted(cols):
        bs = cols[i]
        x0 = min(b["fx"] for b in bs)
        x1 = max(b["fx"] + b["fw"] for b in bs)
        crop = image.crop((x0, 0, x1, header_h))
        names.append(_ocr_line(_preprocess(crop)))
    return names


def ocr_board(image_bytes: bytes, threshold: int = 35) -> dict:
    """Detect + OCR a board screenshot.

    Returns {"drafters": [...], "n_drafters": int, "n_rounds": int,
             "picks": [{seat, round, pick_number, name, team, roster_slot, box}]}.
    Seat/round come from the box's column/row; the pick number is read from the
    box's top-right corner. Raises RuntimeError if OCR is unavailable or no boxes
    are found.
    """
    ok, msg = ocr_available()
    if not ok:
        raise RuntimeError(msg)

    from . import draft  # snake order; computed pick# beats OCR'ing a tiny digit

    image = _open_image(image_bytes)
    detection = detect_boxes(image, threshold)
    if not detection["boxes"]:
        raise RuntimeError(
            "No draft boxes detected — try adjusting the brightness slider, or use manual entry."
        )

    n_drafters = detection["n_cols"]
    picks = []
    for b in detection["boxes"]:
        info = _read_box(image, b)
        seat, rnd = b["col"] + 1, b["row"] + 1
        picks.append({
            "seat": seat, "round": rnd,
            "pick_number": draft.snake_overall(rnd, seat, n_drafters),
            "name": info["name"], "team": info["team"], "roster_slot": info["roster_slot"],
            "box": (b["fx"], b["fy"], b["fw"], b["fh"]),
        })
    return {
        "drafters": _read_headers(image, detection),
        "n_drafters": detection["n_cols"],
        "n_rounds": detection["n_rows"],
        "picks": picks,
        "detection": detection,
    }
