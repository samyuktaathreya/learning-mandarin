"""
data_pipelines/textbook/scripts/load_hsk_lists.py

Pipeline stage 1. Loads the two fixed reference lists into the textbook DB.
No OCR, no AI calls.

  data/raw/disorganized_hsk_vocab_with_radicals.json
      -> one Vocab row per word in the curriculum list: a 2025-syllabus
         level ("newest-N") or a hand-written level in
         data/raw/hsk_level_overrides.json. NO senses: a sense is only
         created when tag_sentences.py finds a sentence using that meaning.
  data/raw/grammar_points.json
      -> one GrammarTip row per grammar point, keyed on its id ("1-16"). No
         unit: assign_units.py sets the unit where each point first appears.
         Levels are then moved (data/grammar/, exported by the grammar trial):
           grammar_level_overrides.json    the 2025 syllabus level, where the
                                            list's 2021 level differs
           grammar_level_by_examples.json  points whose own words are harder
                                            than their level (2-47: 以前/以后
                                            are HSK 3 words) -- applied last
  data/grammar/grammar_explanations.json
      -> GrammarExplanation rows: what each point is, copied from the
         official list and the AllSet Learning wiki (no AI-written text).
         A point's rows are replaced on every run.

Both loads are idempotent: re-running updates rows in place, keeps every
unit assignment, and never creates duplicates. A word or grammar point
dropped from a list is reported, not deleted (sentences may still use it).

Vocab fields:
  hanzi      = simplified
  hsk_level  = 2025 level, or the override
  pinyin     = the default form's numeric reading, spaces removed (ai4hao4).
               Display only: replaced by the primary sense's reading once
               one exists.
  english    = the default form's first meaning (display only, as above)
  frequency, pos (JSON text), word_type (vocab / grammar / proper_noun),
  radical, pattern (pieces of a listed pattern, e.g. 虽然……但是……)
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
from app.textbook.models import Vocab, GrammarTip, GrammarExplanation, WordType

from data_pipelines.textbook.scripts import grammar_dsl
from data_pipelines.textbook.scripts import hsk_vocab_source as hvs

VOCAB_PATH = TEXTBOOK_RAW_DIR / hvs.VOCAB_FILE
GRAMMAR_PATH = TEXTBOOK_RAW_DIR / "grammar_points.json"


def load_vocab(db) -> dict:
    if not VOCAB_PATH.exists():
        raise SystemExit(f"Vocab list not found: {VOCAB_PATH}")
    entries = hvs.load_entries(str(VOCAB_PATH))
    notes = hvs.LOAD_NOTES
    by_level, by_type, malformed, no_radical = Counter(), Counter(), [], []

    for hanzi, entry in entries.items():
        form = entry["forms"][hvs.default_form_index(entry)]
        reading = hvs.numeric(form)
        if not hvs.syllables(reading):
            malformed.append((hanzi, reading))
        word_type = WordType(hvs.word_type_of(entry))
        level = hvs.level_of(entry)
        radical = hvs.radical_of(entry)
        if radical is None:
            no_radical.append(hanzi)
        upsert_hsk_vocab(
            db, hanzi=hanzi, hsk_level=level,
            pinyin=reading.replace(" ", ""),
            english=hvs.clean_meaning(form["meanings"][0]) if form.get("meanings") else "",
            frequency=entry.get("frequency"),
            pos=entry.get("pos") or [],
            word_type=word_type,
            radical=radical,
            pattern=entry.get("pattern"),
        )
        by_level[level] += 1
        by_type[word_type.value] += 1

    listed = set(entries)
    dropped = [h for (h,) in db.query(Vocab.hanzi).filter(Vocab.hsk_level.isnot(None)).all()
               if h not in listed]

    print(f"  vocab: {len(entries)} words loaded (2025 syllabus + overrides)")
    print("    by level: " + ", ".join(f"HSK {k}{'-9' if k == 7 else ''}: {v}"
                                       for k, v in sorted(by_level.items())))
    print("    by type:  " + ", ".join(f"{k}: {v}" for k, v in sorted(by_type.items())))
    for word, list_level, override in notes["overrides"]:
        was = f"2025 list has it at {list_level}" if list_level else "not in the 2025 list"
        print(f"    override: {word} -> HSK {override} ({was})"
              f"{'  [same level: the override does nothing]' if list_level == override else ''}")
    if notes["override_missing"]:
        print(f"    [warning] override words not in the vocab file and with no \"pinyin\" + \"meanings\" "
              f"of their own (ignored): {notes['override_missing']}")
    if notes["merged_readings"]:
        print(f"    readings with identical meanings, merged ({len(notes['merged_readings'])}): "
              + ", ".join(f"{w} {r}" for w, r in notes["merged_readings"][:20]))
    for pattern, pieces in notes["patterns"]:
        print(f"    pattern {pattern} -> pieces {' + '.join(pieces)} (each counts only with the others)")
    for pattern, piece in notes["pattern_conflicts"]:
        print(f"    [note] {piece} of {pattern} is also listed on its own: its own entry is kept")
    print(f"    meaning groups: synonyms merged for {len(notes['grouped'])} word(s) "
          f"(data/raw/{hvs.GROUPS_FILE})")
    if notes["groups_stale"]:
        print(f"    [check] {len(notes['groups_stale'])} word(s) have groups that no longer match their "
              f"dictionary meanings and stay ungrouped (re-run group_meanings, or fix the file): "
              f"{' '.join(notes['groups_stale'][:20])}")
    print(f"    radicals: {len(entries) - len(no_radical)} found, {len(no_radical)} missing"
          f"{f' (e.g. {no_radical[:10]})' if no_radical else ''}")
    if malformed:
        print(f"    [note] {len(malformed)} word(s) have a malformed numeric reading in the source "
              f"(accents or missing tone numbers); tagging falls back to all forms for these. "
              f"e.g. {malformed[:5]}")
    if dropped:
        print(f"    [warning] {len(dropped)} word(s) in the DB have a level but are no longer in the "
              f"list (kept): {dropped[:10]}")
    return {"words": len(entries), "malformed": len(malformed), "dropped": len(dropped),
            "no_radical": len(no_radical)}


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

    moves = grammar_dsl.level_moves()
    moved = []
    for tip in db.query(GrammarTip).filter(GrammarTip.grammar_code.in_(list(moves))).all():
        if tip.hsk_level != moves[tip.grammar_code]:
            moved.append(f"{tip.grammar_code} {tip.hsk_level}->{moves[tip.grammar_code]}")
            by_level[tip.hsk_level] -= 1
            by_level[moves[tip.grammar_code]] += 1
            tip.hsk_level = moves[tip.grammar_code]
    db.flush()

    dropped = [c for (c,) in db.query(GrammarTip.grammar_code).all() if c not in seen]
    print(f"  grammar: {len(seen)} points loaded")
    if moves:
        print(f"    levels moved ({len(moved)}: 2025 syllabus, then the points' own examples; data/grammar/): "
              + ", ".join(moved[:60]))
    elif not grammar_dsl.LEVELS_2025_FILE.exists():
        print(f"    [note] {grammar_dsl.LEVELS_2025_FILE} not found: the list's own levels are used")
    print("    by level: " + ", ".join(f"HSK {k}: {v}" for k, v in sorted(by_level.items())))
    if problems:
        print(f"    [warning] skipped {len(problems)} entr(y/ies) with no id/level or a duplicate id: {problems}")
    if dropped:
        print(f"    [warning] {len(dropped)} grammar point(s) in the DB are no longer in the list "
              f"(kept): {dropped}")
    return {"points": len(seen), "skipped": len(problems), "dropped": len(dropped), "moved": len(moved)}


EXPLANATION_FIELDS = ("source", "source_ref", "title", "structure", "explanation", "level_hint",
                      "url", "attribution", "license")


def load_explanations(db) -> dict:
    """GrammarExplanation rows from data/grammar/grammar_explanations.json
    ({code: [row, ...]} in display order). A point's rows are replaced."""
    data = grammar_dsl.read_json(grammar_dsl.EXPLANATIONS_FILE, None)
    if data is None:
        print(f"  explanations: {grammar_dsl.EXPLANATIONS_FILE} not found -- skipped "
              "(run test_scripts/run_all_grammar.py --export)")
        return {"points": 0, "rows": 0}
    tips = {t.grammar_code: t for t in db.query(GrammarTip).all()}
    unknown, rows, by_source, with_wiki = [], 0, Counter(), 0
    for code, items in data.items():
        if code.startswith("_"):
            continue
        tip = tips.get(code)
        if tip is None:
            unknown.append(code)
            continue
        db.query(GrammarExplanation).filter(GrammarExplanation.grammar_tip_id == tip.id).delete()
        for pos, it in enumerate(items):
            db.add(GrammarExplanation(grammar_tip_id=tip.id, position=pos,
                                      examples=json.dumps(it.get("examples") or [], ensure_ascii=False),
                                      **{k: it.get(k) for k in EXPLANATION_FIELDS}))
            by_source[it.get("source")] += 1
            rows += 1
        with_wiki += any(it.get("source") == "allset_learning" for it in items)
    db.flush()
    print(f"  explanations: {rows} row(s) for {len(data) - len(unknown) - ('_readme' in data)} point(s) "
          f"({', '.join(f'{k}: {v}' for k, v in sorted(by_source.items()))}); "
          f"{with_wiki} point(s) have a wiki explanation, the rest only the official list's")
    if unknown:
        print(f"    [warning] {len(unknown)} code(s) not in the grammar list (skipped): {unknown[:10]}")
    return {"points": len(data), "rows": rows}


def main():
    parser = argparse.ArgumentParser(description="Load the HSK vocabulary and grammar lists into the DB.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--vocab-only", action="store_true")
    group.add_argument("--grammar-only", action="store_true")
    args = parser.parse_args()

    t0 = time.time()
    init_db()
    print("Loading HSK reference lists (2025 syllabus vocab, grammar list)...")
    with get_session() as db:
        if not args.grammar_only:
            load_vocab(db)
        if not args.vocab_only:
            load_grammar(db)
            load_explanations(db)
    print(f"Done in {time.time() - t0:.1f}s.")


if __name__ == "__main__":
    main()