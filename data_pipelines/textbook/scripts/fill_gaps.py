"""
data_pipelines/textbook/scripts/fill_gaps.py

Pipeline stage 4b (after detect_grammar, before resolve_senses): makes sure
every HSK-list word of a level appears in at least one sentence OF THAT
LEVEL, so assign_units.py can teach it.

For each list word of the level that no sentence at that level uses:
  1. look in the fill-in sentence CSVs, data/raw/mandarin_hq_hsk_sentences/
     <level>.csv (1.csv, 2.csv, ...; every .csv in the folder is read). The
     file for the word's own level comes first; within that, rows whose
     Word is this word, then rows whose sentence contains it (shortest
     first); then the same in the other files;
  2. import the candidate (source = external, external_id "hq<file>-<No>",
     e.g. "hq1-12",
     no topic, so assign_units.py places it like the catch-all sentences),
     tag it exactly like tag_sentences.py does (HanLP split -- the CSV has no
     word split), and run the grammar check on it, HSK 1 restriction
     included;
  3. keep it only if it ends up at THIS level and really contains the word
     as a tagged word (not just the characters inside another word).
     Otherwise it is rolled back and the next candidate is tried.
A word no candidate works for is FLAGGED for you to handle by hand. A kept
sentence can cover other missing words too.

Every word's outcome (covered already / filled / flagged, and what was
tried) is written to debug/fill_gaps/<timestamp>.csv. Flagged words don't
stop the pipeline: they simply get no unit until a sentence exists.

Idempotent: a re-run only looks at words still uncovered, and a CSV row
already imported keeps its external_id.

USAGE (from the repo root)
    python -m data_pipelines.textbook.scripts.fill_gaps --levels 1 2
    python -m data_pipelines.textbook.scripts.fill_gaps --levels 1 --dry-run   # list gaps + candidates, no changes
"""
import argparse
import csv
import os
import time
from datetime import datetime

from app.core.config.textbook import TEXTBOOK_RAW_DIR
from app.textbook.db_utils import (
    get_session, init_db, upsert_import_sentence, get_vocab_hanzi_through_level,
)
from app.textbook.models import (
    GrammarTip, Sentence, SentenceSource, SentenceVocab, Vocab, VocabOrigin,
)

from data_pipelines.textbook.scripts import hsk_vocab_source as hvs
from data_pipelines.textbook.scripts import tag_sentences as ts
from data_pipelines.textbook.scripts import detect_grammar as dg

# Fill-in sentence files: every .csv in this folder, named by HSK level
# (1.csv, 2.csv), all with the columns
# No,Word,Pinyin,Chinese_Sentence,English_Translation.
CSV_DIR = TEXTBOOK_RAW_DIR / "mandarin_hq_hsk_sentences"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "debug", "fill_gaps"))


def load_csv_rows() -> list[dict]:
    """Every row of every .csv in CSV_DIR. row["level"] is the file's level
    when its name is a number (2.csv -> 2), else None. The Pinyin column
    isn't used: readings come from tagging, as for every sentence without a
    dataset word split."""
    files = sorted(CSV_DIR.glob("*.csv"), key=lambda p: (not p.stem.isdigit(), int(p.stem) if p.stem.isdigit() else 0, p.stem))
    if not files:
        raise SystemExit(f"No fill-in sentence files (*.csv) in {CSV_DIR}")
    rows = []
    for path in files:
        level = int(path.stem) if path.stem.isdigit() else None
        with open(path, encoding="utf-8-sig", newline="") as f:
            for k, r in enumerate(csv.DictReader(f), 1):
                hanzi = (r.get("Chinese_Sentence") or "").strip()
                if hanzi:
                    no = (r.get("No") or "").strip() or str(k)
                    rows.append({"id": f"hq{path.stem}-{no}", "level": level,
                                 "word": (r.get("Word") or "").strip(), "hanzi": hanzi,
                                 "english": (r.get("English_Translation") or "").strip()})
    counts = ", ".join(f"{p.name}: {sum(r['id'].startswith(f'hq{p.stem}-') for r in rows)}" for p in files)
    print(f"  fill-in sentences: {len(rows)} ({counts})")
    return rows


def covered_vocab_ids(db, level: int) -> set[int]:
    return {vid for (vid,) in db.query(SentenceVocab.vocab_id)
            .join(Sentence, Sentence.id == SentenceVocab.sentence_id)
            .filter(Sentence.hsk_level == level).distinct().all()}


def candidates_for(word: str, level: int, rows: list[dict], in_db: set[str]) -> list[dict]:
    """Rows from this level's file first, then the other files; within
    each, rows made for this word, then other rows containing it, shortest
    first. Rows whose text is already in the DB are skipped: that sentence
    has already been tagged and judged."""
    matching = [r for r in rows if word in r["hanzi"] and r["hanzi"] not in in_db]
    return sorted(matching, key=lambda r: (r["level"] != level, r["word"] != word, len(r["hanzi"])))


def try_row(db, run, row: dict, vocab: Vocab, level: int, grammar: dict):
    """Imports, tags and grammar-checks one CSV row inside a savepoint.
    Returns (sentence, "ok") if kept, else (None, why) with everything it
    wrote rolled back."""
    sp = db.begin_nested()
    try:
        s, written = upsert_import_sentence(
            db, hanzi=row["hanzi"], english=row["english"], external_id=row["id"],
            source=SentenceSource.external, topic=None)
        if not written:
            sp.rollback()
            return None, "same text as a sentence already in the DB"
        ts.tag_sentence(db, run, s)
        db.expire(s, ["vocab_links"])
        found, bump = dg.detect_sentence(db, s, grammar["detectors"], grammar["level_of_point"])
        uses_word = any(r.vocab_id == vocab.id for r in s.vocab_links)
        if s.hsk_level != level:
            why = f"ends up at level {s.hsk_level}"
            if bump:
                why += f" (grammar {' '.join(bump[2])})"
            sp.rollback()
            return None, why
        if not uses_word:
            sp.rollback()
            return None, "word isn't a tagged word in it (part of a longer word)"
        dg.write_links(db, s, found, grammar["id_of"])
        sp.commit()
        return s, "ok"
    except Exception as e:
        sp.rollback()
        return None, f"error: {e!r}"


def main():
    parser = argparse.ArgumentParser(description="Cover every list word with a sentence of its level.")
    parser.add_argument("--levels", nargs="+", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="List uncovered words and their CSV candidates; import nothing.")
    args = parser.parse_args()

    t0 = time.time()
    init_db()
    rows = load_csv_rows()
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    report = []      # (level, word, status, sentence, tried)

    with get_session() as db:
        points = db.query(GrammarTip).all()
        detectors, _ = dg.build_detectors(points)
        grammar = {"detectors": detectors,
                   "level_of_point": {p.grammar_code: p.hsk_level for p in points},
                   "id_of": {p.grammar_code: p.id for p in points}}
        run = None
        if not args.dry_run:
            logger, _ = ts.setup_logging()
            entries = hvs.load_entries(str(ts.VOCAB_PATH))
            run = ts.Run(logger, entries, get_vocab_hanzi_through_level(db, hvs.MAX_LEVEL),
                         ts.load_segmentation_cache(), estimate=False)

        for level in sorted(set(args.levels)):
            words = (db.query(Vocab).filter(Vocab.hsk_level == level, Vocab.origin == VocabOrigin.hsk_list)
                     .order_by(Vocab.id).all())
            covered = covered_vocab_ids(db, level)
            missing = [v for v in words if v.id not in covered]
            print(f"\nHSK {level}: {len(words)} list words, {len(words) - len(missing)} already in a "
                  f"sentence of this level, {len(missing)} not.")
            in_db = {h for (h,) in db.query(Sentence.hanzi).all()}
            filled, flagged = 0, []
            for v in missing:
                if v.id in covered:                 # covered by a sentence kept earlier in this run
                    report.append((level, v.hanzi, "filled (by another word's sentence)", "", ""))
                    continue
                cands = candidates_for(v.hanzi, level, rows, in_db)
                if args.dry_run:
                    report.append((level, v.hanzi, f"{len(cands)} CSV candidate(s)",
                                   cands[0]["hanzi"] if cands else "", ""))
                    if not cands:
                        flagged.append(v.hanzi)
                    continue
                tried = []
                for row in cands:
                    s, why = try_row(db, run, row, v, level, grammar)
                    if s is not None:
                        covered |= {r.vocab_id for r in s.vocab_links}
                        in_db.add(s.hanzi)
                        filled += 1
                        report.append((level, v.hanzi, "filled", s.hanzi, " | ".join(tried)))
                        break
                    tried.append(f"{row['hanzi']} ({why})")
                else:
                    flagged.append(v.hanzi)
                    report.append((level, v.hanzi, "FLAGGED", "",
                                   " | ".join(tried) or "no CSV sentence contains it"))
                db.commit()
            if args.dry_run:
                print(f"  {len(missing) - len(flagged)} have at least one CSV candidate; "
                      f"{len(flagged)} have none: {' '.join(flagged)}")
            else:
                print(f"  filled from the CSV: {filled}")
                print(f"  FLAGGED for manual handling ({len(flagged)}): {' '.join(flagged) or '-'}")

        if args.dry_run:
            db.rollback()
        elif run is not None:
            ts.save_segmentation_cache(run.seg_cache)

    with open(report_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["hsk_level", "word", "status", "sentence", "tried"])
        w.writerows(report)
    print(f"\nEvery word's outcome: {report_path}")
    print(f"Done in {time.time() - t0:.0f}s.{' (dry run: nothing written)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()