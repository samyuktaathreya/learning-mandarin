"""
Offline script: parses the human-curated hanzi confusibles file and writes
all pairwise confusion relationships into the existing characters.db SQLite
database (created by populate_characters.py).

Run AFTER populate_characters.py:
    python populate_confusibles.py

Input:  ids-app-data/data/raw/hanzi_confusibles.txt
Output: ids-app-data/data/clean/characters.db  (adds confusion_pairs table)

Confusibles file format:
    - One line per group, characters separated by tabs
    - Characters on the same line are mutually confusible
    - Lines with a single character have no known confusibles — skipped
    - Relationships are bidirectional: (A, B) implies (B, A)
"""

from pathlib import Path
import sys
from pathlib import Path

# Ensure script can find config.py one level up if executing directly
import sqlite3
from itertools import combinations

# Clean, direct import from the sibling config.py
from app.core.config import RAW_CONFUSIBLES_PATH, OUTPUT_DB_PATH

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS confusion_pairs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    char_a      TEXT NOT NULL,
    char_b      TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'human_curated',
    UNIQUE (char_a, char_b)
);

CREATE INDEX IF NOT EXISTS idx_confusion_a ON confusion_pairs (char_a);
CREATE INDEX IF NOT EXISTS idx_confusion_b ON confusion_pairs (char_b);
"""

# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------
def parse_confusibles(path: Path) -> list[tuple[str, str]]:
    """
    Returns a deduplicated list of (char_a, char_b) pairs where char_a < char_b
    (so each bidirectional pair is stored once; queries use OR on both columns).
    """
    pairs: set[tuple[str, str]] = set()

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            # Strip newline, split on tab, drop empty strings from trailing tabs
            chars = [c for c in line.rstrip("\n").split("\t") if c.strip()]
            if len(chars) < 2:
                # lone character — no known confusibles, skip
                continue
            for a, b in combinations(chars, 2):
                # Canonical order so (A,B) and (B,A) don't both end up in the set
                pair = (a, b) if a < b else (b, a)
                pairs.add(pair)

    return sorted(pairs)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def populate(pairs: list[tuple[str, str]], db_path: Path):
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found at {db_path}. "
            "Run populate_characters.py first."
        )

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)

    # Wipe existing rows so re-runs are idempotent
    conn.execute("DELETE FROM confusion_pairs")

    conn.executemany(
        "INSERT OR IGNORE INTO confusion_pairs (char_a, char_b, source) VALUES (?, ?, 'human_curated')",
        pairs,
    )

    conn.commit()

    # Report how many of these pairs overlap with characters we already track
    tracked = conn.execute("SELECT char FROM characters").fetchall()
    tracked_set = {row[0] for row in tracked}

    in_vocab  = [(a, b) for a, b in pairs if a in tracked_set or  b in tracked_set]
    both_in   = [(a, b) for a, b in pairs if a in tracked_set and b in tracked_set]

    conn.close()
    return in_vocab, both_in


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"Parsing confusibles from: {RAW_CONFUSIBLES_PATH}")
    pairs = parse_confusibles(RAW_CONFUSIBLES_PATH)
    print(f"Found {len(pairs)} unique bidirectional pairs.")

    print(f"Writing to: {OUTPUT_DB_PATH}")
    in_vocab, both_in = populate(pairs, OUTPUT_DB_PATH)

    print(f"\n{len(both_in)} pairs where BOTH characters are in your vocab.")
    print(f"{len(in_vocab)} pairs where AT LEAST ONE character is in your vocab.")
    print(f"{len(pairs) - len(in_vocab)} pairs with no overlap with current vocab (stored but won't surface in quizzes yet).")

    print("\n--- Sample pairs (both in vocab) ---")
    for a, b in both_in[:10]:
        print(f"  {a} ↔ {b}")


if __name__ == "__main__":
    main()