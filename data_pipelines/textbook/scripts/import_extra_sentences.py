"""
data_pipelines/textbook/scripts/import_extra_sentences.py

Pipeline stage 2b (right after import_hsa): adds the sentences the grammar
trial chose for grammar points the dataset covers too thinly, from
data/grammar/selected_sentences.csv (written by
test_scripts/run_all_grammar.py --export). Bare, like import_hsa: no unit,
no tags -- tag_sentences tags them next.

TWO SOURCES, always told apart:
  tatoeba       real sentences from Tatoeba (CC BY 2.0 FR). source_author and
                source_url are kept for the credit line.
  ai_generated  sentences WRITTEN BY AI where Tatoeba had too few.
                generated_by says which model wrote it and when. Learners
                should be shown that a sentence is AI-written.
Both passed the same checks in the trial: every word at the level, no
harder grammar point, the point's rule finds it, two AI checks (it shows the
point; the English is right) and two AI difficulty reviews.

WHAT IS WRITTEN per sentence
  Sentence(source, external_id = "tatoeba-<id>" / "ai-<hash>", english,
  generated_by / source_author / source_url, word_split, review).
  word_split: the word split the trial checked it with, so tagging sees it
  exactly the same way (tag_sentences uses it like the dataset's tokens).
  review: the trial's two passed difficulty reviews, stored as a "fine at
  level N" verdict, so review_sentences doesn't pay to ask again.
A sentence listed under several grammar points is written once. One whose
text is already in the DB (an hsk-sentences-audio sentence) is skipped.
A previously imported sentence that is no longer in the file is deleted,
with its tags, grammar links and questions (questions are rebuilt anyway).
SAFETY: if the file is much SMALLER than what is imported (under three
quarters of it -- an export that failed half-way, an empty file) and would
remove more than a quarter of the imported sentences, nothing is deleted and
the stage stops -- pass --allow-removals if that is intended.

USAGE (from the repo root)
    python -m data_pipelines.textbook.scripts.import_extra_sentences
    python -m data_pipelines.textbook.scripts.import_extra_sentences --dry-run
"""
import argparse
import csv
import json
import time
from collections import Counter

from app.textbook.db_utils import get_session, init_db, upsert_import_sentence, delete_questions
from app.textbook.models import Question, Sentence, SentenceGrammar, SentenceSource

from data_pipelines.textbook.scripts import grammar_dsl
from data_pipelines.textbook.scripts.detect_grammar import REVIEW_PROMPT_VERSION

SOURCES = {"tatoeba": SentenceSource.tatoeba, "ai_generated": SentenceSource.ai_generated}


def read_rows() -> list[dict]:
    path = grammar_dsl.SENTENCES_FILE
    if not path.exists():
        raise SystemExit(f"{path} not found -- run test_scripts/run_all_grammar.py --export first.")
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    bad = [r for r in rows if r.get("source") not in SOURCES or not r.get("external_id") or not r.get("hanzi")]
    if bad:
        raise SystemExit(f"{len(bad)} row(s) in {path.name} have no hanzi / external_id or an unknown source "
                         f"(expected {sorted(SOURCES)}), e.g. {bad[0]}")
    return rows


def main():
    parser = argparse.ArgumentParser(description="Add the grammar trial's Tatoeba and AI-written sentences.")
    parser.add_argument("--dry-run", action="store_true", help="Count what would change; write nothing.")
    parser.add_argument("--allow-removals", action="store_true",
                        help="Delete imported sentences missing from the file even when that is many of them.")
    args = parser.parse_args()

    t0 = time.time()
    init_db()
    rows = read_rows()
    by_ext, points_of = {}, {}
    for r in rows:
        by_ext.setdefault(r["external_id"], r)
        points_of.setdefault(r["external_id"], set()).add(r["grammar_code"])
    stats = Counter()
    with get_session() as db:
        imported = {e for (e,) in db.query(Sentence.external_id)
                    .filter(Sentence.source.in_(list(SOURCES.values()))).all()}
        missing = imported - set(by_ext)
        shrank = len(by_ext) < 0.75 * len(imported)
        if imported and shrank and len(missing) > len(imported) / 4 and not args.allow_removals:
            raise SystemExit(
                f"STOPPED: {grammar_dsl.SENTENCES_FILE.name} has {len(by_ext)} sentence(s), and {len(missing)} of the "
                f"{len(imported)} sentence(s) imported before are not in it. That looks like an export that went "
                "wrong (check the output of run_all_grammar.py --export). Nothing was changed. If you really "
                "mean to remove them, run this stage again with --allow-removals.")
        for ext, r in by_ext.items():
            sentence, written = upsert_import_sentence(
                db, hanzi=r["hanzi"].strip(), english=r["english"].strip(), external_id=ext,
                source=SOURCES[r["source"]], topic=None)
            if not written:
                stats["skipped: same text already in the DB"] += 1
                continue
            sentence.generated_by = r.get("generated_by") or None
            sentence.source_author = r.get("source_author") or None
            sentence.source_url = r.get("source_url") or None
            sentence.word_split = r.get("word_split") or None
            try:
                review = json.loads(sentence.review) if sentence.review else {}
            except json.JSONDecodeError:
                review = {}
            if r.get("hsk_level"):
                review[str(int(r["hsk_level"]))] = {"ok": True, "problems": [], "v": REVIEW_PROMPT_VERSION,
                                                     "by": "grammar trial (two passed reviews)"}
            sentence.review = json.dumps(review, ensure_ascii=False)
            stats[f"written: {r['source']}"] += 1

        stale = (db.query(Sentence)
                 .filter(Sentence.source.in_(list(SOURCES.values())), ~Sentence.external_id.in_(list(by_ext)))
                 .all())
        if stale:
            ids = [s.id for s in stale]
            delete_questions(db, db.query(Question).filter(Question.sentence_id.in_(ids)))
            db.query(SentenceGrammar).filter(SentenceGrammar.sentence_id.in_(ids)).delete(synchronize_session=False)
            for s in stale:
                db.delete(s)                       # tags and fill-in-the-blank questions cascade
            stats["deleted: imported before, no longer in the file"] += len(stale)
        if args.dry_run:
            db.rollback()

    multi = sum(1 for v in points_of.values() if len(v) > 1)
    print(f"{len(rows)} row(s) in {grammar_dsl.SENTENCES_FILE.name}: {len(by_ext)} distinct sentence(s) "
          f"({multi} chosen for more than one grammar point).")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    print(f"Done in {time.time() - t0:.1f}s.{' (dry run: nothing written)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
