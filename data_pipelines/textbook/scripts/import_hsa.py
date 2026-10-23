"""
data_pipelines/textbook/scripts/import_hsa.py

Pipeline stage 2 (replaces sentence_parser.py AND fitb_parser.py). Writes
every hsk-sentences-audio sentence to the DB as a BARE Sentence row: no
unit, no tags, no level yet. No OCR, no AI calls.

  hanzi        = chinese
  english      = translation.en
  external_id  = the dataset id ("hsk1-0002"); attribution + audio key
  topic        = the dataset topic ("food"), used by assign_units.py
  audio_normal / audio_slow = the dataset's relative audio paths; turn them
                 into URLs with hsa_source.audio_url (or your own CDN)
  source       = hsa
  pinyin       = "" -- written later, in tone-sandhi form, once the
                 sentence is tagged (one format for the whole column)
  hsk_level    = NULL -- set by tag_sentences.py from the sentence's words;
                 the dataset's own level label is ignored
  unit_id      = NULL -- set by assign_units.py

The per-word tokens (word split, pinyin, gloss) are NOT copied: tagging
reads them from the dataset by external_id (see hsa_source.py).

SKIPPED, with a reason in the summary:
  - duplicate text: 5 sentences appear twice under different ids; the first
    id is kept (Sentence.hanzi is unique)
  - placeholder translation: 80 HSK 4+ sentences have English like
    "placeholder-p1". Pass --include-placeholders to import them with an
    empty translation instead.

Idempotent: keyed on external_id, so a re-run updates rows in place and
keeps any tags, levels and units already set.

FITB questions are no longer parsed from a book: create_questions.py will
generate them from tagged sentences (Phase 8).

USAGE
    python -m data_pipelines.textbook.scripts.import_hsa
    python -m data_pipelines.textbook.scripts.import_hsa --limit 50
    python -m data_pipelines.textbook.scripts.import_hsa --include-placeholders
"""
import argparse
import time
from collections import Counter

from app.textbook.db_utils import get_session, init_db, upsert_import_sentence
from app.textbook.models import Sentence, SentenceSource

from data_pipelines.textbook.scripts import hsa_source


def import_sentences(db, limit: int | None = None, include_placeholders: bool = False) -> dict:
    rows = list(hsa_source.sentences_by_id().values())
    if limit:
        rows = rows[:limit]

    counts, skipped = Counter(), []
    for s in rows:
        english = hsa_source.english(s)
        if hsa_source.has_placeholder_translation(s):
            if not include_placeholders:
                counts["skipped: placeholder translation"] += 1
                skipped.append((s["id"], s["chinese"], english))
                continue
            english = ""
            counts["imported with empty translation"] += 1

        audio = s.get("audio") or {}
        _, written = upsert_import_sentence(
            db, hanzi=s["chinese"], english=english, external_id=s["id"],
            source=SentenceSource.hsa, topic=s.get("topic"),
            audio_normal=audio.get("normal"), audio_slow=audio.get("slow"),
        )
        if written:
            counts["written (new or updated)"] += 1
        else:
            counts["skipped: duplicate text"] += 1
            skipped.append((s["id"], s["chinese"], "same text as an earlier id"))

    in_dataset = set(hsa_source.sentences_by_id())
    stale = [eid for (eid,) in db.query(Sentence.external_id)
             .filter(Sentence.source == SentenceSource.hsa).all() if eid not in in_dataset]

    print(f"  read {len(rows)} dataset sentence(s)")
    for k, v in sorted(counts.items()):
        print(f"    {k}: {v}")
    total = db.query(Sentence).filter(Sentence.source == SentenceSource.hsa).count()
    print(f"  sentences from this dataset now in the DB: {total}")
    if stale:
        print(f"  [warning] {len(stale)} sentence(s) in the DB are no longer in the dataset "
              f"(kept): {stale[:10]}")
    if skipped:
        print("  skipped (first 10):")
        for eid, zh, why in skipped[:10]:
            print(f"    - {eid} {zh}  [{why}]")
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description="Import hsk-sentences-audio sentences as bare rows.")
    parser.add_argument("--limit", type=int, default=None, help="Only import the first N sentences (testing).")
    parser.add_argument("--include-placeholders", action="store_true",
                        help="Import sentences whose translation is a placeholder, with empty English.")
    args = parser.parse_args()

    t0 = time.time()
    init_db()
    print("Importing hsk-sentences-audio sentences...")
    with get_session() as db:
        import_sentences(db, limit=args.limit, include_placeholders=args.include_placeholders)
    print(f"Done in {time.time() - t0:.1f}s. Next: tag_sentences.py")


if __name__ == "__main__":
    main()