"""
data_pipelines/external_sources/hsk_sentences_audio/import_sentences.py

Imports supplementary sentences from the `hsk_sentences_audio` package into
textbook.db, placing each sentence in the unit of its OWN highest-unit word
-- not the unit ordering the external package uses (it doesn't have units,
only a per-card hsk_level).

No schema changes: this writes only through the existing Sentence/Vocab/
SentenceVocab/Question tables and columns, nothing new. Any sentence
metadata the external package offers that doesn't fit the current schema
(audio, topic, traditional characters, grammar_tags) is intentionally
dropped -- add columns later if/when that's actually wanted.

Why this lives OUTSIDE data_pipelines/textbook/: this is a fundamentally
different kind of ingestion (a structured Python package, not PDF+OCR+LLM
extraction), so it gets its own folder -- but it still writes to the SAME
textbook.db through the SAME app/textbook/db_utils.py / models.py the
textbook pipeline uses, so placement/tagging/idempotency rules don't drift
between the two ingestion paths.

ALGORITHM
---------
1. Find the highest hsk_level currently loaded in textbook.db
   (MAX(units.hsk_level)). Only external cards with hsk_level <= that are
   considered candidates at all -- this is a coarse relevance filter so a
   sentence the source itself calls "HSK6" doesn't get processed just
   because it happens to contain one simple, already-known word (see step 3
   for why that would otherwise be a problem).

2. For each candidate card, segment its `chinese` text against the UNION of
   (a) our own vocab hanzi and (b) the card's own `tokens` word boundaries.
   (b) matters because our vocab alone would greedy-match compound words we
   don't know yet down into individual, wrong-grained characters; the
   source's own token boundaries tell us where a not-yet-taught compound
   word (e.g. "高兴") actually starts and ends.

3. Sentence placement = the unit of the HIGHEST-unit word among tags that
   ARE already in our vocab index (compared as (hsk_level, unit_number)
   tuples, since unit_number alone restarts per level). Tags NOT yet in our
   vocab index are ignored for this step and don't block placement -- per
   spec: "if there is a word in the sentence that isn't in the vocab index,
   ignore it and take the next highest unit word." If NO tag is in our
   vocab index at all, there's nothing to anchor placement to, so the card
   is skipped.

4. Every tag that WASN'T already in our vocab index (including a
   previously "auto"/no-unit word) gets registered as a real vocab word
   (word_type="vocab", not "auto") at the SAME unit the sentence itself
   just got placed in -- per spec: "assign the word in the sentence that
   isn't registered yet to be in that unit." Pinyin/gloss for these come
   from the card's own `tokens` entry when the word matches one; otherwise
   pypinyin for pronunciation and an UNKNOWN_ENGLISH placeholder (same
   placeholder convention append_orphan_tags.py already repairs).

5. Each newly-registered vocab word immediately gets the same standard set
   of word-level questions create_questions.py generates for any vocab
   word (listening/speaking/translate/transcribe), using the exact same
   question_type strings so a later create_questions.py rerun's
   (unit, type, question, answer) upsert key lines up and doesn't
   duplicate. This is a simplified stand-in for create_questions.py's full
   build_vocab_style_questions (skips its "blocked" gating check for
   compound words with not-yet-taught sub-parts) -- the recommended
   create_questions.py rerun afterward reconciles this properly.

6. The sentence itself is written via db_utils.upsert_sentence() -- same
   idempotent upsert the textbook pipeline uses, keyed on (unit, hanzi).

WHAT'S EXCLUDED
----------------
- grammar_tags: not matched against GrammarTip rows (per instructions).
- audio, topic, traditional characters, sentence_type: not stored -- no
  columns for them, and none are being added right now.

AFTER RUNNING THIS SCRIPT
--------------------------
New sentences have tags and their new vocab words have basic questions, but
sentence-level questions (listening/speaking/translate SENTENCE, not word)
and grammar-tip links don't exist yet. Re-run, for each hsk_level touched:
    python data_pipelines/textbook/scripts/main.py --from-grammar --hsk-level <N>

USAGE
-----
    python import_sentences.py                  # HSK level 1..MAX, all topics
    python import_sentences.py --topic food      # only a specific topic
    python import_sentences.py --dry-run         # print what WOULD be written, no DB writes
"""
import argparse
import re
from collections import defaultdict

from hsk_sentences_audio import iter_sentences

from app.textbook.db_utils import (
    get_session, init_db, get_word_to_unit_map, get_word_to_pinyin_map,
    get_all_vocab_hanzi, upsert_vocab, upsert_sentence, upsert_question,
)
from app.textbook.models import Unit, WordType
from data_pipelines.textbook.scripts.vocab_pinyin_utils import (
    diacritic_to_numeric, pypinyin_numeric, cross_check_pinyin,
)
from data_pipelines.textbook.scripts.cedict_utils import lookup_word, segment_into_words

SOURCE_LABEL = "hsk_sentences_audio"

# Must match create_questions.py's QuestionType enum values exactly, so a
# later create_questions.py rerun's upsert_question() dedup key
# (unit, question_type, question, answer) lines up with what we write here
# instead of creating duplicates.
VOCAB_QUESTION_TYPES = [
    "listening vocab",
    "speaking vocab",
    "translate english word to chinese",
    "translate chinese word to english",
    "transcribe word to pinyin",
    "transcribe hanzi to pinyin",
]

_CONTENT_RE = re.compile(r"[\u4e00-\u9fff]|\d+")


def content_only(s: str) -> str:
    """Strip everything except hanzi/digits so punctuation doesn't break
    segmentation -- same filter sentence_parser.py uses."""
    return "".join(_CONTENT_RE.findall(s or ""))


def greedy_segment(content: str, allowed_words: list[str]) -> list[str]:
    """Longest-match-first segmentation against `allowed_words` (our own
    vocab + the external card's own token hints, sorted longest-first).

    Any run of characters that doesn't match anything in `allowed_words`
    gets handed to segment_into_words() (CEDICT-validated jieba) instead of
    falling back to single characters -- that's exactly the failure mode
    that let a three-word phrase like "太热了" get registered as if it were
    one single vocab entry: nothing in our vocab or the card's tokens
    matched it as a whole, so it fell back to unknown single characters
    (or, worse, got treated as one unmatched multi-char run and registered
    whole)."""
    tags, pos = [], 0
    n = len(content)
    unmatched_run: list[str] = []

    def flush_unmatched():
        if not unmatched_run:
            return
        run_text = "".join(unmatched_run)
        unmatched_run.clear()
        tags.extend(w for w in segment_into_words(run_text) if w)

    while pos < n:
        match = next((w for w in allowed_words if content.startswith(w, pos)), None)
        if match:
            flush_unmatched()
            tags.append(match)
            pos += len(match)
        else:
            unmatched_run.append(content[pos])
            pos += 1
    flush_unmatched()
    return tags


def resolve_new_word_pinyin(tag: str, tok: dict | None) -> str:
    """This only runs for words CEDICT doesn't know (see process_card,
    which checks CEDICT first) -- so there's no authoritative compound
    pronunciation available, just the external card's own token pinyin
    (if any) and pypinyin as a last resort.

    TRUST THE TOKEN SOURCE over pypinyin when both are available and they
    disagree. pypinyin computes pinyin character-by-character with no
    knowledge of compounds, so it's frequently wrong for real multi-char
    words (e.g. 学生 -> pypinyin would give the base reading for 生,
    sheng1, when the word actually takes sheng5) -- CEDICT already
    corrects for this when it has the word, but for a word CEDICT
    doesn't have, the source's own annotated pinyin is still a better bet
    than a character-by-character guess. Still logs a mismatch either way,
    since it COULD also be the tone-mark-on-wrong-syllable bug from before
    -- just doesn't silently overwrite with pypinyin anymore."""
    py_pinyin = pypinyin_numeric(tag)  # "" if pypinyin isn't installed

    if not (tok and tok.get("pinyin")):
        return py_pinyin or "UNKNOWN_PINYIN"

    token_pinyin = diacritic_to_numeric(tok["pinyin"])
    if not py_pinyin:
        return token_pinyin  # can't cross-check, best we've got

    if token_pinyin != py_pinyin:
        print(f"  [pinyin-note] '{tag}': source says '{token_pinyin}', pypinyin says "
              f"'{py_pinyin}' -- using source (pypinyin isn't compound-aware; "
              f"if this looks wrong, check manually -- see resolve_new_word_pinyin docstring)")

    return token_pinyin


def resolve_target(tags: list[str], word_to_unit: dict):
    """Among tags already in our vocab index, pick the one with the highest
    (hsk_level, unit_number). Returns (unit_number, hsk_level) or (None, None)
    if nothing in the sentence is registered yet."""
    registered = [(t, word_to_unit[t]) for t in tags if t in word_to_unit]
    if not registered:
        return None, None
    # word_to_unit values are (unit_number, hsk_level) -- sort by hsk_level first
    _, (unit_number, hsk_level) = max(registered, key=lambda item: (item[1][1], item[1][0]))
    return unit_number, hsk_level


def generate_vocab_questions(db, vocab_row, hanzi: str, pinyin: str, english: str,
                              unit_number: int, hsk_level: int):
    """Simplified stand-in for create_questions.py's build_vocab_style_questions
    for exactly this one newly-registered word -- see module docstring, step 5."""
    candidates = {
        "listening vocab": (hanzi, pinyin),
        "speaking vocab": (hanzi, pinyin),
        "translate english word to chinese": (english, hanzi),
        "translate chinese word to english": (hanzi, english),
        "transcribe word to pinyin": (hanzi, pinyin),
        "transcribe hanzi to pinyin": (hanzi, pinyin),
    }
    for qtype in VOCAB_QUESTION_TYPES:
        q_text, a_text = candidates[qtype]
        upsert_question(db, unit_number, qtype, q_text, a_text, vocab_id=vocab_row.id, hsk_level=hsk_level)


def process_card(db, card: dict, known_hanzi: set, word_to_unit: dict, word_to_pinyin: dict,
                  dry_run: bool) -> dict:
    hanzi = card.get("chinese", "")
    if not hanzi:
        return {"status": "skipped", "reason": "no chinese text"}

    token_info = {
        tok["word"]: tok for tok in (card.get("tokens") or []) if tok.get("word")
    }
    combined_words = sorted(known_hanzi | set(token_info.keys()), key=len, reverse=True)
    tags = greedy_segment(content_only(hanzi), combined_words)

    target_unit, target_hsk_level = resolve_target(tags, word_to_unit)
    if target_unit is None:
        return {"status": "skipped", "reason": "no word in this sentence is in the vocab index yet",
                "hanzi": hanzi}

    unregistered = [t for t in tags if t not in word_to_unit]
    new_words = []

    if dry_run:
        return {
            "status": "would_write",
            "hanzi": hanzi,
            "target_unit": target_unit,
            "hsk_level": target_hsk_level,
            "tags": tags,
            "new_words": sorted(set(unregistered)),
        }

    for tag in dict.fromkeys(unregistered):  # de-dup, preserve first-seen order
        cedict_entry = lookup_word(tag)
        if cedict_entry:
            # CEDICT is authoritative for BOTH pinyin (correct compound
            # tones, e.g. 学生 -> xue2sheng5) and definition -- prefer it
            # over the external card's token info or pypinyin whenever
            # it's available.
            pinyin, english = cedict_entry["pinyin"], cedict_entry["english"]
        else:
            tok = token_info.get(tag)
            pinyin = resolve_new_word_pinyin(tag, tok)
            english = (tok.get("gloss_en") if tok else "") or "UNKNOWN_ENGLISH"

        vocab_row = upsert_vocab(
            db, hanzi=tag, pinyin=pinyin, english=english,
            unit_number=target_unit, word_type=WordType.vocab, hsk_level=target_hsk_level,
        )
        # keep local maps in sync so this card's own tag_pinyins lookup below
        # (and the next card's segmentation) sees this word as known
        word_to_unit[tag] = (target_unit, target_hsk_level)
        word_to_pinyin[tag] = pinyin
        known_hanzi.add(tag)
        new_words.append((vocab_row, tag, pinyin, english))

    for vocab_row, tag, pinyin, english in new_words:
        generate_vocab_questions(db, vocab_row, tag, pinyin, english, target_unit, target_hsk_level)

    tag_pinyins = [word_to_pinyin.get(t, "UNKNOWN_PINYIN") for t in tags]
    english_full = (card.get("translation") or {}).get("en", "")
    # card["pinyin_numbered"] is already numeric per the source's own field
    # name/format, so diacritic_to_numeric() is normally a no-op here (its
    # digit short-circuit fires immediately) -- this call is just a safety
    # net in case that assumption is ever wrong for some card.
    pinyin_full = diacritic_to_numeric(card.get("pinyin_numbered") or "")

    upsert_sentence(
        db,
        unit_number=target_unit,
        hsk_level=target_hsk_level,
        hanzi=hanzi,
        english=english_full,
        pinyin=pinyin_full,
        tags=tags,
        tag_pinyins=tag_pinyins,
        source=SOURCE_LABEL,
    )
    return {
        "status": "written", "hanzi": hanzi, "target_unit": target_unit,
        "hsk_level": target_hsk_level, "new_words": [t for _, t, _, _ in new_words],
    }


def main():
    parser = argparse.ArgumentParser(description="Import hsk_sentences_audio sentences into textbook.db")
    parser.add_argument("--topic", type=str, default=None,
                         help="Only import cards matching this topic (e.g. 'food').")
    parser.add_argument("--dry-run", action="store_true",
                         help="Print what would be written without touching the DB.")
    args = parser.parse_args()

    init_db()
    with get_session() as db:
        from sqlalchemy import func
        max_hsk_level = db.query(func.max(Unit.hsk_level)).scalar()
        if max_hsk_level is None:
            print("❌ No units found in textbook.db -- run the textbook pipeline first.")
            return
        print(f"Highest HSK level currently loaded: {max_hsk_level}")

        word_to_unit = get_word_to_unit_map(db)      # {hanzi: (unit_number, hsk_level)}
        word_to_pinyin = get_word_to_pinyin_map(db)   # {hanzi: pinyin}
        known_hanzi = set(get_all_vocab_hanzi(db))    # every Vocab row, incl. word_type="auto"

        results = defaultdict(list)
        total_considered = 0

        for level in range(1, max_hsk_level + 1):
            kwargs = {"level": level}
            if args.topic:
                kwargs["topic"] = args.topic
            for card in iter_sentences(**kwargs):
                total_considered += 1
                result = process_card(db, card, known_hanzi, word_to_unit, word_to_pinyin, args.dry_run)
                results[result["status"]].append(result)

        written = results.get("written", [])
        would_write = results.get("would_write", [])
        skipped = results.get("skipped", [])

        print(f"\nConsidered {total_considered} card(s) at HSK level <= {max_hsk_level}.")
        if args.dry_run:
            print(f"Would write: {len(would_write)}")
            for r in would_write[:20]:
                new_words_note = f" new_words={r['new_words']}" if r["new_words"] else ""
                print(f"  [HSK{r['hsk_level']} unit {r['target_unit']}] {r['hanzi']}  "
                      f"tags={r['tags']}{new_words_note}")
            if len(would_write) > 20:
                print(f"  ... and {len(would_write) - 20} more")
        else:
            new_word_count = sum(len(r["new_words"]) for r in written)
            print(f"Written: {len(written)} sentence(s), {new_word_count} new vocab word(s) registered")

        if skipped:
            print(f"Skipped: {len(skipped)}")
            for r in skipped[:20]:
                print(f"  - {r.get('hanzi', '?')}: {r['reason']}")
            if len(skipped) > 20:
                print(f"  ... and {len(skipped) - 20} more")

    if not args.dry_run and written:
        levels_touched = sorted({r["hsk_level"] for r in written})
        print(
            "\n⚠️  Reminder: sentence-level questions and grammar-tip links "
            "still need the full pipeline stage. For each HSK level just touched, run:"
        )
        for lv in levels_touched:
            print(f"    python data_pipelines/textbook/scripts/main.py --from-grammar --hsk-level {lv}")


if __name__ == "__main__":
    main()