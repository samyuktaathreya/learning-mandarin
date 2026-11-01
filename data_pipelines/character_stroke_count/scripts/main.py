"""
Build a character -> stroke count lookup from the Unihan database.

Layout (data/ and scripts/ are siblings):
    project/
        scripts/build_stroke_counts.py   <- this file
        data/raw/Unihan/                 <- unzipped Unihan.zip (*.txt files)
        data/clean/stroke_counts.json    <- output

Usage:
    python scripts/build_stroke_counts.py
    python scripts/build_stroke_counts.py --input data/raw/SomeOtherFolder

Output is a flat JSON object: {"晴": 12, "人": 2, ...}
When Unihan lists two counts (simplified first, traditional second),
the first (simplified) count is used.
"""

import argparse
import json
import sys
from pathlib import Path
from app.core.config.shared import DATA_DIR

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
CLEAN_DIR = ROOT / "data" / "clean"
OUTPUT_FILE = DATA_DIR / "stroke_counts.json"


def find_unihan_dir() -> Path:
    """Find the Unihan folder inside data/raw (case-insensitive name match)."""
    if not RAW_DIR.is_dir():
        sys.exit(f"Missing folder: {RAW_DIR}")
    matches = [p for p in RAW_DIR.iterdir() if p.is_dir() and "unihan" in p.name.lower()]
    if not matches:
        sys.exit(f"No folder with 'unihan' in its name found in {RAW_DIR}")
    if len(matches) > 1:
        names = ", ".join(p.name for p in matches)
        sys.exit(f"Several Unihan folders found ({names}); pick one with --input")
    return matches[0]


def parse_stroke_counts(unihan_dir: Path) -> dict[str, int]:
    """Scan every .txt file for kTotalStrokes lines.

    Line format: U+6674<TAB>kTotalStrokes<TAB>12
    Scanning all files means it works regardless of which file
    Unicode currently keeps the field in.
    """
    txt_files = sorted(unihan_dir.rglob("*.txt"))
    if not txt_files:
        sys.exit(f"No .txt files found in {unihan_dir}")

    strokes: dict[str, int] = {}
    for path in txt_files:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 3 or parts[1] != "kTotalStrokes":
                    continue
                code, _, value = parts
                char = chr(int(code[2:], 16))
                strokes[char] = int(value.split()[0])

    if not strokes:
        sys.exit(f"No kTotalStrokes entries found in {unihan_dir}")
    return dict(sorted(strokes.items(), key=lambda kv: ord(kv[0])))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--input", type=Path, help="Unihan folder (default: auto-detect in data/raw)")
    args = parser.parse_args()

    unihan_dir = args.input.resolve() if args.input else find_unihan_dir()
    strokes = parse_stroke_counts(unihan_dir)

    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(strokes, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Read {unihan_dir}")
    print(f"Wrote {len(strokes):,} characters to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()