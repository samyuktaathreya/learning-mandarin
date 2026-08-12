"""
data_pipelines/textbook/scripts/repair_diacritic_questions.py

One-off repair: converts any diacritic-format pinyin stored as answers in
the questions table to numeric format. Complements repair_diacritic_pinyin.py
which fixes Vocab.pinyin diacritics; this script handles questions that may
have gotten diacritic answers through various ingestion paths (external
imports, etc.) before all the pinyin conversion paths were properly audited.

Usage:
    python data_pipelines/textbook/scripts/repair_diacritic_questions.py --dry-run
    python data_pipelines/textbook/scripts/repair_diacritic_questions.py
"""
import argparse
import re

from app.textbook.db_utils import get_session, init_db
from app.textbook.models import Question
from data_pipelines.textbook.scripts.vocab_pinyin_utils import diacritic_to_numeric

_HAS_TONE_DIGIT = re.compile(r"[1-5]")


def main():
    parser = argparse.ArgumentParser(description="Repair diacritic pinyin in question answers.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing.")
    args = parser.parse_args()

    init_db()
    with get_session() as db:
        # Only look at "transcribe" questions where the answer SHOULD be pinyin
        transcribe_questions = db.query(Question).filter(
            Question.question_type.in_([
                "transcribe word to pinyin",
                "transcribe hanzi to pinyin",
            ])
        ).all()

        suspect = [
            q for q in transcribe_questions
            if q.answer and not _HAS_TONE_DIGIT.search(q.answer)
        ]

        if not suspect:
            print("✅ No diacritic pinyin answers found in transcribe questions -- nothing to repair.")
            return

        print(f"Found {len(suspect)} transcribe question(s) with likely diacritic pinyin answers:\n")

        fixed = 0
        for q in suspect:
            new_answer = diacritic_to_numeric(q.answer)
            if new_answer == q.answer:
                print(f"  [unchanged, needs manual review] Q{q.id}: '{q.answer}'")
                continue
            print(f"  Q{q.id}: '{q.answer}' -> '{new_answer}'")
            if not args.dry_run:
                q.answer = new_answer
            fixed += 1

        if args.dry_run:
            print(f"\nDry run: would fix {fixed} answer(s). Re-run without --dry-run to apply.")
        else:
            print(f"\n✅ Repaired {fixed} answer(s).")


if __name__ == "__main__":
    main()