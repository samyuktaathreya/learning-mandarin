"""
data_pipelines/textbook/scripts/load_hsk_lists.py

Pipeline stage 1 (replaces vocab_index_parser.py). Loads the two fixed
reference lists into the textbook DB. No OCR, no AI calls.

  data/raw/hsk_vocab.json      -> one Vocab row per word with a 2021-standard
                                  ("new-N") level. NO senses: a sense is only
                                  created when tag_sentences.py finds a
                                  sentence using that meaning.
  data/raw/grammar_points.json -> one GrammarTip row per grammar point, keyed
                                  on its id ("1-16"). No unit: assign_units.py
                                  sets the unit where each point first appears.

Both loads are idempotent: re-running updates rows in place, keeps every
unit assignment, and never creates duplicates. A word or grammar point
dropped from a list is reported, not deleted (sentences may still use it).

Vocab fields:
  hanzi      = simplified
  hsk_level  = N from "new-N" (7 = levels 7-9)
  pinyin     = the default form's numeric reading, spaces removed (ai4hao4),
               matching tag_sentences.py's pypinyin readings. Display only:
               replaced by the primary sense's reading once one exists.
  english    = the default form's first meaning (display only, as above)
  frequency, pos (JSON text), word_type (vocab / grammar / proper_noun)
The default form skips surname readings, so 还 shows "still", not
"surname Huan" (see hsk_vocab_source.default_form_index).

USAGE
    python load_hsk_lists.py                  # both lists
    python load_hsk_lists.py --vocab-only
    python load_hsk_lists.py --grammar-only
"""
import argparse
import json
import time
from collections import Counter

from app.core.config.textbook import TEXTBOOK_RAW_DIR
from app.textbook.db_utils import (
    get_session, init_db, upsert_hsk_vocab, get_or_create_grammar_point,
)
from app.textbook.models import Vocab, GrammarTip, WordType

from data_pipelines.textbook.scripts import hsk_vocab_source as hvs

VOCAB_PATH = TEXTBOOK_RAW_DIR / "hsk_vocab.json"
GRAMMAR_PATH = TEXTBOOK_RAW_DIR / "grammar_points.json"


def load_vocab(db) -> dict:
    entries = hvs.load_entries(str(VOCAB_PATH))
    by_level, by_type, malformed = Counter(), Counter(), []

    for hanzi, entry in entries.items():
        fi = hvs.default_form_index(entry)
        form = entry["forms"][fi]
        reading = hvs.numeric(form)
        if not hvs.syllables(reading):
            malformed.append((hanzi, reading))
        word_type = WordType(hvs.word_type_of(entry))
        level = hvs.level_of(entry)
        upsert_hsk_vocab(
            db, hanzi=hanzi, hsk_level=level,
            pinyin=reading.replace(" ", ""),
            english=form["meanings"][0],
            frequency=entry.get("frequency"),
            pos=entry.get("pos") or [],
            word_type=word_type,
        )
        by_level[level] += 1
        by_type[word_type.value] += 1

    listed = set(entries)
    dropped = [h for (h,) in db.query(Vocab.hanzi).filter(Vocab.hsk_level.isnot(None)).all()
               if h not in listed]

    print(f"  vocab: {len(entries)} words loaded")
    print("    by level: " + ", ".join(f"HSK {k}{'-9' if k == 7 else ''}: {v}"
                                       for k, v in sorted(by_level.items())))
    print("    by type:  " + ", ".join(f"{k}: {v}" for k, v in sorted(by_type.items())))
    if malformed:
        print(f"    [note] {len(malformed)} word(s) have a malformed numeric reading in the source "
              f"(accents or missing tone numbers); tagging falls back to all forms for these. "
              f"e.g. {malformed[:5]}")
    if dropped:
        print(f"    [warning] {len(dropped)} word(s) in the DB have a level but are no longer in the "
              f"list (kept): {dropped[:10]}")
    return {"words": len(entries), "malformed": len(malformed), "dropped": len(dropped)}


def load_grammar(db) -> dict:
    with open(GRAMMAR_PATH, encoding="utf-8") as f:
        points = json.load(f)

    by_level, seen, problems = Counter(), set(), []
    for p in points:
        code = p.get("id")
        if not code or p.get("level") is None:
            problems.append(p.get("id") or p.get("label"))
            continue
        if code in seen:
            problems.append(f"duplicate id {code}")
            continue
        seen.add(code)
        get_or_create_grammar_point(
            db, grammar_code=code, hsk_level=int(p["level"]),
            raw_text=p.get("label_full") or p.get("label") or code,
            content_dict=p,
        )
        by_level[int(p["level"])] += 1

    dropped = [c for (c,) in db.query(GrammarTip.grammar_code).all() if c not in seen]
    print(f"  grammar: {len(seen)} points loaded")
    print("    by level: " + ", ".join(f"HSK {k}: {v}" for k, v in sorted(by_level.items())))
    if problems:
        print(f"    [warning] skipped {len(problems)} entr(y/ies) with no id/level or a duplicate id: {problems}")
    if dropped:
        print(f"    [warning] {len(dropped)} grammar point(s) in the DB are no longer in the list "
              f"(kept): {dropped}")
    return {"points": len(seen), "skipped": len(problems), "dropped": len(dropped)}


def main():
    parser = argparse.ArgumentParser(description="Load the HSK vocabulary and grammar lists into the DB.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--vocab-only", action="store_true")
    group.add_argument("--grammar-only", action="store_true")
    args = parser.parse_args()

    t0 = time.time()
    init_db()
    print("Loading HSK reference lists (2021 standard)...")
    with get_session() as db:
        if not args.grammar_only:
            load_vocab(db)
        if not args.vocab_only:
            load_grammar(db)
    print(f"Done in {time.time() - t0:.1f}s.")


if __name__ == "__main__":
    main()