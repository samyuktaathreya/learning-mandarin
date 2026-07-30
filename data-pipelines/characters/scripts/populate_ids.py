"""
Offline script: parses the raw IDS (Ideographic Description Sequence) file,
filters it down to only the characters that actually appear in the app's
HSK vocab lists, recursively decomposes each character (up to MAX_DEPTH),
and writes the result into a SQLite database.

This is meant to be run manually, NOT at app startup:
    python populate_characters.py

Output: ids-app-data/data/clean/characters.db

Assumed folder layout (adjust PATHS below if different):

    learning-mandarin/
    ├── app/
    │   └── language-app-data/
    │       └── data/clean/unit_vocab_tags.json
    └── ids-app-data/
        ├── scripts/
        │   └── populate_characters.py   <- this file
        └── data/
            ├── raw/ids.txt              <- input, one line per char:
            │                               U+842C  萬      ⿱艹禺
            └── clean/characters.db      <- output (created by this script)
"""

import json
import re
import sqlite3
from pathlib import Path
#from __future__ import annotations

# ---------------------------------------------------------------------------
# PATHS — adjust here if your folder layout differs
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
IDS_APP_DATA_DIR = SCRIPT_DIR.parent  # data-pipelines/characters/
REPO_ROOT = IDS_APP_DATA_DIR.parent.parent   # learning-mandarin/

RAW_IDS_PATH = IDS_APP_DATA_DIR / "data" / "raw" / "ids.txt"
VOCAB_JSON_PATH = REPO_ROOT / "app" / "language-app-data" / "data" / "clean" / "unit_vocab_tags.json"

TOP_DIR = SCRIPT_DIR.parent.parent.parent # learning-mandarin
OUTPUT_DB_PATH = TOP_DIR / "data" / "characters" / "characters.db"

MAX_DEPTH = 2  # per discussion: depth > 2 stops looking visually similar

# IDS operators -> named positions for their components, in the order
# components appear in the IDS string.
# https://en.wikipedia.org/wiki/Ideographic_Description_Characters
OPERATOR_POSITIONS = {
    "⿰": ["left", "right"],
    "⿱": ["top", "bottom"],
    "⿲": ["left", "middle", "right"],
    "⿳": ["top", "middle", "bottom"],
    "⿴": ["enclosing", "nested"],
    "⿵": ["enclosing_top", "nested"],
    "⿶": ["enclosing_bottom", "nested"],
    "⿷": ["enclosing_left", "nested"],
    "⿸": ["enclosing_top_left", "nested"],
    "⿹": ["enclosing_top_right", "nested"],
    "⿺": ["enclosing_bottom_left", "nested"],
    "⿻": ["overlaid_1", "overlaid_2"],
}
IDS_OPERATORS = set(OPERATOR_POSITIONS.keys())

CJK_CHAR_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u20000-\u2a6df\u2a700-\u2b73f]"
)


# ---------------------------------------------------------------------------
# Step 1: figure out which characters we actually care about
# ---------------------------------------------------------------------------
def load_target_characters(vocab_json_path: Path) -> set[str]:
    """Extract the set of unique CJK characters used across all HSK/unit
    vocab words in the JSON file."""
    with open(vocab_json_path, "r", encoding="utf-8") as f:
        unit_vocab = json.load(f)

    chars: set[str] = set()
    for _unit, words in unit_vocab.items():
        for word in words:
            chars.update(CJK_CHAR_RE.findall(word))

    return chars


# ---------------------------------------------------------------------------
# Step 2: parse the raw IDS file into a lookup: char -> (ids_raw, codepoint)
# ---------------------------------------------------------------------------
def load_ids_table(raw_ids_path: Path) -> dict[str, tuple[str, str]]:
    """Returns {char: (codepoint, ids_raw_string)} for every line in the
    IDS file, unfiltered. We keep the whole thing in memory so recursive
    lookups (for depth 1/2) can find components that aren't themselves
    in the vocab list."""
    table: dict[str, tuple[str, str]] = {}
    with open(raw_ids_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            codepoint, char, ids_raw = parts[0], parts[1], parts[2]
            table[char] = (codepoint, ids_raw)
    return table


# ---------------------------------------------------------------------------
# Step 3: decomposition
# ---------------------------------------------------------------------------
def split_ids_components(ids_string: str) -> tuple[str | None, list[str]]:
    """Given an IDS string like '⿱艹禺', return (operator, [components]).
    If the string is a single character with no operator (atomic char,
    ids_raw == char itself), returns (None, [])."""
    if not ids_string:
        return None, []

    first = ids_string[0]
    if first in IDS_OPERATORS:
        # Everything after the operator is the components, in sequence.
        # Components can themselves be multi-character IDS substrings,
        # but for our purposes each component is a single CJK char/atom.
        components = list(ids_string[1:])
        return first, components

    # No operator: it's an atomic character (ids_raw == char itself, e.g. 千 千)
    return None, []


def recursive_decompose(
    char: str,
    ids_table: dict[str, tuple[str, str]],
    depth: int,
    max_depth: int,
    rows: list[dict],
    root_char: str,
):
    """Populates `rows` with component relationships for `root_char`,
    walking down from `char` at the given `depth`."""
    if depth > max_depth:
        return

    _codepoint, ids_raw = ids_table.get(char, (None, char))
    operator, components = split_ids_components(ids_raw)

    if operator is None:
        # Atomic — nothing further to decompose at this branch.
        return

    positions = OPERATOR_POSITIONS.get(operator, [f"part_{i}" for i in range(len(components))])

    for i, comp in enumerate(components):
        position = positions[i] if i < len(positions) else f"part_{i}"

        rows.append(
            {
                "char": root_char,
                "component_char": comp,
                "depth": depth,
                "position": position,
            }
        )

        # Recurse into this component's own decomposition, if we have data
        # for it and haven't hit max depth.
        if depth + 1 <= max_depth and comp in ids_table:
            recursive_decompose(comp, ids_table, depth + 1, max_depth, rows, root_char)


# ---------------------------------------------------------------------------
# Step 4: build the SQLite database
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS characters (
    codepoint TEXT PRIMARY KEY,
    char TEXT UNIQUE NOT NULL,
    ids_raw TEXT,
    decomp_operator TEXT
);

CREATE TABLE IF NOT EXISTS character_components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    char TEXT NOT NULL,
    component_char TEXT NOT NULL,
    depth INTEGER NOT NULL,
    position TEXT,
    frequency_in_corpus INTEGER DEFAULT 0,
    FOREIGN KEY (char) REFERENCES characters(char)
);

CREATE INDEX IF NOT EXISTS idx_component_lookup
    ON character_components (component_char);

CREATE INDEX IF NOT EXISTS idx_char_lookup
    ON character_components (char);
"""


def build_database(
    target_chars: set[str],
    ids_table: dict[str, tuple[str, str]],
    output_db_path: Path,
    max_depth: int,
):
    output_db_path.parent.mkdir(parents=True, exist_ok=True)
    if output_db_path.exists():
        output_db_path.unlink()  # rebuild clean each run

    conn = sqlite3.connect(output_db_path)
    conn.executescript(SCHEMA_SQL)

    char_rows = []
    component_rows: list[dict] = []
    missing_chars = []

    for char in sorted(target_chars):
        entry = ids_table.get(char)
        if entry is None:
            missing_chars.append(char)
            continue

        codepoint, ids_raw = entry
        operator, _components = split_ids_components(ids_raw)
        char_rows.append((codepoint, char, ids_raw, operator))

        recursive_decompose(char, ids_table, depth=0, max_depth=max_depth, rows=component_rows, root_char=char)

    conn.executemany(
        "INSERT OR IGNORE INTO characters (codepoint, char, ids_raw, decomp_operator) VALUES (?, ?, ?, ?)",
        char_rows,
    )

    conn.executemany(
        "INSERT INTO character_components (char, component_char, depth, position) VALUES (?, ?, ?, ?)",
        [(r["char"], r["component_char"], r["depth"], r["position"]) for r in component_rows],
    )

    # Compute frequency_in_corpus: how many distinct root chars use this component
    conn.execute(
        """
        UPDATE character_components
        SET frequency_in_corpus = (
            SELECT COUNT(DISTINCT cc2.char)
            FROM character_components cc2
            WHERE cc2.component_char = character_components.component_char
        )
        """
    )

    conn.commit()
    conn.close()

    return char_rows, component_rows, missing_chars


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"Loading vocab from: {VOCAB_JSON_PATH}")
    target_chars = load_target_characters(VOCAB_JSON_PATH)
    print(f"Found {len(target_chars)} unique characters across vocab list.")

    print(f"Loading IDS table from: {RAW_IDS_PATH}")
    ids_table = load_ids_table(RAW_IDS_PATH)
    print(f"IDS table has {len(ids_table)} total entries.")

    char_rows, component_rows, missing = build_database(
        target_chars, ids_table, OUTPUT_DB_PATH, MAX_DEPTH
    )

    print(f"Inserted {len(char_rows)} characters.")
    print(f"Inserted {len(component_rows)} component relationships (depth 0-{MAX_DEPTH}).")

    if missing:
        print(f"\nWARNING: {len(missing)} characters from vocab had no IDS entry:")
        print(" ".join(missing))

    print(f"\nDatabase written to: {OUTPUT_DB_PATH}")


if __name__ == "__main__":
    main()