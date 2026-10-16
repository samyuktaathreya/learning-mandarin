"""
One-off cleanup: merges duplicate VocabSense rows -- the same word with the
same pinyin and (normalized) english recorded at more than one unit, e.g.

    73 | unit 5 | ge4 | a general measure word
    229| unit 8 | ge4 | a general measure word

For each group of identical senses, the EARLIEST-homed row is kept and every
other row is merged into it (db_utils.merge_sense_into): sentence tags,
questions and sense-cache entries pointing at a duplicate are repointed to
the kept row, the primary flag carries over, and the duplicate is deleted.

New duplicates can't be created any more (upsert_vocab_sense now reuses an
identical sense instead of making a new one), so this only needs to run once
against an existing database.

Dry run by default -- prints what it WOULD merge and changes nothing. Pass
--apply to actually write.

USAGE
-----
    python dedupe_senses.py            # preview
    python dedupe_senses.py --apply    # merge
"""
import argparse
from collections import defaultdict

from app.textbook.db_utils import (
    get_session, init_db, merge_sense_into, normalize_gloss,
)
from app.textbook.models import VocabSense, SentenceVocab, Question, SenseCache


def _norm_pinyin(p: str) -> str:
    return "".join((p or "").split()).lower()


def find_duplicate_groups(db) -> list[list[VocabSense]]:
    """Groups of 2+ senses sharing (vocab, pinyin, normalized english),
    each sorted earliest-home first. Senses with no home unit or a blank
    english gloss are placeholders and never grouped."""
    groups = defaultdict(list)
    for s in db.query(VocabSense).filter(VocabSense.unit_id.isnot(None)).all():
        gloss = normalize_gloss(s.english)
        if not gloss:
            continue
        groups[(s.vocab_id, _norm_pinyin(s.pinyin), gloss)].append(s)

    dupes = []
    for senses in groups.values():
        if len(senses) > 1:
            senses.sort(key=lambda s: (s.unit.hsk_level, s.unit.unit_number, s.id))
            dupes.append(senses)
    dupes.sort(key=lambda g: g[0].vocab.hanzi)
    return dupes


def reference_counts(db, sense_id: int) -> str:
    tags = db.query(SentenceVocab).filter(SentenceVocab.vocab_sense_id == sense_id).count()
    qs = db.query(Question).filter(Question.vocab_sense_id == sense_id).count()
    cache = db.query(SenseCache).filter(SenseCache.vocab_sense_id == sense_id).count()
    return f"{tags} tag(s), {qs} question(s), {cache} cache row(s)"


def describe(s: VocabSense) -> str:
    primary = " [primary]" if s.is_primary else ""
    return (f"sense {s.id}: HSK{s.unit.hsk_level} unit {s.unit.unit_number}, "
            f"{s.pinyin} '{s.english}'{primary}")


def main():
    parser = argparse.ArgumentParser(description="Merge duplicate vocab senses.")
    parser.add_argument("--apply", action="store_true",
                         help="Actually merge (default is a dry-run preview).")
    args = parser.parse_args()

    init_db()
    with get_session() as db:
        groups = find_duplicate_groups(db)
        if not groups:
            print("No duplicate senses found.")
            return

        total_dupes = sum(len(g) - 1 for g in groups)
        mode = "Merging" if args.apply else "Would merge (dry run)"
        print(f"{mode}: {total_dupes} duplicate sense(s) across {len(groups)} word meaning(s)\n")

        for group in groups:
            keep, drops = group[0], group[1:]
            print(f"{keep.vocab.hanzi}")
            print(f"  keep  {describe(keep)}")
            for d in drops:
                print(f"  merge {describe(d)}  ({reference_counts(db, d.id)})")
                if args.apply:
                    merge_sense_into(db, keep=keep, drop=d)

        if not args.apply:
            db.rollback()
            print("\nDry run only -- nothing changed. Rerun with --apply to merge.")
        else:
            print(f"\nMerged {total_dupes} duplicate sense(s).")


if __name__ == "__main__":
    main()