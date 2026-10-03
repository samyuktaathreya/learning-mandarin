"""
Migration script: convert existing citation-form pinyin to spoken-form
(sandhi-applied) pinyin across vocab, vocab_senses, sentences, and questions tables.

This is a one-time data migration that reads the current pinyin, applies
apply_tone_sandhi() from vocab_pinyin_utils, and writes back. It's designed
to be idempotent (safe to re-run; already-converted rows are skipped) and
includes a dry-run mode.

Usage:
  python migrate_pinyin_to_sandhi.py [--dry-run]

Without --dry-run, updates are committed to the database. With --dry-run,
prints what WOULD be changed but doesn't write anything.
"""
import sys
import sqlite3
from pathlib import Path

# Hardcoded DB path import
from app.core.config.data import TEXTBOOK_DB

# Import the sandhi function. Adjust import path as needed if running from
# a different location.
try:
    from data_pipelines.textbook.scripts.vocab_pinyin_utils import apply_tone_sandhi
except ImportError:
    print("ERROR: vocab_pinyin_utils not found. Make sure it's in PYTHONPATH or the same directory.")
    sys.exit(1)


def migrate_vocab(db: sqlite3.Connection, dry_run: bool = False) -> tuple[int, int]:
    """Migrate vocab.pinyin. Returns (updated_count, skipped_count)."""
    cursor = db.cursor()
    
    # Note: vocab table has a direct UNIQUE constraint on hanzi, so 1 row per hanzi.
    # We use hanzi from the vocab table itself.
    cursor.execute("SELECT id, hanzi, pinyin FROM vocab WHERE pinyin IS NOT NULL AND pinyin != ''")
    rows = cursor.fetchall()
    
    updated = 0
    skipped = 0
    
    for row_id, hanzi, pinyin in rows:
        # Skip if already migrated (has digits indicating numeric tone)
        if not pinyin or "UNKNOWN_PINYIN" in pinyin:
            skipped += 1
            continue
        
        # Apply sandhi
        new_pinyin = apply_tone_sandhi(hanzi, pinyin)
        
        if new_pinyin != pinyin:
            print(f"  vocab[{row_id}] '{hanzi}': {pinyin} -> {new_pinyin}")
            if not dry_run:
                cursor.execute("UPDATE vocab SET pinyin = ? WHERE id = ?", (new_pinyin, row_id))
            updated += 1
        else:
            skipped += 1
    
    if not dry_run:
        db.commit()
    return updated, skipped


def migrate_vocab_senses(db: sqlite3.Connection, dry_run: bool = False) -> tuple[int, int]:
    """Migrate vocab_senses.pinyin. Returns (updated_count, skipped_count)."""
    cursor = db.cursor()
    
    # vocab_senses has a foreign key to vocab (hanzi), so we join to get hanzi.
    cursor.execute(
        """
        SELECT vs.id, v.hanzi, vs.pinyin 
        FROM vocab_senses vs
        JOIN vocab v ON vs.vocab_id = v.id
        WHERE vs.pinyin IS NOT NULL AND vs.pinyin != ''
        """
    )
    rows = cursor.fetchall()
    
    updated = 0
    skipped = 0
    
    for row_id, hanzi, pinyin in rows:
        if not pinyin or "UNKNOWN_PINYIN" in pinyin:
            skipped += 1
            continue
        
        new_pinyin = apply_tone_sandhi(hanzi, pinyin)
        
        if new_pinyin != pinyin:
            print(f"  vocab_senses[{row_id}] '{hanzi}': {pinyin} -> {new_pinyin}")
            if not dry_run:
                cursor.execute("UPDATE vocab_senses SET pinyin = ? WHERE id = ?", (new_pinyin, row_id))
            updated += 1
        else:
            skipped += 1
    
    if not dry_run:
        db.commit()
    return updated, skipped


def migrate_sentences(db: sqlite3.Connection, dry_run: bool = False) -> tuple[int, int]:
    """Migrate sentences.pinyin. Returns (updated_count, skipped_count)."""
    cursor = db.cursor()
    
    cursor.execute("SELECT id, hanzi, pinyin FROM sentences WHERE pinyin IS NOT NULL AND pinyin != ''")
    rows = cursor.fetchall()
    
    updated = 0
    skipped = 0
    
    for row_id, hanzi, pinyin in rows:
        if not pinyin or "UNKNOWN_PINYIN" in pinyin:
            skipped += 1
            continue
        
        # For sentences, hanzi and pinyin are both full sentence-length strings.
        # apply_tone_sandhi will split and handle appropriately.
        new_pinyin = apply_tone_sandhi(hanzi, pinyin)
        
        if new_pinyin != pinyin:
            print(f"  sentences[{row_id}] '{hanzi}': {pinyin} -> {new_pinyin}")
            if not dry_run:
                cursor.execute("UPDATE sentences SET pinyin = ? WHERE id = ?", (new_pinyin, row_id))
            updated += 1
        else:
            skipped += 1
    
    if not dry_run:
        db.commit()
    return updated, skipped


def _is_numeric_pinyin(text: str) -> bool:
    """Quick check: does this text look like pinyin? Must have at least one tone digit."""
    return bool(text and any(c in text for c in "12345"))


def _extract_hanzi_only(hanzi_text: str) -> str:
    """Remove punctuation and non-hanzi characters. Keeps only CJK characters."""
    import re
    # CJK Unicode ranges: 4E00-9FFF for common hanzi
    return re.sub(r'[^\u4E00-\u9FFF]', '', hanzi_text)


def migrate_questions(db: sqlite3.Connection, dry_run: bool = False) -> tuple[int, int]:
    """Migrate questions.question and questions.answer that contain pinyin.
    
    Uses the foreign keys (vocab_id, vocab_sense_id, sentence_id) to look up
    the corresponding hanzi, then applies sandhi to any numeric-pinyin text
    in the question/answer columns.
    
    Returns (updated_count, skipped_count).
    """
    cursor = db.cursor()
    
    cursor.execute("""
        SELECT q.id, q.question, q.answer, q.vocab_id, q.vocab_sense_id, q.sentence_id
        FROM questions q
        WHERE q.question IS NOT NULL OR q.answer IS NOT NULL
    """)
    rows = cursor.fetchall()
    
    updated = 0
    skipped = 0
    
    for q_id, question, answer, vocab_id, vocab_sense_id, sentence_id in rows:
        hanzi = None
        
        # Determine the hanzi source based on foreign keys
        if vocab_id:
            cursor.execute("SELECT hanzi FROM vocab WHERE id = ?", (vocab_id,))
            result = cursor.fetchone()
            if result:
                hanzi = result[0]
        elif vocab_sense_id:
            cursor.execute("SELECT v.hanzi FROM vocab v JOIN vocab_senses vs ON vs.vocab_id = v.id WHERE vs.id = ?", (vocab_sense_id,))
            result = cursor.fetchone()
            if result:
                hanzi = result[0]
        elif sentence_id:
            cursor.execute("SELECT hanzi FROM sentences WHERE id = ?", (sentence_id,))
            result = cursor.fetchone()
            if result:
                # For sentences, strip punctuation to get just hanzi
                hanzi = _extract_hanzi_only(result[0])
        
        # If we couldn't find hanzi, skip this row
        if not hanzi:
            skipped += 1
            continue
        
        # Migrate question column if it has numeric pinyin
        new_question = question
        if question and _is_numeric_pinyin(question):
            new_question = apply_tone_sandhi(hanzi, question)
        
        # Migrate answer column if it has numeric pinyin
        new_answer = answer
        if answer and _is_numeric_pinyin(answer):
            new_answer = apply_tone_sandhi(hanzi, answer)
        
        # Only update if something changed
        if new_question != question or new_answer != answer:
            if new_question != question:
                print(f"  questions[{q_id}] question: {question} -> {new_question}")
            if new_answer != answer:
                print(f"  questions[{q_id}] answer: {answer} -> {new_answer}")
            if not dry_run:
                cursor.execute("UPDATE questions SET question = ?, answer = ? WHERE id = ?",
                               (new_question, new_answer, q_id))
            updated += 1
        else:
            skipped += 1
    
    if not dry_run:
        db.commit()
    return updated, skipped


def main():
    db_path = TEXTBOOK_DB
    dry_run = "--dry-run" in sys.argv
    
    if not Path(db_path).exists():
        print(f"ERROR: Database not found at {db_path}")
        sys.exit(1)
    
    print(f"Connecting to {db_path}...")
    db = sqlite3.connect(db_path)
    
    try:
        mode = "DRY RUN" if dry_run else "LIVE MODE"
        print(f"\n{mode}: Migrating pinyin to sandhi form...\n")
        
        print("1. Migrating vocab.pinyin...")
        vocab_updated, vocab_skipped = migrate_vocab(db, dry_run)
        print(f"   -> {vocab_updated} updated, {vocab_skipped} skipped")
        
        print("\n2. Migrating vocab_senses.pinyin...")
        senses_updated, senses_skipped = migrate_vocab_senses(db, dry_run)
        print(f"   -> {senses_updated} updated, {senses_skipped} skipped")
        
        print("\n3. Migrating sentences.pinyin...")
        sentences_updated, sentences_skipped = migrate_sentences(db, dry_run)
        print(f"   -> {sentences_updated} updated, {sentences_skipped} skipped")
        
        print("\n4. Migrating questions (question/answer columns with pinyin)...")
        questions_updated, questions_skipped = migrate_questions(db, dry_run)
        print(f"   -> {questions_updated} updated, {questions_skipped} skipped")
        
        total_updated = vocab_updated + senses_updated + sentences_updated + questions_updated
        total_skipped = vocab_skipped + senses_skipped + sentences_skipped + questions_skipped
        
        print(f"\n{'='*60}")
        print(f"TOTAL: {total_updated} rows updated, {total_skipped} rows skipped")
        
        if dry_run:
            print(f"\nThis was a DRY RUN. Re-run without --dry-run to commit changes.")
        else:
            print(f"\n✅ Migration complete! Changes committed to {db_path}")
    
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()