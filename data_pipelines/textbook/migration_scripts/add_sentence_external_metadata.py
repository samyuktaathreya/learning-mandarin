# migration_scripts/add_sentence_external_metadata.py
"""
One-time migration: adds three nullable columns to `sentences` so
external-source imports (e.g. data_pipelines/external_sources/
hsk_sentences_audio) can carry metadata the textbook/workbook pipeline
never needed.

  sentences.audio_url    TEXT NULL  -- e.g. "audio/hsk1-0002.mp3"
  sentences.topic        TEXT NULL  -- e.g. "greetings", "food"
  sentences.external_id  TEXT NULL  -- e.g. "hsk1-0002", traceability to source

All three are nullable with no default constraint beyond NULL, so this is
purely additive: existing rows (all from textbook/workbook) get NULL in
these columns and every existing query/read path keeps working unchanged.
SQLite's `ALTER TABLE ... ADD COLUMN` doesn't support adding a column with
an index directly, so `external_id`'s index is created as a separate
statement (matches Column(..., index=True) in models.py).

Safe to re-run: checks each column's existence via `PRAGMA table_info`
before adding it, so running this twice is a no-op the second time.

Usage:
    python migration_scripts/add_sentence_external_metadata.py
"""
import sqlite3
from app.core.config.data import TEXTBOOK_DB


NEW_COLUMNS = [
    ("audio_url", "TEXT"),
    ("topic", "TEXT"),
    ("external_id", "TEXT"),
]


def get_existing_columns(cursor, table: str) -> set:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def main():
    conn = sqlite3.connect(str(TEXTBOOK_DB))
    cursor = conn.cursor()

    existing = get_existing_columns(cursor, "sentences")

    added = []
    for col_name, col_type in NEW_COLUMNS:
        if col_name in existing:
            print(f"  [skip] sentences.{col_name} already exists")
            continue
        cursor.execute(f"ALTER TABLE sentences ADD COLUMN {col_name} {col_type}")
        added.append(col_name)
        print(f"  [added] sentences.{col_name} {col_type}")

    # index on external_id, mirroring Column(..., index=True) in models.py
    cursor.execute("PRAGMA index_list(sentences)")
    existing_indexes = {row[1] for row in cursor.fetchall()}
    index_name = "ix_sentences_external_id"
    if index_name not in existing_indexes:
        cursor.execute(f"CREATE INDEX {index_name} ON sentences (external_id)")
        print(f"  [added] index {index_name}")
    else:
        print(f"  [skip] index {index_name} already exists")

    conn.commit()
    conn.close()

    if added:
        print(f"\n✅ Migration complete. Added: {', '.join(added)}")
    else:
        print("\n✅ Nothing to do -- schema already up to date.")


if __name__ == "__main__":
    main()