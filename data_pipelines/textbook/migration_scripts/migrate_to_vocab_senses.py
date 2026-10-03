"""
data_pipelines/textbook/scripts/migrate_to_vocab_senses.py

One-time migration for existing databases: adds the new `vocab_senses`
table and the new `vocab_sense_id` columns (on `sentence_vocab` and
`questions`), then back-fills them from your EXISTING single-definition
Vocab data -- so you don't have to re-run vocab_index_parser.py /
sentence_parser.py / create_questions.py from scratch just to pick up the
sense-aware schema.

WHAT IT DOES, IN ORDER:
  1. SCHEMA: creates `vocab_senses` (if missing) and adds `vocab_sense_id`
     to `sentence_vocab` / `questions` (if missing) via raw ALTER TABLE.
     SQLAlchemy's create_all() only creates whole tables that don't exist
     yet -- it will never add a column to a table that's already there,
     hence the manual ALTER TABLE step for the two existing tables that
     gained a column.
  2. DATA: for every existing Vocab row, creates exactly ONE VocabSense,
     copying that row's current pinyin/english/unit_id/word_type, and
     marks it primary. This is the only safe assumption a migration can
     make about old data: it never recorded more than one meaning per
     word to begin with, so there's nothing to disambiguate here -- this
     just gives that single existing meaning a proper sense row to live in.
  3. Points every existing SentenceVocab row, and every existing
     word-level Question row (vocab_id set, sentence_id not), at that
     word's new (single) sense.

IDEMPOTENT: safe to run more than once. A Vocab that already has a sense
is skipped in step 2; a link/question that already has vocab_sense_id set
is skipped in step 3. Re-running after you've re-run vocab_index_parser.py
for part of your curriculum (which may have already split some words into
multiple senses) won't touch those words again -- step 2 only acts on
words with ZERO senses, and step 3 falls back to the PRIMARY sense for
any word that already has more than one.

WHAT THIS MIGRATION DOES NOT DO: it does not discover any new senses for
words that should genuinely have more than one meaning (e.g. 还). It just
gets your existing data onto the new schema with its current single
meaning intact. To actually split out multi-sense words, re-run
vocab_index_parser.py against your printed index afterward -- it trusts
the index text directly and will create additional senses for any word
whose later listing has a genuinely different gloss, without needing to
redo OCR or sentence extraction for anything else.

USAGE:
    python migrate_to_vocab_senses.py             # migrate for real
    python migrate_to_vocab_senses.py --dry-run    # show counts, roll back, save nothing

Back up your .db file before running this for real -- it's a one-way
schema change (ALTER TABLE ADD COLUMN can't be trivially undone in SQLite).
"""

import argparse

from sqlalchemy import inspect, text

from app.core.config.data import TEXTBOOK_DB
from app.textbook.db_utils import engine, init_db, SessionLocal
from app.textbook.models import Base, Vocab, VocabSense, SentenceVocab, Question


# --------------------------------- SCHEMA ---------------------------------

def _table_exists(table_name: str) -> bool:
    return table_name in inspect(engine).get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return column_name in {c["name"] for c in inspect(engine).get_columns(table_name)}


def migrate_schema():
    """Brings the ON-DISK schema up to date with models.py. create_all()
    handles brand-new tables (vocab_senses, and anything else models.py has
    added that isn't on disk yet -- a no-op for tables already there).
    ALTER TABLE by hand handles the new column on two tables that already
    existed before this migration, since create_all() never alters an
    existing table's columns."""
    print("1. Migrating schema...")

    Base.metadata.create_all(bind=engine)
    print(f"   {'✓' if _table_exists('vocab_senses') else '✗ MISSING'} vocab_senses table")

    with engine.begin() as conn:
        for table, column in [("sentence_vocab", "vocab_sense_id"), ("questions", "vocab_sense_id")]:
            if not _table_exists(table):
                print(f"   - {table} doesn't exist yet (fresh DB) -- nothing to alter")
                continue
            if _column_exists(table, column):
                print(f"   - {table}.{column} already present")
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER"))
            print(f"   ✓ added {table}.{column}")


# --------------------------------- DATA ---------------------------------

def migrate_vocab_to_senses(db) -> dict:
    """Step 2: give every existing Vocab row exactly one VocabSense, copying
    its current pinyin/english/unit_id/word_type. Skips any Vocab that
    already has a sense."""
    all_vocab = db.query(Vocab).all()
    created, skipped = 0, 0
    for vocab in all_vocab:
        if vocab.senses:
            skipped += 1
            continue
        db.add(VocabSense(
            vocab_id=vocab.id,
            unit_id=vocab.unit_id,
            pinyin=vocab.pinyin or "",
            english=vocab.english or "",
            word_type=vocab.word_type,
            is_primary=1,
        ))
        created += 1
    db.flush()  # so the next steps' vocab.senses lookups see these rows
    return {"created": created, "skipped": skipped, "total": len(all_vocab)}


def _pick_sense(vocab: Vocab) -> VocabSense | None:
    """Which sense to point old data at: the primary one if there's a
    choice (there will be exactly one right after migrate_vocab_to_senses,
    but this stays correct even if some words already got split into
    multiple senses via a prior vocab_index_parser.py re-run)."""
    if not vocab.senses:
        return None
    return next((s for s in vocab.senses if s.is_primary), vocab.senses[0])


def migrate_sentence_vocab_links(db) -> dict:
    """Step 3a: point every existing SentenceVocab row at its word's sense."""
    links = db.query(SentenceVocab).filter(SentenceVocab.vocab_sense_id.is_(None)).all()
    updated, missing = 0, 0
    for link in links:
        sense = _pick_sense(link.vocab) if link.vocab else None
        if sense is None:
            missing += 1
            continue
        link.vocab_sense_id = sense.id
        updated += 1
    db.flush()
    return {"updated": updated, "missing": missing, "total": len(links)}


def migrate_question_links(db) -> dict:
    """Step 3b: same idea for word-level Question rows (vocab_id set)."""
    questions = (
        db.query(Question)
        .filter(Question.vocab_id.isnot(None), Question.vocab_sense_id.is_(None))
        .all()
    )
    updated, missing = 0, 0
    for q in questions:
        vocab = db.query(Vocab).filter(Vocab.id == q.vocab_id).first()
        sense = _pick_sense(vocab) if vocab else None
        if sense is None:
            missing += 1
            continue
        q.vocab_sense_id = sense.id
        updated += 1
    db.flush()
    return {"updated": updated, "missing": missing, "total": len(questions)}


# --------------------------------- MAIN ---------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                         help="Run the full migration but roll back the DATA changes at the end "
                              "instead of committing, so you can review the counts first. NOTE: "
                              "the schema step (new table/columns) still applies even on a dry "
                              "run -- ALTER TABLE isn't part of the rollback-able transaction in "
                              "SQLite, and an empty vocab_senses table with no data is harmless.")
    args = parser.parse_args()

    print(f"Migrating DB at: {TEXTBOOK_DB}")
    if not args.dry_run:
        print("⚠️  This modifies your database. Make sure you've backed up the .db file.\n")
    else:
        print("(dry run -- data changes will be rolled back at the end)\n")

    init_db()
    migrate_schema()

    db = SessionLocal()
    try:
        print("\n2. Creating one VocabSense per existing Vocab row...")
        v = migrate_vocab_to_senses(db)
        print(f"   created {v['created']} sense(s), skipped {v['skipped']} already-migrated "
              f"word(s) (of {v['total']} Vocab row(s) total)")

        print("\n3. Pointing existing sentence tags at their word's sense...")
        sv = migrate_sentence_vocab_links(db)
        msg = f"   updated {sv['updated']} link(s)"
        if sv["missing"]:
            msg += f", {sv['missing']} skipped (no vocab/sense found -- worth a manual look)"
        print(msg)

        print("\n4. Pointing existing word-questions at their word's sense...")
        q = migrate_question_links(db)
        msg = f"   updated {q['updated']} question(s)"
        if q["missing"]:
            msg += f", {q['missing']} skipped (no vocab/sense found -- worth a manual look)"
        print(msg)

        if args.dry_run:
            db.rollback()
            print("\n🔎 Dry run complete -- data changes rolled back, nothing was saved.")
            print("   (the vocab_senses table/columns from step 1 do persist -- they're empty "
                  "and harmless; run without --dry-run to actually populate them.)")
        else:
            db.commit()
            print("\n✅ Migration complete. Existing data now has one primary sense per word, "
                  "and every sentence tag / word-question points at it.")
            print("   To actually SPLIT any word that should have multiple senses, re-run "
                  "vocab_index_parser.py against your printed index -- it trusts the index text "
                  "directly and will create additional senses for any word whose later listing "
                  "has a genuinely different gloss, without redoing OCR or sentence extraction "
                  "for anything else.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()