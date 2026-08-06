"""
Adds the `sentence_id` column to `questions`, which is missing on any DB
created before Question.sentence_id existed in the model (see
"no such column: questions.sentence_id" -- Base.metadata.create_all() only
creates NEW tables, it never ALTERs existing ones to add columns).

Unlike the sentence_vocab PK migration, this one is a plain ADD COLUMN --
SQLite supports that directly, no rebuild-the-table dance needed.

Usage:
    python migrate_add_sentence_id.py
    python migrate_add_sentence_id.py --check
    python migrate_add_sentence_id.py --db custom/path/to/textbook.db
"""
import argparse
import sqlite3
import sys
import os

from app.core.config.textbook import DATABASE_FILEPATH


def has_column(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def check(db_path: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        if not has_column(conn, "questions", "sentence_id"):
            print("  ✗ questions.sentence_id is missing.")
            return True
        print("  ✓ questions.sentence_id already present. No migration needed.")
        return False
    finally:
        conn.close()


def migrate(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        if has_column(conn, "questions", "sentence_id"):
            print("  ✓ Already migrated; nothing to do.")
            return

        conn.execute("ALTER TABLE questions ADD COLUMN sentence_id INTEGER REFERENCES sentences(id)")
        conn.commit()
        print("  ✓ Added questions.sentence_id (nullable, FK to sentences.id).")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", default=DATABASE_FILEPATH, help="Path to SQLite database file")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"✗ Database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    print(f"Database: {args.db}\n")
    if args.check:
        sys.exit(1 if check(args.db) else 0)

    if check(args.db):
        migrate(args.db)


if __name__ == "__main__":
    main()