# data_pipelines/pinyin/migration_scripts/migrate_character.py
"""
Adds the character column to pinyin_syllables. create_all() doesn't
ALTER existing tables (see app architecture doc's migration-history
section), so this needs a hand-written migration, same as
migrate_sentence_vocab.py / migrate_add_sentence_id.py / migrate_diacritic_pinyin.py.

Safe to rerun -- checks for the column first.
"""
import sqlite3
from app.core.config.shared import DATA_DIR  # or wherever textbook.db's path constant lives

DB_PATH = f"{DATA_DIR}/textbook.db"  # confirm this matches your actual textbook.db path


def migrate():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(pinyin_syllables)")
    columns = {row[1] for row in cur.fetchall()}

    if "character" in columns:
        print("Column already exists, skipping.")
    else:
        cur.execute("ALTER TABLE pinyin_syllables ADD COLUMN character TEXT")
        conn.commit()
        print("Added character column.")

    conn.close()


if __name__ == "__main__":
    migrate()