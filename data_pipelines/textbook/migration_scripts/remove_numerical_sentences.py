"""
One-off cleanup: removes "words" that are really numbers, dates or times
(二零一一年, 十一点, 三十一, 二十五, ...) that tag_sentences.py's Haiku fallback
used to register as vocabulary before split_numeral_tokens existed.

A word is a candidate if it contains a numeral run by the same rule
tag_sentences.py uses (extract_numeral_runs: 2+ numeral characters in a row,
or 第 + numerals), unless it's in KEEP below. For each one this deletes:
  - its SentenceVocab tags (so those sentences become untagged)
  - word-level Questions built from it
  - its SenseCache entries
  - its PinyinWord drill row, if any
  - its VocabSense rows and the Vocab row itself

Sentences that lost a tag are left with NO tags at all, so a normal
`tag_sentences.py` run re-tags them (it picks up untagged sentences
automatically) -- this time with the number split out as NUM. The script
prints which HSK levels need that rerun.

Dry run by default -- prints what it WOULD delete and changes nothing. Pass
--apply to actually delete.

USAGE
-----
    python remove_numeral_vocab.py            # preview
    python remove_numeral_vocab.py --apply    # delete
"""
import argparse
from collections import defaultdict

from app.textbook.db_utils import get_session, init_db
from app.textbook.models import (
    Vocab, VocabSense, SentenceVocab, Sentence, Question, SenseCache, PinyinWord, Unit,
)
from data_pipelines.textbook.scripts.tag_sentences import extract_numeral_runs

# Real words made of numeral characters -- never removed. Extend as needed.
KEEP = {
    "十一月",  # November
    "十二月",  # December
    "第一",    # first
    "千万",    # be sure to
    "万一",    # in case
}


def is_numeral_word(hanzi: str) -> bool:
    return hanzi not in KEEP and any(is_num for _, is_num in extract_numeral_runs(hanzi))


def main():
    parser = argparse.ArgumentParser(description="Remove number/date tokens registered as vocab.")
    parser.add_argument("--apply", action="store_true",
                         help="Actually delete (default is a dry-run preview).")
    args = parser.parse_args()

    init_db()
    with get_session() as db:
        targets = [v for v in db.query(Vocab).order_by(Vocab.id).all() if is_numeral_word(v.hanzi)]
        if not targets:
            print("No number/date vocab found.")
            return

        mode = "Removing" if args.apply else "Would remove (dry run)"
        print(f"{mode} {len(targets)} word(s):\n")

        affected_sentence_ids = set()
        for v in targets:
            sense_ids = [s.id for s in db.query(VocabSense).filter(VocabSense.vocab_id == v.id)]
            links = db.query(SentenceVocab).filter(SentenceVocab.vocab_id == v.id).all()
            affected_sentence_ids.update(l.sentence_id for l in links)
            q_filter = Question.vocab_id == v.id
            if sense_ids:
                q_filter = q_filter | Question.vocab_sense_id.in_(sense_ids)
            n_questions = db.query(Question).filter(q_filter).count()
            english = "; ".join(s.english for s in db.query(VocabSense).filter(VocabSense.vocab_id == v.id))
            print(f"  {v.id:>5} {v.hanzi}  '{english}'  "
                  f"({len(links)} sentence tag(s), {n_questions} question(s))")

            if args.apply:
                db.query(Question).filter(q_filter).delete(synchronize_session=False)
                if sense_ids:
                    db.query(SenseCache).filter(SenseCache.vocab_sense_id.in_(sense_ids)) \
                        .delete(synchronize_session=False)
                db.query(SenseCache).filter(SenseCache.hanzi == v.hanzi).delete(synchronize_session=False)
                db.query(PinyinWord).filter(PinyinWord.vocab_id == v.id).delete(synchronize_session=False)

        # Sentences that used any of these words: clear ALL their tags so the
        # next tag_sentences.py run re-tags them from scratch.
        levels = defaultdict(set)
        for sid in affected_sentence_ids:
            s = db.get(Sentence, sid)
            levels[s.unit.hsk_level].add(s.unit.unit_number)

        if args.apply:
            if affected_sentence_ids:
                db.query(SentenceVocab).filter(SentenceVocab.sentence_id.in_(affected_sentence_ids)) \
                    .delete(synchronize_session=False)
            target_ids = [v.id for v in targets]
            db.query(VocabSense).filter(VocabSense.vocab_id.in_(target_ids)).delete(synchronize_session=False)
            db.query(Vocab).filter(Vocab.id.in_(target_ids)).delete(synchronize_session=False)

        print(f"\n{len(affected_sentence_ids)} sentence(s) "
              f"{'were' if args.apply else 'would be'} untagged for re-tagging:")
        for level in sorted(levels):
            units = ", ".join(str(u) for u in sorted(levels[level]))
            print(f"  HSK{level}: unit(s) {units}")

        if not args.apply:
            db.rollback()
            print("\nDry run only -- nothing changed. Rerun with --apply to delete.")
        else:
            print("\nDone. Re-tag those sentences with:")
            for level in sorted(levels):
                print(f"  HSK_LEVEL={level} python tag_sentences.py")


if __name__ == "__main__":
    main()