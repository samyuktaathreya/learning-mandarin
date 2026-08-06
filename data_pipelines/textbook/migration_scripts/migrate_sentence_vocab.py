"""
Diagnostic + migration for the sentence_vocab uniqueness bug.

PROBLEM
-------
sentence_vocab was originally created with a composite PRIMARY KEY on
(sentence_id, vocab_id), which wrongly assumes a word appears at most once
per sentence. Real sentences repeat words ("我不是___，我是学生，我是___人"
uses 我 and 是 three times each), so the second occurrence fails with:

    UNIQUE constraint failed: sentence_vocab.sentence_id, sentence_vocab.vocab_id

Fixing models.py alone is NOT enough: Base.metadata.create_all() only creates
tables that don't exist yet -- it never alters an existing one. So a database
created before the fix keeps the broken constraint forever until the table is
rebuilt.

SQLite additionally cannot ALTER a PRIMARY KEY in place, so the only correct
fix is: create a new table with the right shape, copy the rows over, drop the
old one, rename. That's what migrate() does, inside a transaction.

USAGE
-----
    python migrate_sentence_vocab.py --check      # report current schema only
    python migrate_sentence_vocab.py              # run the migration
    python migrate_sentence_vocab.py --db path/to/textbook.db

The migration is safe to run more than once: if the table is already in the
correct shape, it reports that and exits without touching anything.
"""

import argparse
import sqlite3
import sys
import os


NEW_TABLE_SQL = """
CREATE TABLE sentence_vocab_new (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    sentence_id INTEGER NOT NULL,
    vocab_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(sentence_id) REFERENCES sentences (id),
    FOREIGN KEY(vocab_id) REFERENCES vocab (id),
    CONSTRAINT _sentence_position_uc UNIQUE (sentence_id, position)
)
"""


def get_schema(conn, table: str) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row[0] if row else None


def is_already_migrated(schema_sql: str) -> bool:
    """The fixed table has a surrogate `id` PK and a (sentence_id, position)
    unique constraint. Checking for the constraint name is the most direct
    signal, with the id column as a fallback for hand-rolled variants."""
    if not schema_sql:
        return False
    normalized = schema_sql.lower().replace("\n", " ")
    return "_sentence_position_uc" in normalized or (
        "id integer not null primary key" in normalized
        and "primary key (sentence_id, vocab_id)" not in normalized
    )


def check(db_path: str) -> bool:
    """Report the current schema. Returns True if migration is needed."""
    conn = sqlite3.connect(db_path)
    try:
        schema = get_schema(conn, "sentence_vocab")
        if schema is None:
            print("  sentence_vocab table does not exist yet.")
            print("  -> Nothing to migrate. Running the pipeline will create it")
            print("     correctly, as long as models.py has the fix.")
            return False

        print("  Current sentence_vocab schema:")
        for line in schema.splitlines():
            print(f"    {line}")
        print()

        count = conn.execute("SELECT COUNT(*) FROM sentence_vocab").fetchone()[0]
        print(f"  Rows currently in sentence_vocab: {count}")

        if is_already_migrated(schema):
            print("  ✓ Table already has the corrected constraint. No migration needed.")
            return False

        print("  ✗ Table still uses the OLD (sentence_id, vocab_id) constraint.")
        print("    Repeated words in a sentence will fail to insert.")
        return True
    finally:
        conn.close()


def migrate(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        schema = get_schema(conn, "sentence_vocab")
        if schema is None:
            print("  sentence_vocab doesn't exist; nothing to migrate.")
            return
        if is_already_migrated(schema):
            print("  ✓ Already migrated; nothing to do.")
            return

        before = conn.execute("SELECT COUNT(*) FROM sentence_vocab").fetchone()[0]
        print(f"  Migrating {before} row(s)...")

        # Foreign keys must be off while we swap tables, otherwise the DROP
        # of the old table can trip FK enforcement mid-migration.
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")

        conn.execute("DROP TABLE IF EXISTS sentence_vocab_new")
        conn.execute(NEW_TABLE_SQL)

        # Copy rows. If the old data somehow contains duplicate
        # (sentence_id, position) pairs -- it shouldn't, since position was
        # written from enumerate() -- this would fail loudly rather than
        # silently dropping tag occurrences, which is what we want.
        conn.execute("""
            INSERT INTO sentence_vocab_new (sentence_id, vocab_id, position)
            SELECT sentence_id, vocab_id, position FROM sentence_vocab
        """)

        conn.execute("DROP TABLE sentence_vocab")
        conn.execute("ALTER TABLE sentence_vocab_new RENAME TO sentence_vocab")

        conn.execute("COMMIT")
        conn.execute("PRAGMA foreign_keys=ON")

        after = conn.execute("SELECT COUNT(*) FROM sentence_vocab").fetchone()[0]
        print(f"  ✓ Migration complete. {after} row(s) preserved "
              f"({'no loss' if after == before else f'WARNING: was {before}'}).")

        print("\n  New schema:")
        for line in get_schema(conn, "sentence_vocab").splitlines():
            print(f"    {line}")

    except Exception as e:
        conn.execute("ROLLBACK")
        print(f"  ✗ Migration failed and was rolled back: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="textbook.db",
                        help="Path to the SQLite database (default: textbook.db)")
    parser.add_argument("--check", action="store_true",
                        help="Report the current schema without modifying anything.")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"✗ Database not found: {args.db}", file=sys.stderr)
        print("  Pass the correct path with --db path/to/textbook.db", file=sys.stderr)
        sys.exit(1)

    print(f"Database: {args.db}\n")

    if args.check:
        needed = check(args.db)
        sys.exit(1 if needed else 0)

    if check(args.db):
        print()
        migrate(args.db)


if __name__ == "__main__":
    main()