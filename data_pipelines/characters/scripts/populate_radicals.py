"""
Offline script: parses radical-data.csv and inserts radicals into the same
characters.db used by populate_characters.py / populate_confusibles.py.

Radicals are stored as ordinary rows in the `characters` table (is_radical=1)
so they share the same strength-tracking / lookup machinery as regular
characters. Radical-specific metadata (pinyin, english meaning, stroke count,
radical number) goes in a separate `radical_meta` table, since that data
doesn't apply to non-radical characters.

Variant characters (visual confusibles of a radical, e.g. 乀/乁 for 丿) are
inserted into confusion_pairs with source='radical_variant'.

Run AFTER populate_characters.py and populate_confusibles.py:
    python populate_radicals.py

Input:  ids-app-data/data/raw/radical-data.csv
Output: ids-app-data/data/clean/characters.db (adds is_radical column,
        radical_meta table, and radical_variant confusion pairs)

CSV format:
    number,radical,variants,simplifiedradical,pinyin,english,strokecount
    1,一,,,yi1,one,1
    4,丿,"乀 (fu2), 乁(yi2)",,pie3,slash,1
"""

import csv
import re
import sqlite3
from pathlib import Path
from app.core.config import OUTPUT_DB_PATH, RAW_RADICALS_PATH

# ---------------------------------------------------------------------------
# PATHS — must match populate_characters.py layout
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
IDS_APP_DATA_DIR = SCRIPT_DIR.parent
TOP_DIR = SCRIPT_DIR.parent.parent.parent # learning-mandarin

OUTPUT_DB_PATH = TOP_DIR / "data" / "characters" / "characters.db"

RAW_RADICALS_PATH = IDS_APP_DATA_DIR / "data" / "raw" / "radical-data.csv"

# Matches a single CJK character, used to pull just the glyph out of entries
# like "乀 (fu2)" or "乁(yi2)" in the variants column.
CJK_CHAR_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u2e80-\u2eff\u31c0-\u31ef]"
)

# ---------------------------------------------------------------------------
# Schema additions
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS radical_meta (
    char          TEXT PRIMARY KEY,
    radical_number INTEGER,
    pinyin        TEXT,
    english       TEXT,
    stroke_count  INTEGER,
    FOREIGN KEY (char) REFERENCES characters(char)
);
"""


def ensure_is_radical_column(conn: sqlite3.Connection):
    """characters.db was created by populate_characters.py without an
    is_radical column — add it if missing. Safe to re-run."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(characters)")]
    if "is_radical" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN is_radical INTEGER DEFAULT 0")


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------
def extract_variant_chars(variants_field: str) -> list[str]:
    """'乀 (fu2), 乁(yi2)' -> ['乀', '乁']"""
    if not variants_field or not variants_field.strip():
        return []
    return CJK_CHAR_RE.findall(variants_field)


def parse_radical_csv(path: Path) -> list[dict]:
    radicals = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            radical_char = (row.get("radical") or "").strip()
            if not radical_char:
                continue

            radicals.append({
                "number": int(row["number"]) if row.get("number") else None,
                "radical": radical_char,
                "variants": extract_variant_chars(row.get("variants", "")),
                "simplified": (row.get("simplifiedradical") or "").strip() or None,
                "pinyin": (row.get("pinyin") or "").strip() or None,
                "english": (row.get("english") or "").strip() or None,
                "stroke_count": int(row["strokecount"]) if row.get("strokecount") else None,
            })
    return radicals


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def populate(radicals: list[dict], db_path: Path):
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found at {db_path}. Run populate_characters.py first."
        )

    conn = sqlite3.connect(db_path)
    ensure_is_radical_column(conn)
    conn.executescript(SCHEMA_SQL)

    # Wipe existing radical rows/metadata for idempotent re-runs
    conn.execute("DELETE FROM radical_meta")
    conn.execute("DELETE FROM confusion_pairs WHERE source = 'radical_variant'")

    char_rows = []
    meta_rows = []
    confusion_rows = []

    for r in radicals:
        char = r["radical"]
        codepoint = f"U+{ord(char):04X}"

        char_rows.append((codepoint, char, char, None))  # ids_raw = char itself (atomic)
        meta_rows.append((char, r["number"], r["pinyin"], r["english"], r["stroke_count"]))

        for variant in r["variants"]:
            # Ensure the variant itself exists as a character row too, so it's
            # queryable/confusible even if it never appears in vocab
            variant_codepoint = f"U+{ord(variant):04X}"
            char_rows.append((variant_codepoint, variant, variant, None))

            pair = (char, variant) if char < variant else (variant, char)
            confusion_rows.append(pair)

    # Insert/update character rows (OR IGNORE preserves existing rows from
    # populate_characters.py — we only add rows that don't already exist)
    conn.executemany(
        "INSERT OR IGNORE INTO characters (codepoint, char, ids_raw, decomp_operator) VALUES (?, ?, ?, ?)",
        char_rows,
    )

    # Flag every radical (and its variants) as is_radical, whether newly
    # inserted or already present from the main character set
    all_radical_chars = [r["radical"] for r in radicals] + [
        v for r in radicals for v in r["variants"]
    ]
    conn.executemany(
        "UPDATE characters SET is_radical = 1 WHERE char = ?",
        [(c,) for c in all_radical_chars],
    )

    conn.executemany(
        "INSERT INTO radical_meta (char, radical_number, pinyin, english, stroke_count) VALUES (?, ?, ?, ?, ?)",
        meta_rows,
    )

    conn.executemany(
        "INSERT OR IGNORE INTO confusion_pairs (char_a, char_b, source) VALUES (?, ?, 'radical_variant')",
        confusion_rows,
    )

    conn.commit()
    conn.close()

    return len(char_rows), len(meta_rows), len(confusion_rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"Parsing radicals from: {RAW_RADICALS_PATH}")
    radicals = parse_radical_csv(RAW_RADICALS_PATH)
    print(f"Found {len(radicals)} radicals.")

    n_chars, n_meta, n_confusions = populate(radicals, OUTPUT_DB_PATH)
    print(f"Upserted {n_chars} character rows (radicals + variants).")
    print(f"Inserted {n_meta} radical_meta rows.")
    print(f"Inserted {n_confusions} radical_variant confusion pairs.")


if __name__ == "__main__":
    main()