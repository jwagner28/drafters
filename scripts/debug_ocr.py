"""Debug a real draft-board screenshot through the OCR pipeline.

Usage:
    python scripts/debug_ocr.py path/to/board.png

Prints detection results and per-box OCR, and saves a `<image>_overlay.png`
next to the input so you can see the detected boxes.
"""

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dfs import ocr  # noqa: E402


def main(path: str, threshold: int = 35) -> None:
    p = Path(path)
    image = ocr._open_image(p.read_bytes())
    print(f"Image: {p.name}  size={image.size}  mode={image.mode}")

    try:
        det = ocr.detect_boxes(image, threshold)
        print(f"Detection: n_cols={det['n_cols']} n_rows={det['n_rows']} boxes={len(det['boxes'])}")
        overlay = ocr.detect_overlay(image, det)
        out = p.with_name(p.stem + "_overlay.png")
        overlay.save(out)
        print(f"Saved overlay -> {out}")
    except Exception:
        print("detect_boxes FAILED:")
        traceback.print_exc()
        return

    try:
        result = ocr.ocr_board(p.read_bytes(), threshold)
        print(f"\nBoard: {result['n_drafters']} drafters x {result['n_rounds']} rounds; "
              f"drafters={result['drafters']}")
        for pick in sorted(result["picks"], key=lambda x: (x["round"], x["seat"])):
            print(f"  r{pick['round']}c{pick['seat']} pick={pick['pick_number']} "
                  f"name={pick['name']!r} team={pick['team']} slot={pick['roster_slot']}")
    except Exception:
        print("ocr_board FAILED:")
        traceback.print_exc()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/debug_ocr.py path/to/board.png [threshold]")
        sys.exit(1)
    thr = int(sys.argv[2]) if len(sys.argv) > 2 else 35
    main(sys.argv[1], thr)
