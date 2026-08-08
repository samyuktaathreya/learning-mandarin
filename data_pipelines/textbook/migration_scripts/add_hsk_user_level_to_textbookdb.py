"""
Migration: replace units.unit_number's global UNIQUE constraint with a
composite UNIQUE(unit_number, hsk_level), so unit numbering can restart
per HSK level (e.g. HSK2's "unit 1" and HSK1's "unit 1" are different rows)
instead of colliding.

SQLite has no ALTER TABLE ... DROP CONSTRAINT, so this uses the standard
SQLite migration pattern: create a new table with the right constraint,
copy data across, drop the old table, rename the new one into place.
Foreign keys pointing at units.id (vocab.unit_id, sentences.unit_id, etc.)
are untouched -- id is still the PK, only the UNIQUE constraint on
unit_number changes.

Idempotent: checks the existing constraint shape first.
"""
import sys
from pathlib import Path

from app.core.config.shared import BASE_DIR
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import text
from app.textbook.db_utils import engine


def _has_composite_constraint(conn) -> bool:
    rows = conn.execute(text("PRAGMA index_list(units)")).fetchall()
    for row in rows:
        index_name = row[1]
        if not row[2]:  # not unique
            continue
        cols = [r[2] for r in conn.execute(text(f"PRAGMA index_info({index_name})")).fetchall()]
        if set(cols) == {"unit_number", "hsk_level"}:
            return True
    return False


def migrate():
    with engine.connect() as conn:
        if _has_composite_constraint(conn):
            print("Composite (unit_number, hsk_level) constraint already exists -- nothing to do.")
            return

        print("Rebuilding units table with UNIQUE(unit_number, hsk_level)...")

        conn.execute(text("PRAGMA foreign_keys=OFF"))

        conn.execute(text("""
            CREATE TABLE units_new (
                id INTEGER NOT NULL PRIMARY KEY,
                unit_number INTEGER NOT NULL,
                title TEXT,
                hsk_level INTEGER NOT NULL DEFAULT 1,
                CONSTRAINT _unit_number_hsk_level_uc UNIQUE (unit_number, hsk_level)
            )
        """))

        conn.execute(text("""
            INSERT INTO units_new (id, unit_number, title, hsk_level)
            SELECT id, unit_number, title, hsk_level FROM units
        """))

        conn.execute(text("DROP TABLE units"))
        conn.execute(text("ALTER TABLE units_new RENAME TO units"))

        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.commit()

        print("Done.")


if __name__ == "__main__":
    migrate()