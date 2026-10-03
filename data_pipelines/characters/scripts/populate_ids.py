"""
Offline script: parses the raw IDS (Ideographic Description Sequence) file,
filters it down to only the characters that actually appear in the app's
HSK vocab (now a SQLite DB, not JSON), recursively decomposes each
character (up to MAX_DEPTH), and writes the result into a SQLite database.

This is meant to be run manually, NOT at app startup:
    python populate_characters.py

Output: ids-app-data/data/clean/characters.db

Assumed folder layout (adjust PATHS below if different):

    learning-mandarin/
    ├── app/
    │   └── language-app-data/
    │       └── data/clean/vocab.db      <- input, sqlite DB with a `vocab`
    │                                       table (see TEXTBOOK_DB); each row's
    │                                       `hanzi` column is a vocab word
    └── ids-app-data/
        ├── scripts/
        │   └── populate_characters.py   <- this file
        └── data/
            ├── raw/dictionary.txt       <- input, one JSON object per line:
            │                               {"character":"萬","definition":"...",
            │                                "pinyin":["wàn"],"decomposition":"⿱艹禺",
            │                                "radical":"艹","matches":[...]}
            │                               A decomposition that is (or starts
            │                               with) the full-width '？' means
            │                               unknown/invalid.
            └── clean/characters.db      <- output (created by this script)
"""

import json
import re
import sqlite3
from pathlib import Path
from app.core.config.characters import RAW_IDS_PATH
from app.core.config.data import CHARACTERS_DB
from app.core.config.data import TEXTBOOK_DB
#from __future__ import annotations


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
def load_target_characters(textbook_db_path: Path) -> set[str]:
    """Extract the set of unique CJK characters used across every vocab
    word in the `vocab` table of the (now SQLite, formerly JSON) vocab DB."""
    conn = sqlite3.connect(textbook_db_path)
    try:
        rows = conn.execute("SELECT hanzi FROM vocab").fetchall()
    finally:
        conn.close()

    chars: set[str] = set()
    for (hanzi,) in rows:
        if hanzi:
            chars.update(CJK_CHAR_RE.findall(hanzi))

    return chars


# ---------------------------------------------------------------------------
# Step 2: parse the raw IDS file into a lookup: char -> (ids_raw, codepoint)
# ---------------------------------------------------------------------------
# {char: (codepoint, decomposition, definition, pinyin_csv, radical)}
IdsEntry = tuple[str, str, str | None, str, str | None]


def load_ids_table(raw_ids_path: Path) -> dict[str, IdsEntry]:
    """Returns {char: (codepoint, decomposition, definition, pinyin_csv,
    radical)} for every line in dictionary.txt, unfiltered. We keep the
    whole thing in memory so recursive lookups (for depth 1/2) can find
    components that aren't themselves in the vocab list.

    dictionary.txt is '\n'-separated JSON objects, one per character, e.g.:
        {"character":"萬","definition":"...","pinyin":["wan4"],
         "decomposition":"⿱艹禺","radical":"艹", ...}
    The codepoint isn't given explicitly, so it's derived from the
    character itself. A decomposition that is missing or starts with the
    full-width '？' means "unknown" and is treated the same as an atomic
    character (nothing further to decompose). `pinyin` is stored as a
    comma-separated string; `definition` is optional and may be None."""
    table: dict[str, IdsEntry] = {}
    with open(raw_ids_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            entry = json.loads(line)
            char = entry.get("character")
            if not char:
                continue
            decomposition = entry.get("decomposition") or ""
            codepoint = f"U+{ord(char):04X}"
            definition = entry.get("definition")
            pinyin = ",".join(entry.get("pinyin") or [])
            radical = entry.get("radical")
            table[char] = (codepoint, decomposition, definition, pinyin, radical)
    return table


# ---------------------------------------------------------------------------
# Step 3: decomposition
# ---------------------------------------------------------------------------
def split_ids_components(ids_string: str) -> tuple[str | None, list[str]]:
    """Given an IDS string like '⿱艹禺', return (operator, [components]).
    If the string is empty, is a bare atomic character, or is/starts with
    the full-width '？' (unknown decomposition), returns (None, [])."""
    if not ids_string or ids_string[0] == "？":
        return None, []

    first = ids_string[0]
    if first in IDS_OPERATORS:
        # Everything after the operator is the components, in sequence.
        # Components can themselves be multi-character IDS substrings,
        # but for our purposes each component is a single CJK char/atom.
        # Some components may individually be '？' (unrecognized), which
        # callers should skip rather than treat as a real character.
        components = list(ids_string[1:])
        return first, components

    # No operator: it's an atomic character.
    return None, []


def recursive_decompose(
    char: str,
    ids_table: dict[str, IdsEntry],
    depth: int,
    max_depth: int,
    rows: list[dict],
    root_char: str,
):
    """Populates `rows` with component relationships for `root_char`,
    walking down from `char` at the given `depth`."""
    if depth > max_depth:
        return

    entry = ids_table.get(char)
    ids_raw = entry[1] if entry is not None else char
    operator, components = split_ids_components(ids_raw)

    if operator is None:
        # Atomic — nothing further to decompose at this branch.
        return

    positions = OPERATOR_POSITIONS.get(operator, [f"part_{i}" for i in range(len(components))])

    for i, comp in enumerate(components):
        if comp == "？":
            # Unrecognized component in an otherwise-valid decomposition;
            # nothing real to record or recurse into.
            continue

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
    decomp_operator TEXT,
    definition TEXT,
    pinyin TEXT,
    radical TEXT
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
    ids_table: dict[str, IdsEntry],
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

        codepoint, ids_raw, definition, pinyin, radical = entry
        operator, _components = split_ids_components(ids_raw)
        char_rows.append((codepoint, char, ids_raw, operator, definition, pinyin, radical))

        recursive_decompose(char, ids_table, depth=0, max_depth=max_depth, rows=component_rows, root_char=char)

    conn.executemany(
        """INSERT OR IGNORE INTO characters
               (codepoint, char, ids_raw, decomp_operator, definition, pinyin, radical)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
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
    print(f"Loading vocab from: {TEXTBOOK_DB}")
    target_chars = load_target_characters(TEXTBOOK_DB)
    print(f"Found {len(target_chars)} unique characters across vocab list.")

    print(f"Loading IDS table from: {RAW_IDS_PATH}")
    ids_table = load_ids_table(RAW_IDS_PATH)
    print(f"IDS table has {len(ids_table)} total entries.")

    char_rows, component_rows, missing = build_database(
        target_chars, ids_table, CHARACTERS_DB, MAX_DEPTH
    )

    print(f"Inserted {len(char_rows)} characters.")
    print(f"Inserted {len(component_rows)} component relationships (depth 0-{MAX_DEPTH}).")

    if missing:
        print(f"\nWARNING: {len(missing)} characters from vocab had no IDS entry:")
        print(" ".join(missing))

    print(f"\nDatabase written to: {CHARACTERS_DB}")


if __name__ == "__main__":
    main()