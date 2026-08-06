"""
Quick pipeline health check. Prints totals and flags problems only --
no per-unit tables to scroll through.

Usage:
    python check_pipeline_stats.py [--db path/to/textbook.db]
"""
import argparse
import os
import sqlite3
import sys

from app.core.config.data import TEXTBOOK_DB


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(TEXTBOOK_DB), help="Path to the SQLite database")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"Database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    print(f"Database: {args.db}\n")

    # --- totals ---
    print("TOTALS")
    for t in ["units", "vocab", "sentences", "sentence_vocab", "grammar_tips",
              "sentence_grammar", "fitb_questions", "questions"]:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n}")

    print("\nISSUES FOUND")
    found_any = False

    # units with vocab but zero questions
    empty_units = conn.execute("""
        SELECT u.unit_number FROM units u
        WHERE (SELECT COUNT(*) FROM vocab v WHERE v.unit_id = u.id) > 0
          AND (SELECT COUNT(*) FROM questions q WHERE q.unit_id = u.id) = 0
    """).fetchall()
    if empty_units:
        found_any = True
        print(f"  - Units with vocab but 0 questions: {[r[0] for r in empty_units]}")

    # grammar tips missing entirely
    grammar_count = conn.execute("SELECT COUNT(*) FROM grammar_tips").fetchone()[0]
    if grammar_count == 0:
        found_any = True
        print("  - No grammar_tips at all -- extract_and_match_grammar.py hasn't "
              "completed successfully yet")

    # vocab with placeholder/missing definitions
    bad_vocab = conn.execute("""
        SELECT COUNT(*) FROM vocab
        WHERE pinyin LIKE '%UNKNOWN%' OR english LIKE '%UNKNOWN%'
           OR pinyin = '' OR pinyin IS NULL OR english = '' OR english IS NULL
    """).fetchone()[0]
    if bad_vocab:
        found_any = True
        print(f"  - {bad_vocab} vocab row(s) with missing/placeholder pinyin or english "
              f"(run sync_index_definitions.py / append_orphan_tags.py)")

    # questions unreachable via any tag lookup
    orphan_questions = conn.execute("""
        SELECT COUNT(*) FROM questions WHERE vocab_id IS NULL AND sentence_id IS NULL
    """).fetchone()[0]
    if orphan_questions:
        found_any = True
        print(f"  - {orphan_questions} question(s) with neither vocab_id nor sentence_id "
              f"(unreachable by spaced repetition)")

    # sentences with no tags
    untagged = conn.execute("""
        SELECT COUNT(*) FROM sentences s
        WHERE NOT EXISTS (SELECT 1 FROM sentence_vocab sv WHERE sv.sentence_id = s.id)
    """).fetchone()[0]
    if untagged:
        found_any = True
        print(f"  - {untagged} sentence(s) with zero tags")

    # duplicates (should never happen -- upserts are supposed to prevent this)
    dup_vocab = conn.execute("""
        SELECT COUNT(*) FROM (SELECT hanzi FROM vocab GROUP BY hanzi HAVING COUNT(*) > 1)
    """).fetchone()[0]
    dup_questions = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT unit_id, question_type, question, answer
            FROM questions GROUP BY unit_id, question_type, question, answer HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    if dup_vocab or dup_questions:
        found_any = True
        print(f"  - Duplicates found: {dup_vocab} vocab, {dup_questions} questions "
              f"(idempotency broke somewhere)")

    if not found_any:
        print("  None -- looks healthy.")

    conn.close()


if __name__ == "__main__":
    main()