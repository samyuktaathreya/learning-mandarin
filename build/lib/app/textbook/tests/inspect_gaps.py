"""
Two focused queries to see exactly what's incomplete, instead of just counts.

Usage:
    python inspect_gaps.py              # both
    python inspect_gaps.py --vocab      # only the incomplete vocab list
    python inspect_gaps.py --questions  # only the orphan questions list
"""
import argparse
import sqlite3
import sys

from app.core.config.data import TEXTBOOK_DB
from app.core.logger import logger

def show_incomplete_vocab(conn):
    logger.debug("=" * 70)
    logger.debug("VOCAB WORDS WITH MISSING/BLANK/PLACEHOLDER PINYIN OR ENGLISH")
    logger.debug("=" * 70)
    rows = conn.execute("""
        SELECT v.hanzi, v.pinyin, v.english, v.word_type, u.unit_number
        FROM vocab v
        LEFT JOIN units u ON v.unit_id = u.id
        WHERE v.pinyin LIKE '%UNKNOWN%' OR v.english LIKE '%UNKNOWN%'
           OR v.pinyin IS NULL OR TRIM(v.pinyin) = ''
           OR v.english IS NULL OR TRIM(v.english) = ''
        ORDER BY u.unit_number, v.hanzi
    """).fetchall()

    if not rows:
        logger.debug("  None. All vocab has pinyin + english filled in.\n")
        return

    logger.debug(f"  {len(rows)} word(s):\n")
    for hanzi, pinyin, english, word_type, unit in rows:
        pinyin_display = pinyin if pinyin and pinyin.strip() else "(blank)"
        english_display = english if english and english.strip() else "(blank)"
        unit_display = unit if unit is not None else "(no unit)"
        logger.debug(f"  {hanzi:6s} unit={str(unit_display):8s} type={word_type:10s} "
              f"pinyin={pinyin_display:16s} english={english_display}")
    logger.debug()


def show_orphan_questions(conn):
    logger.debug("=" * 70)
    logger.debug("ORPHAN QUESTIONS (no vocab_id AND no sentence_id -- unreachable)")
    logger.debug("=" * 70)
    rows = conn.execute("""
        SELECT q.id, u.unit_number, q.question_type, q.question, q.answer
        FROM questions q
        JOIN units u ON q.unit_id = u.id
        WHERE q.vocab_id IS NULL AND q.sentence_id IS NULL
        ORDER BY u.unit_number, q.question_type
    """).fetchall()

    if not rows:
        logger.debug("  None. Every question links to a vocab word or a sentence.\n")
        return

    logger.debug(f"  {len(rows)} question(s):\n")
    for qid, unit, qtype, question, answer in rows:
        logger.debug(f"  id={qid:6} unit={unit:3} type={qtype:35s} q={question!r:30s} a={answer!r}")
    logger.debug()

    by_type = {}
    for _, unit, qtype, _, _ in rows:
        by_type[qtype] = by_type.get(qtype, 0) + 1
    logger.debug("  Breakdown by question_type:")
    for qtype, count in sorted(by_type.items(), key=lambda x: -x[1]):
        logger.debug(f"    {qtype}: {count}")
    logger.debug()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vocab", action="store_true", help="Show only the incomplete vocab list")
    parser.add_argument("--questions", action="store_true", help="Show only the orphan questions list")
    args = parser.parse_args()

    db_path = str(TEXTBOOK_DB)
    conn = sqlite3.connect(db_path)
    logger.debug(f"Database: {db_path}\n")

    show_both = not args.vocab and not args.questions
    if args.vocab or show_both:
        show_incomplete_vocab(conn)
    if args.questions or show_both:
        show_orphan_questions(conn)

    conn.close()


if __name__ == "__main__":
    main()