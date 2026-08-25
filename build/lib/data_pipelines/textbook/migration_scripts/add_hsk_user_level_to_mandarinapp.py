from app.core.config.data import MANDARIN_APP_DB

"""
Adds `hsk_level` to `users` in the session/progress database. Existing users 
default to 1, since that's what everyone's been learning so far.

Plain ADD COLUMN this time -- no unique constraint involved, so unlike the
sentence_vocab / units migrations this doesn't need a rebuild-and-copy.

Usage:
    python migrate_add_user_hsk_level.py --check
    python migrate_add_user_hsk_level.py
"""
import argparse
import sqlite3
import sys
import os


def has_column(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def check(db_path: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        if has_column(conn, "users", "hsk_level"):
            print("  users.hsk_level already present. No migration needed.")
            return False
        n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        print(f"  users.hsk_level is missing. {n} user row(s) will default to hsk_level=1.")
        return True
    finally:
        conn.close()


def migrate(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        if has_column(conn, "users", "hsk_level"):
            print("  Already migrated; nothing to do.")
            return

        conn.execute("ALTER TABLE users ADD COLUMN hsk_level INTEGER NOT NULL DEFAULT 1")
        conn.commit()
        print("  Added users.hsk_level (NOT NULL, default 1).")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=MANDARIN_APP_DB, help="Path to the database (defaults to DATABASE_URL)")
    parser.add_argument("--check", action="store_true", help="Check if migration is needed without applying it")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"Database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    print(f"Database: {args.db}\n")
    if args.check:
        sys.exit(1 if check(args.db) else 0)

    if check(args.db):
        migrate(args.db)


if __name__ == "__main__":
    main()