"""
Per-HSK-level source configuration shared by sentence_parser.py and
fitb_parser.py (both OCR the same unit pages, so they must agree on layout).

Each HSK level has its own textbook and workbook PDF
(.../data/raw/hsk_textbook/{level}.pdf, .../data/raw/hsk_workbook/{level}.pdf),
and each book has its own page layout, so the unit page ranges live here
keyed by level, then by source.

Structure:
  SOURCE_DEFAULTS[source]      -> settings shared by every level of that
                                  source (raw subdir, OCR SOP)
  SOURCES[hsk_level][source]   -> that level's book layout. Any key from
                                  SOURCE_DEFAULTS can also be set here to
                                  override it for just that level (e.g. a
                                  different OCR SOP for a book with a
                                  different format).

Adding a new level: copy the HSK1 block, fill in the page numbers for that
level's books, and leave out a source entirely if that level doesn't have
one (sentence_parser.py and fitb_parser.py will skip it with a warning).

Page numbers are 1-indexed PDF page numbers (as shown in a PDF viewer),
not the printed page numbers in the book.
"""

import os
from typing import Optional

from app.core.config.textbook import SOP_PATH

SOURCE_NAMES = ("textbook", "workbook")

REQUIRED_KEYS = (
    "RAW_SUBDIR",
    "OCR_SOP_FILENAME",
    "UNIT_STARTS",
    "LAST_UNIT_END_PAGE",
    "FIRST_UNIT_NUMBER",
)

# --------------------------------- SHARED PER-SOURCE SETTINGS ---------------------------------

SOURCE_DEFAULTS = {
    "textbook": {
        "RAW_SUBDIR": "hsk_textbook",
        "OCR_SOP_FILENAME": SOP_PATH / "sentence_parser" / "ocr.txt",
    },
    "workbook": {
        "RAW_SUBDIR": "hsk_workbook",
        "OCR_SOP_FILENAME": os.path.join("workbook_parser", "ocr.txt"),
    },
}

# --------------------------------- PER-LEVEL BOOK LAYOUTS ---------------------------------

SOURCES = {
    1: {
        "textbook": {
            "UNIT_STARTS": [34, 42, 50, 60, 68, 76, 84, 92, 102, 110, 118, 124, 132],
            "LAST_UNIT_END_PAGE": 139,
            "FIRST_UNIT_NUMBER": 3,
        },
        "workbook": {
            "UNIT_STARTS": [15, 23, 31, 39, 47, 55, 63, 71, 87, 96, 105, 113],
            "LAST_UNIT_END_PAGE": 120,
            "FIRST_UNIT_NUMBER": 3,
        },
    },
    2: {
        "textbook": {
            "UNIT_STARTS": [21, 29, 37, 45, 53, 61, 69, 77, 85, 93, 101, 109, 117, 125, 133],
            "LAST_UNIT_END_PAGE": 139,
            "FIRST_UNIT_NUMBER": 1,
        },
        "workbook": {
            "UNIT_STARTS": [8, 18, 28, 38, 48, 58, 68, 77, 86, 95, 104, 113, 122, 131, 140],
            "LAST_UNIT_END_PAGE": 148,
            "FIRST_UNIT_NUMBER": 1,
        },
    },
    # 2: {
    #     "textbook": {
    #         "UNIT_STARTS": [...],
    #         "LAST_UNIT_END_PAGE": ...,
    #         "FIRST_UNIT_NUMBER": ...,
    #     },
    #     "workbook": {
    #         "UNIT_STARTS": [...],
    #         "LAST_UNIT_END_PAGE": ...,
    #         "FIRST_UNIT_NUMBER": ...,
    #     },
    # },
}


# --------------------------------- LOOKUP HELPERS ---------------------------------

def configured_sources(hsk_level: int) -> list:
    """Sources that have a layout configured for this level.
    Raises if the level itself isn't configured at all."""
    if hsk_level not in SOURCES:
        raise ValueError(
            f"No source config for HSK level {hsk_level}. "
            f"Configured levels: {sorted(SOURCES)}. "
            f"Add it to SOURCES in sentence_parser_constants.py."
        )
    return [s for s in SOURCE_NAMES if s in SOURCES[hsk_level]]


def get_source_config(hsk_level: int, source: str) -> Optional[dict]:
    """Merged config (shared defaults + this level's layout) for one book,
    or None if this level has no entry for that source."""
    level_cfg = SOURCES.get(hsk_level)
    if level_cfg is None or source not in level_cfg:
        return None

    cfg = {**SOURCE_DEFAULTS.get(source, {}), **level_cfg[source]}

    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"HSK{hsk_level} {source} config is missing keys: {missing}")

    starts = cfg["UNIT_STARTS"]
    if not starts or starts != sorted(starts):
        raise ValueError(f"HSK{hsk_level} {source} UNIT_STARTS must be non-empty and ascending: {starts}")
    if cfg["LAST_UNIT_END_PAGE"] < starts[-1]:
        raise ValueError(
            f"HSK{hsk_level} {source} LAST_UNIT_END_PAGE ({cfg['LAST_UNIT_END_PAGE']}) "
            f"is before the last unit start ({starts[-1]})"
        )
    return cfg